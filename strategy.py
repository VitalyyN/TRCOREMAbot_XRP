import time
from datetime import datetime
import settings as cfg
import pandas as pd
from trader import (
    fetch_klines,
    compute_ema,
    place_limit_best,
    latest_price,
    close_position,
    get_avg_entry_price,
    get_position,
    calc_order_qty
)
from settings import ONLY_LONG

class TradingBot:
    def __init__(self, tg_bot, chat_id, markup, logger):
        self.tg_bot = tg_bot
        self.chat_id = chat_id
        self.markup = markup
        self.logger = logger
        self.last_bar_time = None
        self.in_position = False
        self.position_side = ""
        self.base_price = 0.0
        self.dca_index = 0
        self.last_trend = None
        self.limit_order_plased = False  # Флаг: размещен ли лимитный ордер
        self.breakeven_set = False  # Флаг: выставлялся ли уже тейк на безубыток
        self.is_message_dca = False  # Флаг: выводилось ли сообщение о следующем уровне добавке
        self.is_message_TP = False  # Флаг: выводилось ли сообщение о выставленном TP
        self.is_message_trend_change = False  # Флаг: выводилось ли сообщение о смене тренда
        self.is_stoped = True  # # Флаг: был ли трейдинг остановлен

    def update_candles(self) -> pd.DataFrame:
        """
        Загружаем свечи и считаем EMA.
        """
        df = fetch_klines(cfg.SYMBOL, limit=max(cfg.EMA_FAST, cfg.EMA_SLOW) + 10)
        df = compute_ema(df, cfg.EMA_FAST, cfg.EMA_SLOW)
        return df

    def check_new_candle(self, df: pd.DataFrame) -> bool:
        """
        Определяем, закрылась ли новая свеча.
        """
        latest_time = df.index[-1]
        if self.last_bar_time is None or latest_time > self.last_bar_time:
            self.last_bar_time = latest_time
            return True
        return False

    def determine_trend(self, candle) -> str:
        """
        Определяет текущий тренд по EMA.
        """
        if candle["ema_fast"] > candle["ema_slow"]:
            return "long"
        elif candle["ema_fast"] < candle["ema_slow"]:
            return "short"
        else:
            return "flat"

    def check_entry(self, df: pd.DataFrame):
        """
        Вход в сделку по завершению свечи.
        """
        candle = df.iloc[-2]
        prev_candle = df.iloc[-3]
        trend = self.determine_trend(candle)
        self.last_trend = trend  # обновим текущий тренд

        if self.in_position:
            return

        # print(trend, candle["close"], candle["ema_fast"], prev_candle["close"], prev_candle["ema_fast"])
        # Лонг при коррекции к EMA
        if trend == "long" and candle["close"] < candle["ema_fast"] and prev_candle["close"] > prev_candle["ema_fast"]:
            qty = calc_order_qty(cfg.SYMBOL, cfg.POSITION_SIZE)
            self.tg_bot.send_message(self.chat_id, f"{datetime.now().strftime('%H:%M:%S %d-%m-%Y')} [ENTRY] LONG signal. Size {qty}", reply_markup=self.markup)
            self.logger.info(f" [ENTRY] LONG signal. Size {qty}")
            if place_limit_best("Buy", qty, cfg.SYMBOL):
                self.limit_order_plased = True            
            self.in_position = True
            self.position_side = "Buy"
            self.dca_index = 0
            self.base_price = candle["close"]
            self.breakeven_set = False
            self.is_message_dca = False
            self.is_message_TP = False

        # Шорт при коррекции к EMA
        elif not ONLY_LONG and trend == "short" and candle["close"] > candle["ema_fast"] and prev_candle["close"] < prev_candle["ema_fast"]:
            qty = calc_order_qty(cfg.SYMBOL, cfg.POSITION_SIZE)
            self.tg_bot.send_message(self.chat_id, f"{datetime.now().strftime('%H:%M:%S %d-%m-%Y')} [ENTRY] SHORT signal. Size {qty}", reply_markup=self.markup)
            self.logger.info(f"[ENTRY] SHORT signal. Size {qty}")
            if place_limit_best("Sell", qty, cfg.SYMBOL):
                self.limit_order_plased = True
            self.in_position = True
            self.position_side = "Sell"
            self.dca_index = 0
            self.base_price = candle["close"]
            self.breakeven_set = False
            self.is_message_dca = False
            self.is_message_TP = False

    def check_exit(self):
        """
        Закрытие позиции по тейк-профиту или в безубыток при смене тренда.
        """
        if not self.in_position:
            return

        current_price = latest_price(cfg.SYMBOL)
        avg_price = get_avg_entry_price(cfg.SYMBOL)
        size, side = get_position(cfg.SYMBOL)

        if size == 0:
            if not self.limit_order_plased:
                self.reset_position()
                return
        else:
            self.limit_order_plased = False

        # Расчёт цели тейк-профита
        target = avg_price * (1 + cfg.TAKE_PROFIT) if side == "Buy" else \
                 avg_price * (1 - cfg.TAKE_PROFIT)
        
        if not self.is_message_TP:
            self.tg_bot.send_message(self.chat_id, f"Take proffit entered by {target}", reply_markup=self.markup)
            self.is_message_TP = True

        should_tp = current_price >= target if side == "Buy" else current_price <= target

        if should_tp:
            self.tg_bot.send_message(self.chat_id, f"{datetime.now().strftime('%H:%M:%S %d-%m-%Y')} [TP Exit] Closing {side} at {current_price} (avg: {avg_price})", reply_markup=self.markup)
            self.logger.info(f"[TP Exit] Closing {side} at {current_price} (avg: {avg_price})")
            close_position(cfg.SYMBOL)
            self.reset_position()

        # Проверка на смену тренда и установка безубытка
        if self.position_side == "Buy":
            if self.last_trend == "short":
                exit_price = avg_price + avg_price * cfg.COMMISSION_RATE
                if not self.is_message_trend_change:
                    #print(f'Смена тренда. Цена выхода {exit_price}')
                    self.is_message_trend_change = True
                if current_price >= exit_price:
                    self.tg_bot.send_message(self.chat_id, f"{datetime.now().strftime('%H:%M:%S %d-%m-%Y')} [TP Not Loss] Closing {side} at {current_price} (avg: {avg_price})", reply_markup=self.markup)
                    self.logger.info(f"[TP Not Loss] Closing {side} at {current_price} (avg: {avg_price})")
                    close_position(cfg.SYMBOL)
                    self.reset_position()

        if self.position_side == "Sell":
            if self.last_trend == "long":
                exit_price = avg_price - avg_price * cfg.COMMISSION_RATE
                if not self.is_message_trend_change:
                    # print(f'Смена тренда. Цена выхода {exit_price}')
                    self.is_message_trend_change = True
                if current_price <= exit_price:
                    self.tg_bot.send_message(self.chat_id, f"{datetime.now().strftime('%H:%M:%S %d-%m-%Y')} [TP Not Loss] Closing {side} at {current_price} (avg: {avg_price})", reply_markup=self.markup)
                    close_position(cfg.SYMBOL)
                    self.reset_position()

    def check_dca(self):
        """
        Усреднение: увеличиваем позицию по сетке, шаги растут в 2 раза.
        """
        if not self.in_position or self.dca_index >= len(cfg.DCA_GRID):
            return

        current_price = latest_price(cfg.SYMBOL)
        side = self.position_side

        # Рассчитываем полное расстояние от базовой цены для текущего уровня
        total_distance = 0
        step_size = cfg.DCA_STEP
    
        for i in range(self.dca_index + 1):
                total_distance += step_size
                step_size *= 2

        # Расчёт триггерной цены от базовой цены
        trigger_price = self.base_price - total_distance if side == "Buy" else \
                    self.base_price + total_distance
        
        if not self.is_message_dca:
            # print(f'Price to Add: {trigger_price}, DCA level: {self.dca_index + 1}')
            self.is_message_dca = True

        should_add = current_price <= trigger_price if side == "Buy" else current_price >= trigger_price

        if should_add:
            factor = cfg.DCA_GRID[self.dca_index]
            qty = calc_order_qty(cfg.SYMBOL, cfg.POSITION_SIZE)
            qty = qty * factor
            self.tg_bot.send_message(self.chat_id, f"{datetime.now().strftime('%H:%M:%S %d-%m-%Y')} [DCA level] Add {side} x{factor} at {current_price}", reply_markup=self.markup)
            self.logger.info(f"[DCA level] Add {side} x{factor} at {current_price})")
            place_limit_best(side, qty, cfg.SYMBOL)
            self.dca_index += 1
            self.is_message_dca = False

    def reset_position(self):
        """
        Обнуляем данные по позиции.
        """
        self.in_position = False
        self.position_side = ""
        self.base_price = 0.0
        self.dca_index = 0
        self.limit_order_plased = False
        self.breakeven_set = False
        self.is_message_trend_change = False
        # print('============================================\n')

    def run(self, traiding_flag):
        """
        Главный цикл: следим за свечами, сигналами и позициями.
        """
        if traiding_flag:
            self.is_stoped = False
            try:
                df = self.update_candles()

                if self.check_new_candle(df):
                    self.check_entry(df)

                self.check_exit()
                self.check_dca()

            except Exception as e:
                self.tg_bot.send_message(self.chat_id, f"[ERROR] {e}", reply_markup=self.markup)
                self.logger.info(e)

        if not self.is_stoped and not traiding_flag:
            close_position(cfg.SYMBOL)
            self.tg_bot.send_message(self.chat_id, "[STOP TRAIDING] Торговля остановлена. Все позиции закрыты", reply_markup=self.markup)
            self.is_stoped = True

        time.sleep(3)  # Пауза между итерациями            

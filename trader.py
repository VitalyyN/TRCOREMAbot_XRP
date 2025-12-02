import os
from typing import List, Literal, Tuple
import math
from dotenv import load_dotenv
import pandas as pd
from ta.trend import EMAIndicator

from pybit.unified_trading import HTTP
from pybit.exceptions import InvalidRequestError

import settings as cfg


load_dotenv()

SYMBOL_SPECS = {}


# --- Сессия ByBit --- #
def create_session() -> HTTP:
    """Создаёт сессию PyBit с учётом переменной TESTNET=1|0."""
    return HTTP(
        testnet=False,
        api_key=os.getenv("BYBIT_API_KEY"),
        api_secret=os.getenv("BYBIT_API_SECRET"),
        demo=cfg.DEMO
    )

session = create_session()


# --- Метаданные инструмента --- #
def get_symbol_specs(symbol: str):
    """
    Получить параметры инструмента:
    - минимальный размер позиции
    - шаг цены
    - шаг объема

    Сохраняется в глобальную переменную SYMBOL_SPECS
    """
    global SYMBOL_SPECS
    resp = session.get_instruments_info(category="linear", symbol=symbol)
    info = resp["result"]["list"][0]

    min_qty = float(info["lotSizeFilter"]["minOrderQty"])
    qty_step = float(info["lotSizeFilter"]["qtyStep"])
    tick_size = float(info["priceFilter"]["tickSize"])

    SYMBOL_SPECS[symbol] = {
        "min_qty": min_qty,
        "qty_step": qty_step,
        "tick_size": tick_size,
    }


# --- Баланс --- #
def get_balance() -> float:
    """Текущее equity аккаунта USDT."""
    data = session.get_wallet_balance(accountType="UNIFIED", coin='USDT')
    #return float(data["result"]["list"][0]["equity"])
    return float(data["result"]["list"][0]["coin"][0]["equity"])


# --- Последняя цена --- #
def latest_price(symbol: str) -> float:
    """Последняя цена сделки."""
    data = session.get_tickers(symbol=symbol, category="linear")
    return float(data["result"]["list"][0]["lastPrice"])


# --- Закрытие последней свечи --- #
def last_candle_close(
    symbol: str,
    interval: Literal["1", "3", "5", "15", "30", "60", "240", "D", "W", "M"] = cfg.INTERVAL
) -> float:
    """
    Цена закрытия последней завершённой свечи.

    • interval – тайм-фрейм в минутах
      (Bybit API: "1", "3", "5", "15", "30", "60", "240", "D", "W", "M")
    """
    klines = session.get_kline(
        category="linear",
        symbol=symbol,
        interval=interval,
        limit=1,          # запрашиваем только последнюю свечу
    )
    return float(klines["result"]["list"][0][4])   # индекc 4 – close


# --- Расчет EMA --- #
def compute_ema(df: pd.DataFrame, period_fast: int, period_slow: int) -> pd.DataFrame:
    """
    Добавляет в DataFrame две колонки с EMA для закрытия: EMA_FAST и EMA_SLOW.

    Args:
        df: DataFrame с колонкой 'close'.
        period_fast: Период для быстрой EMA.
        period_slow: Период для медленной EMA.

    Returns:
        DataFrame с добавленными колонками 'ema_fast' и 'ema_slow'.
    """
    df = df.copy()

    ema_fast = EMAIndicator(close=df['close'], window=period_fast).ema_indicator()
    ema_slow = EMAIndicator(close=df['close'], window=period_slow).ema_indicator()
    df['ema_fast'] = ema_fast
    df['ema_slow'] = ema_slow
    return df


# --- История свечей --- #
def fetch_klines(
    symbol: str,
    limit: int,
    interval: Literal["1", "3", "5", "15", "30", "60", "240", "D", "W", "M"] = "1",
    as_df: bool = True,
) -> List[List] | pd.DataFrame:
    """
    Получить последние *limit* свечей.

    Возврат:
      • raw-список klines (если as_df = False)
      • или pandas.DataFrame c колонками:
        ['open_time','open','high','low','close','volume','turnover']

    Примечание: для EMA достаточно колонки 'close'.
    """
    raw = session.get_kline(
        category="linear",
        symbol=symbol,
        interval=interval,
        limit=limit,
    )["result"]["list"]

    if not as_df:
        return raw

    # Преобразуем в DataFrame с удобными названиями
    cols = [
        "open_time",  # ms timestamp
        "open",
        "high",
        "low",
        "close",
        "volume",
        "turnover"
    ]
    df = pd.DataFrame(raw, columns=cols).astype(
        {
            "open": float,
            "high": float,
            "low": float,
            "close": float,
            "volume": float,
            "turnover": float
        }
    )
    # конвертация времени (мс) → datetime
    df["open_time"] = pd.to_datetime(pd.to_numeric(df["open_time"]), unit="ms")
    df.set_index("open_time", inplace=True)
    return df.sort_index()   # гарантируем по возрастанию


# --- Рыночный ордер --- #
def place_market(side: str, qty: float, symbol: str):
    """Отправить маркет‑ордер. side = "Buy" | "Sell"""
    session.place_order(
        category   = "linear",
        symbol     = symbol,
        side       = side,
        orderType  = "Market",
        qty        = qty,
        reduceOnly = False,
    )


# --- Верх стакана --- #
def best_bid_ask(symbol: str) -> Tuple[float, float]:
    """
    Верх стакана (1 уровень).  →  (best_bid, best_ask)
    """
    ob = session.get_orderbook(category="linear", symbol=symbol, limit=1)
    best_bid = float(ob["result"]["b"][0][0])
    best_ask = float(ob["result"]["a"][0][0])
    return best_bid, best_ask


# --- Лимитный ордер по лучшей цене ± тик --- #
def place_limit_best(side: str, qty: float, symbol: str):
    """
    Лимитный ордер по лучшей цене ± тик.

    Цена округляется до допустимого tickSize.
    """
    if symbol not in SYMBOL_SPECS:
        get_symbol_specs(symbol)

    specs = SYMBOL_SPECS[symbol]
    tick = specs["tick_size"]

    best_bid, best_ask = best_bid_ask(symbol)

    raw_price = best_bid + tick if side == "Buy" else best_ask - tick
    steps = round(raw_price / tick)
    final_price = steps * tick

    try:
        resp = session.place_order(
                    category="linear",
                    symbol=symbol,
                    side=side.capitalize(),
                    orderType="Limit",
                    qty=qty,
                    price=str(final_price),
                    reduceOnly=False,
                )
        
        if resp["retMsg"] == "OK":
            return True
        else:
            return False
        
    except InvalidRequestError as e:
        raise RuntimeError(f"Ошибка лимитного ордера: {e}")
    

# --- Получение информации о позиции --- #
def get_position(symbol: str) -> Tuple[float, str]:
    """
    Возвращает: (кол-во позиции, side)
    Если позиции нет — (0, "")
    """
    data = session.get_positions(category="linear", symbol=symbol)
    pos = data["result"]["list"][0]
    size = float(pos["size"])
    side = pos["side"]
    return size, side if size > 0 else ""


# --- Получение средней цены позиции --- #
def get_avg_entry_price(symbol: str) -> float:
    """Средняя цена входа в позицию"""
    data = session.get_positions(category="linear", symbol=symbol)
    return float(data["result"]["list"][0]["avgPrice"])


# --- Закрытие всей позиции --- #
def close_position(symbol: str):
    """Принудительно закрыть позицию (маркет)"""
    size, side = get_position(symbol)
    if size > 0:
        opposite = "Sell" if side == "Buy" else "Buy"
        place_market(opposite, size, symbol)


# --- Нереализованный PnL позиции --- #
def get_position_pnl(symbol: str) -> float:
    """Нереализованный PnL позиции"""
    data = session.get_positions(category="linear", symbol=symbol)
    return data["result"]["list"][0]["unrealisedPnl"]


# --- Расчет правильного объема заявки исходя из размера в деньгах --- #
def calc_order_qty(symbol: str, portion: float) -> float:
    """
    Рассчитывает объём позиции (qty), кратный шагу объема и >= min_qty.
    """
    if symbol not in SYMBOL_SPECS:
        get_symbol_specs(symbol)

    specs = SYMBOL_SPECS[symbol]
    min_qty = specs["min_qty"]
    qty_step = specs["qty_step"]

    balance = get_balance()
    price = latest_price(symbol)
    raw_qty = balance * portion / price

    # округляем вниз до кратного qty_step
    steps = math.floor(raw_qty / qty_step)
    final_qty = steps * qty_step

    if final_qty < min_qty:
        raise ValueError(f"Объем ({final_qty}) меньше минимального ({min_qty})")

    return round(final_qty, 6)

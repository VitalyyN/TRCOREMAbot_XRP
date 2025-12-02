
from datetime import datetime
import threading
from strategy import TradingBot
from trader import get_symbol_specs, close_position
import settings as cfg
from logger import setup_logging
import os
import telebot
from telebot import types
from requests.exceptions import ReadTimeout, ConnectionError
import time

from trader import get_balance, get_position_pnl


chat_id = os.getenv("TELEGRAM_CHAT_ID")
traiding_start = False
bot = telebot.TeleBot(os.getenv("TELEGRAM_TOKEN"))

markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
btn1 = types.KeyboardButton("Start_trading")
btn2 = types.KeyboardButton('Stop_trading')
btn3 = types.KeyboardButton('Balance')
btn4 = types.KeyboardButton('PnL')
markup.add(btn1, btn2, btn3, btn4)

@bot.message_handler(commands=['start'])
def start(message):
    global chat_id, markup
    bot.send_message(chat_id, "Выберите действие", reply_markup=markup)
    

@bot.message_handler(content_types=['text'])
def get_text_messages(message):
    global chat_id, markup
    if message.text == "Start_trading":
        bot.send_message(chat_id, f"{datetime.now().strftime('%H:%M:%S %d-%m-%Y')} [BOT ACTIVE] Awaiting signals...", reply_markup=markup)
        start_traiding()
    elif message.text == "Stop_trading":
        stop_traiding()
        bot.send_message(chat_id, message.text, reply_markup=markup)
    elif message.text == "Balance":
        print_balance()
    elif message.text == "PnL":
        print_pnl()


def start_traiding():
    global traiding_start
    traiding_start = True


def stop_traiding():
    global traiding_start
    traiding_start = False


def print_balance():
    global chat_id
    try:
        balance = get_balance()
        bot.send_message(chat_id, f"Текущий баланс: {balance:.2f} USDT")
    except Exception as e:
        bot.send_message(chat_id, f"Ошибка получения баланса: {str(e)}")


def print_pnl():
    global chat_id
    try:
        pnl = get_position_pnl(cfg.SYMBOL)
        bot.send_message(chat_id, f"PnL: {pnl}")
    except Exception as e:
        bot.send_message(chat_id, f"Ошибка получения PnL: {str(e)}")


def telegram_polling(logger):
    """Функция для безопасного запуска polling с обработкой ошибок"""
    while True:
        try:
            # Увеличиваем таймауты для более стабильной работы
            bot.infinity_polling(
                timeout=60, 
                long_polling_timeout=60
            )
        except (ReadTimeout, ConnectionError) as e:
            logger.warning(f"Telegram polling timeout/connection error: {e}")
            time.sleep(5)  # Пауза перед повторной попыткой
        except Exception as e:
            logger.error(f"Unexpected error in telegram polling: {e}")
            time.sleep(10)  # Большая пауза при неожиданных ошибках


def main():
    logger = setup_logging()
    logger.info("Запуск торгового бота...")

    # Загрузить спецификацию символа: tickSize, minQty и т.п.
    get_symbol_specs(cfg.SYMBOL)

    # Запуск Telegram бота в отдельном потоке
    telegram_thread = threading.Thread(target=telegram_polling, args=(logger,), daemon=True)
    telegram_thread.start()
    # Создаём и запускаем стратегию
    traiding_bot = TradingBot(tg_bot=bot, chat_id=chat_id, markup=markup, logger=logger)

    while True:
        try:
            # print(traiding_start)
            traiding_bot.run(traiding_start)  # Основной цикл робота
        except KeyboardInterrupt:
            logger.info("Бот остановлен пользователем. Позиции закрыты")
            try:
                close_position(cfg.SYMBOL)
            except Exception as e:
                logger.error(f"Ошибка закрытия позиции: {e}")
            break
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            break

if __name__ == "__main__":
    main()

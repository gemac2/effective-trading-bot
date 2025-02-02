import logging
from binance.client import Client
import time
from ta.momentum import RSIIndicator
import pandas as pd
import requests
import os
from dotenv import load_dotenv

# Constants
ideal_volume = 50000000
class RSISignalBot:
    def __init__(self):
        load_dotenv()
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.client = Client('your_api_key', 'your_api_secret', tld='com')
        self.last_signal_time = {}
        self.timeframe_wait_times = {
            "1 Minute": 60,
            "5 Minutes": 300,
            "15 Minutes": 900,
            "1 Hour": 3600,
            "4 Hours": 14400
        }
        self.tickers_list = []

        logging.basicConfig(level=logging.INFO, 
                            format='%(asctime)s - %(levelname)s - %(message)s')

    def search_ticks(self):
        try:
            ticks = self.client.futures_symbol_ticker()
        except Exception as e:
            logging.error(f"Error while getting ticks: {e}")
            return []

        valid_ticks = []
        for tick in ticks:
            if tick['symbol'][-4:] != 'USDT' or tick['symbol'] in ["USDCUSDT", "BTCUSDT"]:
                continue
            valid_ticks.append(tick['symbol'])

        logging.info(f"Number of currencies found in the USDT Pair: {len(valid_ticks)}")
        return valid_ticks

    def get_klines(self, tick, interval):
        try:
            return self.client.futures_klines(symbol=tick, interval=interval, limit=48, timeout=30)
        except Exception as e:
            logging.error(f"Error while getting data for {tick} klines: {e}")
            return None

    def get_rsi_signal(self, tick, klines, timeframe):
        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df['close'] = df['close'].astype(float)

        rsi = RSIIndicator(df['close']).rsi().iloc[-1]
        if rsi == 100:
            return False
        
        current_price = df['close'].iloc[-1]
        
        if tick not in self.last_signal_time or (time.time() - self.last_signal_time[tick]) > self.timeframe_wait_times.get(timeframe, 0):
            if rsi < 20:
                self.send_telegram_message("RSI Below 20", timeframe, "Possible Long", tick, rsi, current_price)
                self.last_signal_time[tick] = time.time()
                return True
            elif rsi > 80:
                self.send_telegram_message("RSI Above 80", timeframe, "Possible Short", tick, rsi, current_price)
                self.last_signal_time[tick] = time.time()
                return True
        return False

    def send_telegram_message(self, title, timeframe, signal_type, currency_name, rsi, current_price):
        url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
        message = (f"**{title}**\n\n"
                   f"⌛️ TimeFrame: {timeframe}\n\n"
                   f"🛍️ Signal: {signal_type}\n\n"
                   f"🪙 Pair: {currency_name}\n\n"
                   f"📊 RSI: {rsi:.2f}\n\n"
                   f"💰 Current Price: {current_price:.4f}")
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }
        try:
            requests.post(url, json=payload)
        except Exception as e:
            logging.error(f"Error while sending message to Telegram: {e}")

    def run(self):
        self.tickers_list = self.search_ticks()
        logging.info("Starting RSI Signal Bot")
        while True:
            logging.info("Scanning Currencies...")
            for tick in self.tickers_list:
                for interval, timeframe in [(Client.KLINE_INTERVAL_1MINUTE, "1 Minute"),
                                             (Client.KLINE_INTERVAL_5MINUTE, "5 Minutes"),
                                             (Client.KLINE_INTERVAL_15MINUTE, "15 Minutes"),
                                             (Client.KLINE_INTERVAL_1HOUR, "1 Hour"),
                                             (Client.KLINE_INTERVAL_4HOUR, "4 Hours")]:
                    klines = self.get_klines(tick, interval)
                    if klines is not None:
                        self.get_rsi_signal(tick, klines, timeframe)

            logging.info("Waiting 30 seconds...")
            time.sleep(30)

if __name__ == "__main__":
    bot = RSISignalBot()
    bot.run()

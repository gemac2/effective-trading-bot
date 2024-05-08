from binance.client import Client
import time
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
import pandas as pd
import requests
import os
from dotenv import load_dotenv

bollinger_deviation_three = 3  # Third Deviation for Bollinger Bands
ideal_volume = 50000000
last_signal_time = {}
timeframe_wait_times = {
    "1 Hour": 3600,
    "4 Hours": 14400
}

load_dotenv()
telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

client = Client('your_api_key', 'your_api_secret', tld='com')

def initialize():
    global tickers_list
    tickers_list = search_ticks()

def search_ticks():
    ticks = []
    try:
        list_ticks = client.futures_symbol_ticker()
    except Exception as e:
        print(f"Error while getting ticks: {e}")
        return ticks

    for tick in list_ticks:
        if tick['symbol'][-4:] != 'USDT':
            continue
        if tick['symbol'] == "USDCUSDT" or tick['symbol'] == "BTCUSDT":
            continue
        ticks.append(tick['symbol'])

    print('Number of currencies found in the USDT Pair: #' + str(len(ticks)))

    return ticks

def get_klines_five_minutes(tick):
    try:
        klines = client.futures_klines(symbol=tick, interval=Client.KLINE_INTERVAL_5MINUTE, limit=48, timeout=30)
        timeframe = "1 Hour"
    except Exception as e:
        print(f"Error while getting data for {tick} 1-hour klines: {e}")
        return None, None

    return klines, timeframe

def get_klines_one_hour(tick):
    try:
        klines = client.futures_klines(symbol=tick, interval=Client.KLINE_INTERVAL_1HOUR, limit=48, timeout=30)
        timeframe = "1 Hour"
    except Exception as e:
        print(f"Error while getting data for {tick} 1-hour klines: {e}")
        return None, None

    return klines, timeframe

def get_klines_four_hour(tick):
    try:
        klines = client.futures_klines(symbol=tick, interval=Client.KLINE_INTERVAL_4HOUR, limit=48, timeout=30)
        timeframe = "4 Hours"
    except Exception as e:
        print(f"Error while getting data for {tick} 4-hour klines: {e}")
        return None, None

    return klines, timeframe

def get_info_ticks(tick):
    try:
        info = client.futures_ticker(symbol=tick)
    except Exception as e:
        print(f"Error while getting info for {tick}: {e}")
        return None

    return info

def human_format(volume):
    magnitude = 0
    while abs(volume) >= 1000:
        magnitude += 1
        volume /= 1000.0
    return '%.2f%s' % (volume, ['', 'K', 'M', 'G', 'T', 'P'][magnitude])

def get_bollinger_signals(tick, klines, timeframe):
    df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    df['close'] = df['close'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)

    rsi = RSIIndicator(df['close']).rsi().iloc[-1]
    if rsi == 100:
        return False

    wait_time = timeframe_wait_times.get(timeframe, None)
    if wait_time is None:
        print(f"Timeframe not found: {timeframe}")
        return False

    if tick not in last_signal_time or (time.time() - last_signal_time[tick]) > wait_time:
        # Bollinger Bands
        bb = BollingerBands(df['close'], window=20, window_dev=bollinger_deviation_three)
        upper_band = bb.bollinger_hband()
        lower_band = bb.bollinger_lband()
        close_price = df['close'].iloc[-1]
        max_high = df['high'].iloc[-1]
        min_low = df['low'].iloc[-1]

        # LONG signals
        if min_low < lower_band.iloc[-1] and close_price <= lower_band.iloc[-1]:
            info = get_info_ticks(tick)
            volume = float(info['quoteVolume'])
            if volume >= ideal_volume:
                send_telegram_message("⚠️ Third Bollinger Bands Broken", timeframe, "Possible Long", tick, human_format(volume), info['lastPrice'], info['highPrice'], info['lowPrice'], False)
                last_signal_time[tick] = time.time()
                return True

        # SHORT signals
        elif max_high > upper_band.iloc[-1] and close_price >= upper_band.iloc[-1]:
            info = get_info_ticks(tick)
            volume = float(info['quoteVolume'])
            if volume >= ideal_volume:
                send_telegram_message("⚠️ Third Bollinger Bands Broken", timeframe, "Possible Short", tick, human_format(volume), info['lastPrice'], info['highPrice'], info['lowPrice'], False)
                last_signal_time[tick] = time.time()
                return True
    
    return False

def send_telegram_message(title, timeframe, order_type, currency_name, volume, last_price, high_price, low_price, has_variation):
    url = f"https://api.telegram.org/bot{telegram_bot_token}/sendMessage"
    message = f"**{title}**\n\n"
    message += f"⌛️ TimeFrame: {timeframe}\n\n"
    message += f"🛍️ Order: {order_type}\n\n"
    message += f"🪙 Pair: {currency_name}\n\n"
    message += f"📊 Vol: {volume}\n\n"
    message += f"💰 Price: {last_price}\n\n"
    message += f"📈 High Price: {high_price}\n\n"
    message += f"📉 Low Price: {low_price}"
    payload = {
        "chat_id": telegram_chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print("Error while sending message to Telegram:", e)

def main_loop():
    while True:
        ticks = search_ticks()
        print('Scanning Currencies...')
        print('')
        for tick in ticks:
            klines_5m, time5m = get_klines_five_minutes(tick)
            if klines_5m is not None and time5m is not None:
                found_signal_bollinger = get_bollinger_signals(tick, klines_5m, time5m)
                if found_signal_bollinger:
                    print("Found signal for", tick, "on 5 minutes timeframe")
                    print('**************************************************')
                    print('')
            
            klines_1h, time1h = get_klines_one_hour(tick)
            if klines_1h is not None and time1h is not None:
                found_signal_bollinger = get_bollinger_signals(tick, klines_1h, time1h)
                if found_signal_bollinger:
                    print("Found signal for", tick, "on 1-hour timeframe")
                    print('**************************************************')
                    print('')
            
            klines_4h, time4h = get_klines_four_hour(tick)
            if klines_4h is not None and time4h is not None:
                found_signal_bollinger = get_bollinger_signals(tick, klines_4h, time4h)
                if found_signal_bollinger:
                    print("Found signal for", tick, "on 4-hour timeframe")
                    print('**************************************************')
                    print('')

        print('Waiting 30 seconds...')
        print('')
        time.sleep(30)

if __name__ == "__main__":
    initialize()
    main_loop()

from binance.client import Client
import time
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
import pandas as pd
import requests

bollinger_deviation_three = 3  #Third Deviation for Bollinger Bands
telegram_bot_token = '6633256482:AAGD_stwsFrVqeK7oXyy5W3nZdlvSbBYqXg'
telegram_chat_id = '1026595920'
ideal_volumen = 50000000

client = Client('', '', tld='com')

def initialize():
    global tickers_list
    tickers_list = search_ticks()

def search_ticks():
    ticks = []
    try:
        list_ticks = client.futures_symbol_ticker()
    except Exception as e:
        print(f"Error while we get the ticks: {e}")
        return ticks

    for tick in list_ticks:
        if tick['symbol'][-4:] != 'USDT':
            continue
        if tick['symbol'] ==  "USDCUSDT":
            continue
        ticks.append(tick['symbol'])

    print('Number of currency found in the USDT Pair: #' + str(len(ticks)))

    return ticks


def get_klines(tick):
    try:
        klines = client.futures_klines(symbol=tick, interval=Client.KLINE_INTERVAL_5MINUTE, limit=48)
    except Exception as e:
        print(f"Error while get data for klines  {tick}: {e}")
        return None

    return klines

def get_info_ticks(tick):
    try:
        info = client.futures_ticker(symbol=tick)
    except Exception as e:
        print(f"Error while we get the info for a ticker {tick}: {e}")
        return None

    return info


def human_format(volumen):
    magnitude = 0
    while abs(volumen) >= 1000:
        magnitude += 1
        volumen /= 1000.0
    return '%.2f%s' % (volumen, ['', 'K', 'M', 'G', 'T', 'P'][magnitude])

def get_bollinger_signals(tick, klines):
    df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    df['close'] = df['close'].astype(float)

    rsi = RSIIndicator(df['close']).rsi().iloc[-1]
    if rsi == 100:
        return

    # Bollinger Bands
    bb = BollingerBands(df['close'], window=20, window_dev=bollinger_deviation_three)
    upper_band = bb.bollinger_hband()
    lower_band = bb.bollinger_lband()
    close_price = df['close'].iloc[-1]

    # LONG signals
    if close_price <= lower_band.iloc[-1]:
        info = get_info_ticks(tick)
        volumen = float(info['quoteVolume'])
        if  volumen >= ideal_volumen:
            send_telegram_message("⚠️ Third Bollinger Bands Broken", "Possible Long", tick, human_format(volumen), info['lastPrice'], info['highPrice'], info['lowPrice'], False)
            return True

    # SHORT signals
    elif close_price >= upper_band.iloc[-1]:
        info = get_info_ticks(tick)
        volumen = float(info['quoteVolume'])
        if  volumen >= ideal_volumen:
            send_telegram_message("⚠️ Third Bollinger Bands Broken", "Possible Short", tick, human_format(volumen), info['lastPrice'], info['highPrice'], info['lowPrice'], False)
            return True
    
    return False

def send_telegram_message(title, order_type, currency_name, volume, last_price, high_price, low_price, has_variation):
    url = f"https://api.telegram.org/bot{telegram_bot_token}/sendMessage"
    message = f"**{title}**\n\n"
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
        print("Error while we send message to Telegram:", e)

def main_loop():
    while True:
        ticks = search_ticks()
        print('Scanning Currencies...')
        print('')
        for tick in ticks:
            klines = get_klines(tick)
            if klines is None:
                continue

            found_signal_bollinger = get_bollinger_signals(tick, klines)

            if found_signal_bollinger:
                print("Found signal for", tick)
                print('**************************************************')
                print('')
        print('Waiting 30 seconds...')
        print('')
        time.sleep(30)

if __name__ == "__main__":
    initialize()
    main_loop()

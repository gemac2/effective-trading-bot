from binance.client import Client
import time
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
import pandas as pd
import requests
import os
from dotenv import load_dotenv
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from enum import Enum
import logging
import asyncio
import aiohttp
from aiohttp import TCPConnector
import backoff

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TimeFrame(Enum):
    FIVE_MINUTES = ("5 Minutes", Client.KLINE_INTERVAL_5MINUTE, 300)
    FIFTEEN_MINUTES = ("15 Minutes", Client.KLINE_INTERVAL_15MINUTE, 900)
    ONE_HOUR = ("1 Hour", Client.KLINE_INTERVAL_1HOUR, 3600)
    FOUR_HOURS = ("4 Hours", Client.KLINE_INTERVAL_4HOUR, 14400)

    def __init__(self, display_name: str, interval: str, wait_time: int):
        self.display_name = display_name
        self.interval = interval
        self.wait_time = wait_time

@dataclass
class TradingConfig:
    bollinger_deviation: float = 3.0
    ideal_volume: float = 50_000_000
    klines_limit: int = 48
    scan_interval: int = 60
    excluded_pairs: set = frozenset({"USDCUSDT", "BTCUSDT"})
    max_concurrent_requests: int = 5
    rate_limit_per_second: int = 8
    connection_pool_size: int = 100
    request_timeout: int = 30

@dataclass
class SignalInfo:
    last_price: str
    high_price: str
    low_price: str
    volume: float

class RateLimiter:
    def __init__(self, rate_limit: int):
        self.rate_limit = rate_limit
        self.tokens = rate_limit
        self.last_update = time.time()
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            now = time.time()
            time_passed = now - self.last_update
            self.tokens = min(self.rate_limit, self.tokens + time_passed * self.rate_limit)
            self.last_update = now

            if self.tokens < 1:
                wait_time = (1 - self.tokens) / self.rate_limit
                await asyncio.sleep(wait_time)
                self.tokens = 0
            else:
                self.tokens -= 1

class TradingBot:
    def __init__(self):
        load_dotenv()
        self.config = TradingConfig()
        self.client = Client(
            os.getenv("BINANCE_API_KEY"),
            os.getenv("BINANCE_API_SECRET"),
            tld='com'
        )
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.last_signal_time: Dict[str, float] = {}
        self.session: Optional[aiohttp.ClientSession] = None
        self.rate_limiter = RateLimiter(self.config.rate_limit_per_second)
        self.semaphore = asyncio.Semaphore(self.config.max_concurrent_requests)

    async def initialize(self):
        connector = TCPConnector(
            limit=self.config.connection_pool_size,
            enable_cleanup_closed=True,
            keepalive_timeout=60
        )
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=self.config.request_timeout)
        )
        self.tickers_list = await self.search_ticks()

    async def cleanup(self):
        if self.session:
            await self.session.close()

    @backoff.on_exception(
        backoff.expo,
        (aiohttp.ClientError, asyncio.TimeoutError),
        max_tries=3,
        max_time=30
    )
    async def make_request(self, func, *args, **kwargs):
        async with self.semaphore:
            if self.session is None or self.session.closed:
                connector = TCPConnector(
                    limit=self.config.connection_pool_size,
                    enable_cleanup_closed=True,
                    keepalive_timeout=60
                )
                self.session = aiohttp.ClientSession(
                    connector=connector,
                    timeout=aiohttp.ClientTimeout(total=self.config.request_timeout)
                )
            await self.rate_limiter.acquire()
            return await func(*args, **kwargs)

    async def search_ticks(self) -> List[str]:
        try:
            ticks = []
            list_ticks = await self.make_request(
                asyncio.to_thread,
                self.client.futures_symbol_ticker
            )
            
            ticks = [
                tick['symbol'] for tick in list_ticks 
                if tick['symbol'].endswith('USDT') 
                and tick['symbol'] not in self.config.excluded_pairs
            ]
            
            logger.info(f'Number of currencies found in the USDT Pair: #{len(ticks)}')
            return ticks
            
        except Exception as e:
            logger.error(f"Error while getting ticks: {e}")
            return []

    async def get_klines(self, tick: str, timeframe: TimeFrame) -> Optional[Tuple[list, TimeFrame]]:
        try:
            klines = await self.make_request(
                asyncio.to_thread,
                self.client.futures_klines,
                symbol=tick,
                interval=timeframe.interval,
                limit=self.config.klines_limit
            )
            return klines, timeframe
        except Exception as e:
            logger.error(f"Error while getting {timeframe.display_name} klines for {tick}: {e}")
            return None

    async def get_info_ticks(self, tick: str) -> Optional[SignalInfo]:
        try:
            info = await self.make_request(
                asyncio.to_thread,
                self.client.futures_ticker,
                symbol=tick
            )
            return SignalInfo(
                last_price=info['lastPrice'],
                high_price=info['highPrice'],
                low_price=info['lowPrice'],
                volume=float(info['quoteVolume'])
            )
        except Exception as e:
            logger.error(f"Error while getting info for {tick}: {e}")
            return None

    @staticmethod
    def human_format(volume: float) -> str:
        for unit in ['', 'K', 'M', 'G', 'T', 'P']:
            if abs(volume) < 1000:
                return f"{volume:.2f}{unit}"
            volume /= 1000.0
        return f"{volume:.2f}P"

    async def analyze_bollinger_signals(self, tick: str, klines: list, timeframe: TimeFrame) -> bool:
        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df[['close', 'high', 'low']] = df[['close', 'high', 'low']].astype(float)

        rsi = RSIIndicator(df['close']).rsi().iloc[-1]
        if rsi == 100:
            return False

        current_time = time.time()
        if tick in self.last_signal_time and (current_time - self.last_signal_time[tick]) <= timeframe.wait_time:
            return False

        bb = BollingerBands(
            df['close'],
            window=20,
            window_dev=self.config.bollinger_deviation
        )
        
        upper_band = bb.bollinger_hband()
        lower_band = bb.bollinger_lband()
        close_price = df['close'].iloc[-1]
        max_high = df['high'].iloc[-1]
        min_low = df['low'].iloc[-1]

        signal_info = await self.get_info_ticks(tick)
        if not signal_info or signal_info.volume < self.config.ideal_volume:
            return False

        if min_low < lower_band.iloc[-1] and close_price <= lower_band.iloc[-1]:
            await self.send_telegram_message(
                "⚠️ Third Bollinger Bands Broken",
                timeframe.display_name,
                "Possible Long",
                tick,
                signal_info
            )
            self.last_signal_time[tick] = current_time
            return True

        if max_high > upper_band.iloc[-1] and close_price >= upper_band.iloc[-1]:
            await self.send_telegram_message(
                "⚠️ Third Bollinger Bands Broken",
                timeframe.display_name,
                "Possible Short",
                tick,
                signal_info
            )
            self.last_signal_time[tick] = current_time
            return True

        return False

    async def send_telegram_message(
        self,
        title: str,
        timeframe: str,
        order_type: str,
        currency_name: str,
        signal_info: SignalInfo
    ):
        if not self.telegram_bot_token or not self.telegram_chat_id:
            logger.warning("Telegram credentials not configured")
            return

        message = (
            f"**{title}**\n\n"
            f"⌛️ TimeFrame: {timeframe}\n\n"
            f"🛍️ Order: {order_type}\n\n"
            f"🪙 Pair: {currency_name}\n\n"
            f"📊 Vol: {self.human_format(signal_info.volume)}\n\n"
            f"💰 Last Price: {signal_info.last_price}\n"
            f"📈 High: {signal_info.high_price}\n"
            f"📉 Low: {signal_info.low_price}\n\n"
        )
        url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
        data = {
            "chat_id": self.telegram_chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }

        try:
            async with self.session.post(url, data=data) as resp:
                if resp.status == 200:
                    logger.info("Telegram notification sent successfully")
                else:
                    logger.error(f"Failed to send Telegram notification: {resp.status}")
        except Exception as e:
            logger.error(f"Error while sending Telegram notification: {e}")

    async def analyze_timeframe(self, tick: str, timeframe: TimeFrame):
        klines_data = await self.get_klines(tick, timeframe)
        if klines_data:
            klines, tf = klines_data
            await self.analyze_bollinger_signals(tick, klines, tf)

    async def process_batch(self, ticks: List[str], timeframe: TimeFrame):
        await asyncio.gather(*[self.analyze_timeframe(tick, timeframe) for tick in ticks])

    async def main_loop(self):
        while True:
            logger.info("Starting new scanning cycle")
            try:
                tasks = [
                    self.process_batch(self.tickers_list, tf)
                    for tf in TimeFrame
                ]
                await asyncio.gather(*tasks)
            except Exception as e:
                logger.error(f"Error during main loop: {e}")
            finally:
                await asyncio.sleep(self.config.scan_interval)

if __name__ == "__main__":
    bot = TradingBot()
    async def run_bot():
        try:
            await bot.initialize()
            await bot.main_loop()
        finally:
            await bot.cleanup()

    asyncio.run(run_bot())

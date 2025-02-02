import logging
import asyncio
import pandas as pd
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
from dataclasses import dataclass
from typing import Dict, List, Optional
from enum import Enum
from binance import AsyncClient, BinanceSocketManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TimeFrame(Enum):
    FIVE_MINUTES = ("5m", 300)
    FIFTEEN_MINUTES = ("15m", 900)
    ONE_HOUR = ("1h", 3600)
    FOUR_HOURS = ("4h", 14400)

    def __init__(self, interval: str, wait_time: int):
        self.interval = interval
        self.wait_time = wait_time

@dataclass
class TradingConfig:
    bollinger_deviation: float = 3.0
    ideal_volume: float = 50_000_000
    klines_limit: int = 48
    scan_interval: int = 60
    excluded_pairs: set = frozenset({"USDCUSDT", "BTCUSDT"})

@dataclass
class SignalInfo:
    last_price: float
    high_price: float
    low_price: float
    volume: float

class TradingBot:
    def __init__(self):
        self.config = TradingConfig()
        self.client = None
        self.bsm = None
        self.last_signal_time: Dict[str, float] = {}

    async def initialize(self):
        self.client = await AsyncClient.create()
        self.bsm = BinanceSocketManager(self.client)

    async def cleanup(self):
        if self.client:
            await self.client.close_connection()

    async def search_ticks(self) -> List[str]:
        try:
            exchange_info = await self.client.get_exchange_info()
            tickers = [
                symbol["symbol"] for symbol in exchange_info["symbols"]
                if symbol["symbol"].endswith("USDT") and symbol["symbol"] not in self.config.excluded_pairs
            ]
            logger.info(f"Found {len(tickers)} valid USDT pairs.")
            return tickers
        except Exception as e:
            logger.error(f"Error fetching tickers: {e}")
            return []

    async def start_websocket(self):
        tickers = await self.search_ticks()
        streams = [f"{ticker.lower()}@kline_{TimeFrame.FIFTEEN_MINUTES.interval}" for ticker in tickers]
        socket = self.bsm.multiplex_socket(streams)

        async with socket as stream:
            while True:
                try:
                    message = await stream.recv()
                    if message and "data" in message and message["data"].get("e") == "kline":
                        await self.handle_kline_message(message["data"])
                except Exception as e:
                    logger.error(f"Error in WebSocket stream: {e}")

    async def handle_kline_message(self, message):
        try:
            data = message["k"]
            tick = message["s"]
            close_price = float(data["c"])
            high_price = float(data["h"])
            low_price = float(data["l"])
            volume = float(data["q"])

            df = pd.DataFrame([{  
                "close": close_price,
                "high": high_price,
                "low": low_price,
                "volume": volume
            }])

            rsi = RSIIndicator(df["close"]).rsi().iloc[-1]

            if rsi < 30:
                logger.info(f"RSI < 30 detected on {tick}. Possible Long.")
                await self.send_signal(tick, "Long", close_price, high_price, low_price, volume)
            elif rsi > 70:
                logger.info(f"RSI > 70 detected on {tick}. Possible Short.")
                await self.send_signal(tick, "Short", close_price, high_price, low_price, volume)

            bb = BollingerBands(
                df["close"],
                window=20,
                window_dev=self.config.bollinger_deviation
            )

            upper_band = bb.bollinger_hband().iloc[-1]
            lower_band = bb.bollinger_lband().iloc[-1]

            if low_price < lower_band and close_price <= lower_band:
                await self.send_signal(tick, "Long", close_price, high_price, low_price, volume)

            if high_price > upper_band and close_price >= upper_band:
                await self.send_signal(tick, "Short", close_price, high_price, low_price, volume)

        except Exception as e:
            logger.error(f"Error handling kline message: {e}")

    async def send_signal(self, tick: str, signal_type: str, close: float, high: float, low: float, volume: float):
        logger.info(
            f"Signal: {signal_type} | Pair: {tick} | Close: {close:.2f} | High: {high:.2f} | Low: {low:.2f} | Volume: {volume:.2f}"
        )

    async def main_loop(self):
        await self.start_websocket()

async def main():
    bot = TradingBot()
    await bot.initialize()
    try:
        await bot.main_loop()
    finally:
        await bot.cleanup()

if __name__ == "__main__":
    asyncio.run(main())

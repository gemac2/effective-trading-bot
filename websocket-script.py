from binance.client import Client
from binance.async_client import AsyncClient
from binance import BinanceSocketManager
import asyncio
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
import pandas as pd
from dotenv import load_dotenv
import os
import logging
import time
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@dataclass
class TradingConfig:
    bollinger_deviation: float = 3.0
    ideal_volume: float = 50_000_000
    klines_limit: int = 48
    scan_interval: int = 60
    excluded_pairs: set = frozenset({"USDCUSDT", "BTCUSDT"})

class TimeFrame(Enum):
    FIFTEEN_MINUTES = "15m"
    ONE_HOUR = "1h"

    @property
    def ms_interval(self):
        return {
            "15m": 900_000,  # 15 minutos en ms
            "1h": 3_600_000  # 1 hora en ms
        }[self.value]

@dataclass
class SignalInfo:
    last_price: str
    high_price: str
    low_price: str
    volume: float

class TradingBot:
    def __init__(self):
        load_dotenv()
        self.config = TradingConfig()
        self.client: Optional[AsyncClient] = None
        self.bsm: Optional[BinanceSocketManager] = None
        self.data: Dict[str, pd.DataFrame] = {}
        self.last_signal_time: Dict[str, float] = {}

    async def initialize(self):
        self.client = await AsyncClient.create(
            os.getenv("BINANCE_API_KEY"),
            os.getenv("BINANCE_API_SECRET"),
            tld='com'
        )
        self.bsm = BinanceSocketManager(self.client)

    async def cleanup(self):
        if self.client:
            await self.client.close_connection()

    async def process_message(self, msg):
        """Procesar mensajes de WebSocket."""
        if msg.get('e') == 'error':
            logger.error(f"WebSocket error: {msg}")
        else:
            symbol = msg['s']
            kline = msg['k']

            # Actualizar DataFrame
            df = self.data.get(symbol, pd.DataFrame())
            new_row = {
                'timestamp': pd.to_datetime(kline['t'], unit='ms'),
                'open': float(kline['o']),
                'high': float(kline['h']),
                'low': float(kline['l']),
                'close': float(kline['c']),
                'volume': float(kline['q']),
            }
            df = pd.concat([df, pd.DataFrame([new_row])]).set_index('timestamp')
            self.data[symbol] = df.tail(self.config.klines_limit)  # Mantener solo las últimas 48 velas

            # Realizar análisis
            await self.analyze_bollinger_signals(symbol, df)

    async def analyze_bollinger_signals(self, tick: str, df: pd.DataFrame):
        """Analizar señales de Bandas de Bollinger."""
        if len(df) < 20:
            return  # Necesitamos al menos 20 velas para calcular las Bandas de Bollinger

        rsi = RSIIndicator(df['close']).rsi().iloc[-1]
        if rsi == 100:
            return

        current_time = time.time()
        if tick in self.last_signal_time and (current_time - self.last_signal_time[tick]) <= TimeFrame.FIFTEEN_MINUTES.ms_interval / 1000:
            return

        bb = BollingerBands(df['close'], window=20, window_dev=self.config.bollinger_deviation)
        upper_band = bb.bollinger_hband().iloc[-1]
        lower_band = bb.bollinger_lband().iloc[-1]
        close_price = df['close'].iloc[-1]

        if close_price <= lower_band:
            logger.info(f"Long Signal: {tick}")
            self.last_signal_time[tick] = current_time
        elif close_price >= upper_band:
            logger.info(f"Short Signal: {tick}")
            self.last_signal_time[tick] = current_time

    async def start_websocket(self):
        """Iniciar WebSocket para recibir datos."""
        tickers = await self.get_tickers()

        streams = [f"{tick.lower()}@kline_{TimeFrame.FIFTEEN_MINUTES.value}" for tick in tickers]
        stream = self.bsm.multiplex_socket(streams)

        try:
            while True:
                msg = await stream.recv()
                if msg:
                    await self.process_message(msg)
        except Exception as e:
            logger.error(f"Error al procesar el mensaje: {e}")


    async def get_tickers(self) -> List[str]:
        """Obtener tickers compatibles con el par USDT."""
        info = await self.client.futures_exchange_info()
        tickers = [
            symbol['symbol'] for symbol in info['symbols']
            if symbol['quoteAsset'] == 'USDT' and symbol['symbol'] not in self.config.excluded_pairs
        ]
        logger.info(f"Found {len(tickers)} USDT pairs.")
        return tickers

async def main():
    bot = TradingBot()
    await bot.initialize()
    try:
        await bot.start_websocket()
    finally:
        await bot.cleanup()

if __name__ == "__main__":
    asyncio.run(main())

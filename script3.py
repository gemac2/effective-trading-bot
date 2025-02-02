from binance.client import Client
from binance import ThreadedWebsocketManager
import asyncio
from dataclasses import dataclass
from typing import Dict, Optional, List
import logging
import os
import aiohttp
from dotenv import load_dotenv
import pandas as pd
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands

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
    excluded_pairs: set = frozenset({"USDCUSDT", "BTCUSDT"})
    scan_interval: int = 300  # Intervalo de escaneo en segundos
    max_concurrent_requests: int = 2  # Limitar concurrencia
    rate_limit_per_second: int = 2  # Limitar a 2 solicitudes por segundo

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
        self.tickers_list: List[str] = []
        self.twm = ThreadedWebsocketManager(
            api_key=os.getenv("BINANCE_API_KEY"),
            api_secret=os.getenv("BINANCE_API_SECRET")
        )
        self.twm.start()
    
    async def initialize(self):
        # Obtener lista de ticks una vez
        self.tickers_list = await asyncio.to_thread(self.search_ticks)
    
    def search_ticks(self) -> List[str]:
        try:
            ticks = self.client.futures_symbol_ticker()
            filtered_ticks = [
                tick['symbol'] for tick in ticks 
                if tick['symbol'].endswith('USDT') and tick['symbol'] not in self.config.excluded_pairs
            ]
            logger.info(f'Pares encontrados: {len(filtered_ticks)}')
            return filtered_ticks
        except Exception as e:
            logger.error(f"Error al obtener ticks: {e}")
            return []

    def handle_socket_message(self, msg):
        if 'e' in msg and msg['e'] == 'kline':  # Verificar si el mensaje es de Kline
            symbol = msg['s']
            kline = msg['k']
            is_final = kline['x']
            if is_final:  # Procesar solo si es la vela finalizada
                asyncio.run(self.process_kline(symbol, kline))
    
    async def process_kline(self, symbol: str, kline: dict):
        try:
            close_price = float(kline['c'])
            high_price = float(kline['h'])
            low_price = float(kline['l'])
            
            # Simulación de Bollinger y RSI para el ejemplo
            df = pd.DataFrame([{
                'close': close_price,
                'high': high_price,
                'low': low_price
            }])
            
            bb = BollingerBands(df['close'], window=20, window_dev=self.config.bollinger_deviation)
            upper_band = bb.bollinger_hband().iloc[-1]
            lower_band = bb.bollinger_lband().iloc[-1]
            rsi = RSIIndicator(df['close']).rsi().iloc[-1]
            
            if close_price < lower_band:
                await self.send_telegram_message(f"⚠️ Posible LONG en {symbol} - Precio bajo la banda inferior.")
            elif close_price > upper_band:
                await self.send_telegram_message(f"⚠️ Posible SHORT en {symbol} - Precio sobre la banda superior.")
        
        except Exception as e:
            logger.error(f"Error procesando Kline para {symbol}: {e}")

    async def send_telegram_message(self, message: str):
        if not self.telegram_bot_token or not self.telegram_chat_id:
            logger.warning("Credenciales de Telegram no configuradas.")
            return

        url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
        data = {
            "chat_id": self.telegram_chat_id,
            "text": message
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data) as resp:
                    if resp.status == 200:
                        logger.info("Notificación enviada con éxito.")
                    else:
                        logger.error(f"Error enviando notificación de Telegram: {resp.status}")
        except Exception as e:
            logger.error(f"Error en Telegram: {e}")

    async def main_loop(self):
        # Suscribirse a WebSocket para todos los pares
        for tick in self.tickers_list:
            self.twm.start_kline_socket(
                callback=self.handle_socket_message,
                symbol=tick,
                interval=Client.KLINE_INTERVAL_15MINUTE
            )

        logger.info("Bot en ejecución. Esperando señales...")
        while True:
            await asyncio.sleep(self.config.scan_interval)

if __name__ == "__main__":
    bot = TradingBot()
    asyncio.run(bot.initialize())
    asyncio.run(bot.main_loop())

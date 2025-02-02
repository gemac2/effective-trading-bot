
import os
import time
import math
import pandas as pd

from binance.client import Client
from dotenv import load_dotenv
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands

# ==================================
# CONFIGURACIONES GLOBALES
# ==================================

load_dotenv()
api_key = os.getenv("BINANCE_API_KEY")
api_secret = os.getenv("BINANCE_SECRET_KEY")

client = Client(api_key, api_secret, tld='com')

# Bollinger y RSI
BOLLINGER_DEVIATION = 3
IDEAL_VOLUME = 100_000_000  # Volumen mínimo en USDT
WAIT_TIME_5M = 300  # 5 minutos = 300 seg para no repetir señal en el mismo símbolo

# Rango de SL y TP
STOP_LOSS_PCT = 0.02  # 2%
TAKE_PROFIT_PCT = 0.03  # 4%

# Riesgo por operación
RISK_PERCENTAGE = 0.01  # 1% del capital

# Trackea la última señal para cada símbolo
last_signal_time = {}

# Trackea las posiciones abiertas
open_positions = {}

# NUEVO: Diccionario para cooldown post-cierre
COOLDOWN_AFTER_CLOSE = 900  # 15 minutos (en segundos)
cooldown_until = {}         # cooldown_until[symbol] = timestamp hasta el que no abrimos

# ==================================
# FUNCIONES DE OBTENCIÓN DE DATOS
# ==================================

def search_ticks():
    try:
        list_ticks = client.futures_symbol_ticker()
    except Exception as e:
        print(f"Error al obtener tickers de Futuros: {e}")
        return []

    ticks = []
    for tick in list_ticks:
        if tick['symbol'].endswith('USDT'):
            # Excluye pares si lo deseas
            if tick['symbol'] not in ("USDCUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT", "ANIMEUSDT", "BNBUSDT"):
                ticks.append(tick['symbol'])

    print(f"Número de monedas encontradas en par USDT: {len(ticks)}")
    return ticks

def get_klines_5m(symbol, limit=48, timeout=30):
    try:
        klines = client.futures_klines(
            symbol=symbol,
            interval=Client.KLINE_INTERVAL_5MINUTE,
            limit=limit,
            timeout=timeout
        )
        return klines
    except Exception as e:
        print(f"Error al obtener klines de 5m para {symbol}: {e}")
        return None

def get_info_tick(symbol):
    try:
        info = client.futures_ticker(symbol=symbol)
        return info
    except Exception as e:
        print(f"Error al obtener info para {symbol}: {e}")
        return None

def get_current_funding_rate(symbol):
    """
    Obtiene el funding rate más reciente de un símbolo de Futuros.
    Retorna None si ocurre un error.
    """
    try:
        # limit=1 nos devuelve el registro más reciente
        response = client.futures_funding_rate(symbol=symbol, limit=1)
        if response and len(response) > 0:
            return float(response[0]['fundingRate'])
    except Exception as e:
        print(f"Error al obtener funding rate de {symbol}: {e}")
    return None

# ==================================
# FUNCIONES AUXILIARES
# ==================================

def get_futures_usdt_balance():
    try:
        futures_balance = client.futures_account_balance()
        for asset_info in futures_balance:
            if asset_info["asset"] == "USDT":
                return float(asset_info["balance"])
    except Exception as e:
        print(f"Error al obtener balance de Futuros: {e}")
    return 0.0

def get_step_size(symbol):
    try:
        exchange_info = client.futures_exchange_info()
        for s in exchange_info["symbols"]:
            if s["symbol"] == symbol:
                for f in s["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        return float(f["stepSize"])
    except:
        pass
    return 0.0

def adjust_quantity_to_step_size(qty, step_size):
    if step_size <= 0:
        return round(qty, 3)  # fallback
    decimals = int(round(-math.log(step_size, 10), 0))
    return round(qty, decimals)

def get_price_tick_size(symbol):
    try:
        exchange_info = client.futures_exchange_info()
        for s in exchange_info["symbols"]:
            if s["symbol"] == symbol:
                for f in s["filters"]:
                    if f["filterType"] == "PRICE_FILTER":
                        return float(f["tickSize"])
    except:
        pass
    return 0.0

def adjust_price_to_tick_size(price, tick_size):
    if tick_size <= 0:
        return round(price, 6)  # fallback
    decimals = int(round(-math.log(tick_size, 10), 0))
    return round(price, decimals)

def calculate_quantity_for_1pct_risk(symbol, entry_price, stop_price):
    usdt_balance = get_futures_usdt_balance()
    if usdt_balance <= 0:
        print("No hay balance USDT en Futuros, no se puede abrir posición.")
        return 0.0

    risk_in_usdt = usdt_balance * RISK_PERCENTAGE
    distance_abs = abs(entry_price - stop_price)
    stop_distance_pct = distance_abs / entry_price

    if stop_distance_pct <= 0:
        print("stop_distance_pct = 0, revisa valores.")
        return 0.0

    position_size_usdt = risk_in_usdt / stop_distance_pct
    base_qty = position_size_usdt / entry_price

    step_size = get_step_size(symbol)
    final_qty = adjust_quantity_to_step_size(base_qty, step_size)
    return final_qty

def is_position_open_in_binance(symbol):
    try:
        positions = client.futures_position_information(symbol=symbol)
        for pos in positions:
            if float(pos['positionAmt']) != 0.0:
                return True
        return False
    except Exception as e:
        print(f"Error consultando posición en {symbol}: {e}")
        return False

def cancel_order(symbol, order_id):
    try:
        orders = client.futures_get_open_orders(symbol=symbol)
        for order in orders:
            client.futures_cancel_order(symbol=symbol, orderId=order['orderId'])
        print(f"[{symbol}] Orden {order_id} cancelada.")
    except Exception as e:
        print(f"No se pudo cancelar la orden {order_id} en {symbol}: {e}")

# ==================================
# COLOCAR ÓRDENES (MARKET, SL, TP)
# ==================================


def place_futures_limit_order(symbol, side, entry_price):
    price_tick_size = get_price_tick_size(symbol)

    if side == "LONG":
        position_side = "LONG"
        calc_stop_price = entry_price * (1 - STOP_LOSS_PCT)
        calc_tp_price = entry_price * (1 + TAKE_PROFIT_PCT)
    else:  # SHORT
        position_side = "SHORT"
        calc_stop_price = entry_price * (1 + STOP_LOSS_PCT)
        calc_tp_price = entry_price * (1 - TAKE_PROFIT_PCT)

    stop_price = adjust_price_to_tick_size(calc_stop_price, price_tick_size)
    tp_price = adjust_price_to_tick_size(calc_tp_price, price_tick_size)

    quantity = calculate_quantity_for_1pct_risk(symbol, entry_price, stop_price)
    if quantity <= 0:
        print(f"[{symbol}] Cantidad calculada <= 0. No abrimos operación.")
        return None, None, None

    stop_order_id = None
    tp_order_id = None

    if side == "LONG":
        # Abrir posición LONG (LIMIT)
        try:
            client.futures_create_order(
                symbol=symbol,
                side="BUY",
                type="LIMIT",
                positionSide="LONG",   # Hedge mode
                timeInForce="GTC",
                price=str(entry_price),
                quantity=quantity
            )
            print(f"[{symbol}] Orden LIMIT BUY (LONG) ejecutada. Qty={quantity}")
        except Exception as e:
            print(f"Error abriendo LONG en {symbol}: {e}")
            return None, None, None

        # Stop Loss (STOP_MARKET)
        try:
            stop_order = client.futures_create_order(
                symbol=symbol,
                side="SELL",           # cierra LONG
                positionSide="SHORT",
                type="STOP_MARKET",
                stopPrice=stop_price,
                quantity=quantity
            )
            stop_order_id = stop_order["orderId"]
            print(f"[{symbol}] Stop Loss en {stop_price}. ID={stop_order_id}")
        except Exception as e:
            print(f"Error al colocar SL en {symbol}: {e}")

        # Take Profit (LIMIT)
        try:
            tp_order = client.futures_create_order(
                symbol=symbol,
                side="SELL",
                positionSide="LONG",
                type="STOP_MARKET",
                stopPrice=str(tp_price),
                quantity=quantity,
                timeInForce="GTC"
            )
            tp_order_id = tp_order["orderId"]
            print(f"[{symbol}] Take Profit en {tp_price}. ID={tp_order_id}")
        except Exception as e:
            print(f"Error al colocar TP en {symbol}: {e}")

    else:  # SHORT
        # Abrir posición SHORT (LIMIT)
        try:
            client.futures_create_order(
                symbol=symbol,
                side="SELL",
                type="LIMIT",
                positionSide="SHORT",
                price=str(tp_price),
                timeInForce="GTC",
                quantity=quantity
            )
            print(f"[{symbol}] Orden LIMIT SELL (SHORT) ejecutada. Qty={quantity}")
        except Exception as e:
            print(f"Error abriendo SHORT en {symbol}: {e}")
            return None, None, None

        # Stop Loss (STOP_MARKET)
        try:
            stop_order = client.futures_create_order(
                symbol=symbol,
                side="BUY",            # cierra SHORT
                positionSide="LONG",
                type="STOP_MARKET",
                stopPrice=stop_price,
                quantity=quantity
            )
            stop_order_id = stop_order["orderId"]
            print(f"[{symbol}] Stop Loss en {stop_price}. ID={stop_order_id}")
        except Exception as e:
            print(f"Error al colocar SL en {symbol}: {e}")

        # Take Profit (LIMIT)
        try:
            tp_order = client.futures_create_order(
                symbol=symbol,
                side="BUY",
                positionSide="SHORT",
                type="STOP_MARKET",
                stopPrice=str(tp_price),
                quantity=quantity,
                timeInForce="GTC"
            )
            tp_order_id = tp_order["orderId"]
            print(f"[{symbol}] Take Profit en {tp_price}. ID={tp_order_id}")
        except Exception as e:
            print(f"Error al colocar TP en {symbol}: {e}")

    return stop_order_id, tp_order_id, quantity

# ==================================
# CHECKEAR ÓRDENES ABIERTAS (SL/TP)
# ==================================

def check_open_positions():
    symbols_in_positions = list(open_positions.keys())

    for symbol in symbols_in_positions:
        data = open_positions[symbol]
        stop_order_id = data.get("stop_order_id")
        tp_order_id = data.get("tp_order_id")

        stop_filled = False
        tp_filled   = False

        # Check Stop
        if stop_order_id:
            try:
                stop_info = client.futures_get_order(symbol=symbol, orderId=stop_order_id)
                if stop_info["status"] == "FILLED":
                    stop_filled = True
            except Exception as e:
                print(f"Error consultando SL {stop_order_id} de {symbol}: {e}")

        # Check TP
        if tp_order_id:
            try:
                tp_info = client.futures_get_order(symbol=symbol, orderId=tp_order_id)
                if tp_info["status"] == "FILLED":
                    tp_filled = True
            except Exception as e:
                print(f"Error consultando TP {tp_order_id} de {symbol}: {e}")

        # Si se llenó STOP, cancelamos TP y removemos la posición
        if stop_filled:
            print(f"[{symbol}] STOP LOSS ejecutado. Cerramos posición.")
            if tp_order_id:
                cancel_order(symbol, tp_order_id)
            open_positions.pop(symbol, None)
            continue

        # Si se llenó TP, cancelamos SL y removemos la posición
        if tp_filled:
            print(f"[{symbol}] TAKE PROFIT ejecutado. Cerramos posición.")
            if stop_order_id:
                cancel_order(symbol, stop_order_id)
            
            # NUEVO: Activar cooldown 15 min
            cooldown_until[symbol] = time.time() + COOLDOWN_AFTER_CLOSE

            open_positions.pop(symbol, None)
            continue

# ==================================
# FUNCIÓN DE SEÑAL (BOLLINGER + RSI)
# ==================================

def apply_strategy_bollinger_rsi_5m(symbol, klines):
    if not klines:
        return False

    df = pd.DataFrame(klines, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
    ])

    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    df['close'] = df['close'].astype(float)
    df['high']  = df['high'].astype(float)
    df['low']   = df['low'].astype(float)

    # RSI
    rsi = RSIIndicator(df['close']).rsi().iloc[-1]
    if rsi == 100:
        return False

    # Bollinger Bands
    bb = BollingerBands(df['close'], window=20, window_dev=BOLLINGER_DEVIATION)
    upper_band = bb.bollinger_hband().iloc[-1]
    lower_band = bb.bollinger_lband().iloc[-1]

    close_price = df['close'].iloc[-1]
    max_high    = df['high'].iloc[-1]
    min_low     = df['low'].iloc[-1]

    # Definir precio objetivo de la orden límite a 1% de distancia
    short_limit_price = upper_band * 1.01  # 1% por encima de la banda superior
    long_limit_price  = lower_band * 0.99  # 1% por debajo de la banda inferior

    price_tick_size = get_price_tick_size(symbol)
    short_limit_price = adjust_price_to_tick_size(short_limit_price, price_tick_size)
    long_limit_price = adjust_price_to_tick_size(long_limit_price, price_tick_size)

    # Condiciones de LONG (cuando el precio toque la banda inferior, colocamos orden límite en 1% menos)
    if (min_low <= lower_band) and (rsi < 30):
        info = get_info_tick(symbol)
        if not info:
            return False

        volume = float(info['quoteVolume'])
        if volume >= IDEAL_VOLUME:
            funding_rate = get_current_funding_rate(symbol)
            if funding_rate is not None and funding_rate < 0:
                print(f"[{symbol}] Funding Rate negativo ({funding_rate}), no abrimos operación.")
                return False

            if is_position_open_in_binance(symbol) or symbol in open_positions:
                print(f"Ya hay una posición abierta en {symbol}. No abrimos otra.")
                return False

            entry_price = long_limit_price  # Orden límite en 1% menos de la banda inferior
            stop_id, tp_id, qty = place_futures_limit_order(symbol, side="LONG", entry_price=entry_price)
            if stop_id and tp_id:
                open_positions[symbol] = {
                    "side": "LONG",
                    "position_size": qty,
                    "stop_order_id": stop_id,
                    "tp_order_id": tp_id
                }
            return True

    # Condiciones de SHORT (cuando el precio toque la banda superior, colocamos orden límite en 1% más)
    if (max_high >= upper_band) and (rsi > 70):
        info = get_info_tick(symbol)
        if not info:
            return False

        volume = float(info['quoteVolume'])
        if volume >= IDEAL_VOLUME:
            funding_rate = get_current_funding_rate(symbol)
            if funding_rate is not None and funding_rate < 0:
                print(f"[{symbol}] Funding Rate negativo ({funding_rate}), no abrimos operación.")
                return False

            if is_position_open_in_binance(symbol) or symbol in open_positions:
                print(f"Ya hay una posición abierta en {symbol}. No abrimos otra.")
                return False

            entry_price = short_limit_price  # Orden límite en 1% más de la banda superior
            stop_id, tp_id, qty = place_futures_limit_order(symbol, side="SHORT", entry_price=entry_price)
            if stop_id and tp_id:
                open_positions[symbol] = {
                    "side": "SHORT",
                    "position_size": qty,
                    "stop_order_id": stop_id,
                    "tp_order_id": tp_id
                }
            return True

    return False

# ==================================
# BUCLE PRINCIPAL
# ==================================

def main_loop():
    tickers = search_ticks()

    while True:
        # Revisión de número de posiciones abiertas
        if len(open_positions) >= 10:
            print("Número máximo de operaciones abiertas alcanzado (10). Pausando el bot...")
            break  # Sale del bucle principal y detiene el bot.
            # Si prefieres pausar en lugar de detener, usa:
            # time.sleep(300)  # Espera 5 minutos antes de reintentar
            # continue

        print("Iniciando ciclo de análisis de 5m...\n")
        for symbol in tickers:
            if symbol in cooldown_until and time.time() < cooldown_until[symbol]:
                # Aún en ventana de enfriamiento, saltamos este símbolo
                continue
            
            # Verificamos si pasaron 5min (300s) desde la última señal
            if symbol not in last_signal_time or (time.time() - last_signal_time[symbol]) > WAIT_TIME_5M:
                klines_5m = get_klines_5m(symbol)
                if klines_5m:
                    signal_found = apply_strategy_bollinger_rsi_5m(symbol, klines_5m)
                    if signal_found:
                        last_signal_time[symbol] = time.time()

        check_open_positions()

        print("Ciclo completado, espero 60 seg...\n")
        time.sleep(60)


if __name__ == "__main__":
    main_loop()
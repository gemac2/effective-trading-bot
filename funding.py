from binance.client import Client

# Configura tus claves API de Binance
api_key = 'TU_API_KEY'
api_secret = 'TU_API_SECRET'

# Inicializa el cliente de Binance
client = Client(api_key, api_secret, tld='com')

# Función para obtener el funding rate de un par de trading
def get_funding_rate(symbol):
    try:
        # Obtiene el funding rate más reciente
        mark_price_info = client.futures_mark_price(symbol=symbol)
        if mark_price_info:
            mark_price = float(mark_price_info['lastFundingRate'])
            print(f"Precio de marca para {symbol}: {mark_price:.8f}")
        else:
            print(f"No se encontró información de precio de marca para {symbol}")

        funding_info = client.futures_funding_rate(symbol=symbol, limit=1)
        
        if funding_info:
            funding_rate = float(funding_info[0]['fundingRate'])
            funding_time = funding_info[0]['fundingTime']
            print(f"Funding Rate para {symbol}: {funding_rate * 100}%")
            print(f"Fecha y hora del funding: {funding_time}")
        else:
            print(f"No se encontró información de funding para {symbol}")
    
    except Exception as e:
        print(f"Error al obtener el funding rate: {e}")

# Ejemplo de uso
symbol = 'BTCUSDT'  # Puedes cambiar el símbolo por el par que desees
get_funding_rate(symbol)
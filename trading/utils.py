import yfinance as yf
import numpy as np
import finnhub


from trading.settings import FINNHUB_API_KEY
# def get_current_price(ticker):
#     """
#     Get the latest stock price for a given ticker.
#     Parameters:
#     - ticker (str): The stock ticker symbol.
#     Returns:
#     - float: The latest stock price.
#     """
#     try:
#         data = yf.download(tickers=ticker, period='1d', interval='1m')
#         price = np.round(data.iloc[-1].Close, 2)
#         print('Price', price)
#         current_price = get_current_price(ticker_symbol)
#         return price.values[0]
#     except Exception as e:
#         print(f"Error fetching data for {ticker}: {e}")
#         return None, None
    

# Replace 'YOUR_API_KEY' with your actual Finnhub API key


# Initialize the Finnhub client
finnhub_client = finnhub.Client(api_key=FINNHUB_API_KEY)

def get_current_price(ticker):
    """
    Retrieves the current stock price for a given symbol.

    Args:
        symbol (str): The stock ticker symbol (e.g., "AAPL").

    Returns:
        float: The current stock price, or None if an error occurs.
    """
    try:
        quote = finnhub_client.quote(ticker)
        if quote and quote['c'] is not None:
            return quote['c']  # 'c' represents the current price
        else:
            print(f"Could not retrieve quote for {ticker}")
            return None

    except Exception as e:
        print(f"Error retrieving stock price for {ticker}: {e}")
        return None



ticker_symbol = "AAPL"  # Example: Google stock
current_price = get_current_price(ticker_symbol)
print(f"The current price of {ticker_symbol} is: {current_price}")









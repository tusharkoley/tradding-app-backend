

from eodhd import APIClient
import pandas as pd
from dotenv import load_dotenv
import os
from pathlib import Path
import requests

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

print(BASE_DIR)

# Explicitly load the .env file from BASE_DIR so manage.py runs pick it up
load_dotenv(BASE_DIR / '.env')

API_KEY = os.environ.get("EODHD_API_KEY")


api = APIClient(api_key=API_KEY)


resp = api.get_eod_historical_stock_market_data(symbol = 'HDFC.NSE', period='d', from_date = '2023-01-01', to_date = '2023-01-15', order='a')


# df = pd.DataFrame(resp) 
# print(df)

# resp = api.get_eod_historical_stock_market_data(symbol = 'APPL.US', period='d', from_date = '2023-01-01', to_date = '2023-01-15', order='a')


# df1 = pd.DataFrame(resp) 
# print(df1)

def get_company_profile(symbol):
    url = f"https://eodhd.com/api/fundamentals/{symbol}"
    params = {
        "api_token": API_KEY,
        "fmt": "json"
    }

    response = requests.get(url, params=params)
    response.raise_for_status()

    data = response.json()

    # Company profile info
    profile = data.get("General", {})

    return {
        "symbol": symbol,
        "name": profile.get("Name"),
        "isin": profile.get("ISIN"),
        "sector": profile.get("Sector"),
        "industry": profile.get("Industry"),
        "exchange": profile.get("Exchange"),
        "currency": profile.get("Currency"),
        "country": profile.get("Country"),
        "address": profile.get("Address"),
        "description": profile.get("Description"),
        "website": profile.get("WebURL"),
    }


profile = get_company_profile("RELIANCE.NSE")
for k, v in profile.items():
    print(f"{k}: {v}")



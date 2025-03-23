import pandas as  pd
import yfinance as yf
from datetime import datetime
import json
import requests
from tqdm import tqdm

import os

# os.environ.setdefault('price_seq',str(0))

start_pr_seq = int(os.environ.get('price_seq','0'))
# price_seq_iter = iter(range(start_pr_seq,2**32))

with open('seq.txt','r') as f:
    start_pr_seq = int(f.read())
    

print('***** Start Seq val ', start_pr_seq)

class Stock:
    def __init__(self, ticker,company_id=0, period='10y', batch_size=500):
        self.ticker = ticker
        self.company_id = company_id
        self.stock  = yf.Ticker(ticker) 
        self.period = period
        self.batch_size = batch_size



    def prepare_company_data(self):
        info = self.stock.info
        companay_data = [{
        "id": self.company_id,
        "ticker": self.ticker,
        "company_name": info.get('shortName',''),
        "industry": info.get('industry',''),
        "description": (info.get('longBusinessSummary',''))[:2000],
        "country": info.get('country',''),
        "website": info.get('website',''),
        "address": info.get('address1','')
        }]
        return companay_data
    

    def prepare_price_data(self):
        global start_pr_seq
        try:
            df = self.stock.history(period=self.period)
        except:
            df = self.stock.history(period='max')


        df['company']=self.company_id
        df['date'] = df.reset_index()['Date'].apply(lambda x : datetime.strftime(x,'%Y-%m-%d')).values
        df = df.reset_index().drop('Date', axis=1)
        df['ticker'] = self.ticker


        # price_id_list = list(range(start_pr_seq, start_pr_seq+df.shape[0]))
        # # df['id'] = price_id_list

        start_pr_seq = start_pr_seq + start_pr_seq+df.shape[0]
   
        df = df.rename(columns={
            'Open':'open',
            'High':'high',
            'Low':'low',
            'Close':'close',
            'Volume':'volume',
            'Dividends':'dividends',
            'Stock Splits':'stock_splits'
                })
        
        tot_records = df.shape[0]
        idx=0
        batched_data = []
        while idx<tot_records:
            df_batch = df.iloc[idx:idx+self.batch_size]
            idx=idx+self.batch_size
            data = list(df_batch.T.to_dict().values())
            batched_data.append(data)


        return batched_data


    def upload_company_info_in_db(self, target_url, auth, headers):
        
        data = self.prepare_company_data()
        data = json.dumps(data)
        # print(data)
        # print('*****************')
        
        resp = requests.post(url=target_url, data=data, headers=headers,auth=auth)

        if resp.status_code==201:
            print ('Data inserted Successfully')


    def upload_price_info_in_db(self,  target_url, auth, headers):

        batched_data = self.prepare_price_data()

        for data in batched_data:

            # print(f'***** Data id start {data[0]} and \n **** end {data[-1]} ')
            
            data_json = json.dumps(data,indent=2)
           
            resp = requests.post(url=target_url, data=data_json, headers=headers,auth=auth)

            if resp.status_code==201:
                print ('Data inserted Successfully')

            else:
                with open('pricedata','a') as f:
                    f.write(data_json)
                print (f'Failed to insert the data {resp.status_code}')




company_url = 'http://127.0.0.1:8000/stocks/companies/'
price_url = 'http://127.0.0.1:8000/stocks/prices/'

headers={
    'Content-type':'application/json', 
    'Accept':'application/json'
}
auth = ('admin', 'Tushar@123')

snp_df = pd.read_csv('snp500.csv')

tickers = list(snp_df['Symbol'].values)

ticker_id_list =  [(t,id) for id,t in enumerate(tickers)]

# Skip the processed data
for (t,id) in ticker_id_list:
    if t!='TFC':
        ticker_id_list.pop(0)
    else:
        break


while ticker_id_list:
    ticker, company_id = (ticker_id_list.pop(0))

    stock = Stock(ticker=ticker, company_id=company_id)
    stock.upload_company_info_in_db(target_url=company_url, auth=auth, headers=headers)
    stock.upload_price_info_in_db(target_url=price_url, auth=auth, headers=headers)

    print(f'Data loading completed sucecssfully for {ticker}')

    


# print(f'**** The Seql Value {start_pr_seq}')
os.environ.setdefault('price_seq',str(start_pr_seq))










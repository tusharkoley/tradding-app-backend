
import pandas as pd
import requests
import json





snp_df['description'] = snp_df['Security']
snp_df = snp_df.rename(columns={
                    'Symbol' : 'ticker',
                    'Security' : 'company_name',
                    'GICS Sector' : 'sector',
                    'GICS Sub-Industry' : 'industry',
                    'Headquarters Location' : 'address',
                       
                       })

snp_df = snp_df.drop(['CIK', 'Date added','Founded'], axis=1)
snp_df['country'] = 'USA'

company_url = 'http://127.0.0.1:8000/stocks/companies/'

headers={
    'Content-type':'application/json', 
    'Accept':'application/json'
}
auth = ('admin', 'Tushar@123')

failed_lst = []
for idx, row in snp_df.iterrows():
    try:
        data = row.to_dict()
        data['id'] = idx
        data = json.dumps(data)
        print(data)
        res = requests.post(company_url, data=data, auth=auth, headers=headers)
        print(res)

    except BaseException:
        print(f'failed to load the data  for {row.ticker}')
        failed_lst.append(idx)

print('Failed indexes', failed_lst)




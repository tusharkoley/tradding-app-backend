from django.shortcuts import render

from .models import Transactions, Portfolio
from users.models import Profile
from trading.utils import get_current_price
from django.views.decorators.csrf import csrf_exempt
from rest_framework.parsers import JSONParser
from django.http import HttpResponse
import json

@csrf_exempt
def trade_security(request,userId,  ticker):
    if request.method=='POST':     
        data = JSONParser().parse(request)
        qunatity = data.get('quantity',0)
        user = Profile.objects.get(id=userId)
        price = data.get('price')
        brokerage = data.get('brokerage',0)
        try:
            portfolio_entry = Portfolio.objects.get(user=user,ticker=ticker)
            new_qty = portfolio_entry.quantity + qunatity            
            if round(new_qty,4)==0:
                portfolio_entry.delete()
            else:
                portfolio_entry.quantity = new_qty
                portfolio_entry.save()
        except Portfolio.DoesNotExist:
            portfolio = Portfolio()
            portfolio.user = user
            portfolio.ticker = ticker
            portfolio.quantity = qunatity
            portfolio.save()
        transaction = Transactions()
        transaction.user = user
        if qunatity> 0:
            transaction.transaction_type = 'Buy'
        else:
            transaction.transaction_type = 'Sell'
        transaction.amount = price*qunatity
        user.cash_position = user.cash_position - price*qunatity
        transaction.ticker = ticker
        transaction.save()
        transaction = Transactions()
        transaction.user = user
        transaction.transaction_type = 'Brokerage'
        transaction.amount = brokerage
        user.cash_position = user.cash_position - brokerage
        transaction.save()
        user.save()

    return HttpResponse(f""" Successfully bought the {qunatity} of {ticker} share""")

@csrf_exempt
def money_transfer(request,userId):
    if request.method=='POST':
        data = JSONParser().parse(request)
        amount = data.get('amount',0)
        user = Profile.objects.get(id=userId)
        user.cash_position = user.cash_position + amount        
        transaction = Transactions()
        if amount>0:
            transaction.transaction_type='Deposit'
        else:
            transaction.transaction_type='Withdraw'
        transaction.amount = amount
        transaction.user = user
        transaction.save()
        user.save()
        return HttpResponse(f""" Amount updated successfully""")

def calculate_portfolio_performance(request, userId):
    if request.method == 'GET':
        user = Profile.objects.get(id=userId)
        cash_pos = user.cash_position
        portfolio_entries = Portfolio.objects.filter(user=user) 
        position_value = 0
 
        for entry in portfolio_entries:
            position_value += (get_current_price(entry.ticker)*entry.quantity)
        
        total_val = cash_pos + position_value
        user_deposits = Transactions.objects.filter(user=user,transaction_type='Deposit')
        user_withdraw = Transactions.objects.filter(user=user,transaction_type='Withdraw')
        total_deposit = 0
        total_withraw = 0
        for transaction in user_deposits:
            
            total_deposit += transaction.amount
        for transaction in user_withdraw:
            total_withraw += transaction.amount
        total_invested = total_deposit + total_withraw
        total_position_value = position_value + cash_pos

        if total_invested > 0:
            overall_portfolio_ret = (total_position_value  - total_invested)/total_invested  
        else:
            overall_portfolio_ret = 0 

        portfoli_performance = {
            'total_deposit' : total_deposit,
            'total_withdraw' : total_withraw,
            'toal_invested' : total_invested,
            'current_holding_amount' : position_value,
            'cash_position' : cash_pos,
            'overall_portfolio_ret' : f'{overall_portfolio_ret*100}%'
        }

        json_str = json.dumps(portfoli_performance)
                              
        return HttpResponse(json_str)

        



        

        








        
        


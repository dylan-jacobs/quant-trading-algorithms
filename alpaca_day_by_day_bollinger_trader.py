# -*- coding: utf-8 -*-
"""
Created on Thu Dec 8 2022

@author: dylan

Strategy: semi hft?
Every five mins:
    iterate throught list of tickers (all from s&p500 index)
    find one that is at low bollinger index
    buy
    keep track of bought stocks and profits
        when owned stocks are at too high bollinger -> sell, or when price has risen by $1
        sell when lost > 5% of initial dinero
"""

import alpaca_trade_api as tradeapi
from bs4 import BeautifulSoup
import csv
import matplotlib as plt
import json
import pandas as pd
import numpy as np
import yfinance
import time
from datetime import datetime
import math
from pytz import timezone
import sys
import requests
import pandas_datareader as web

# ALPACA API Info for fetching data, portfolio, etc. from Alpaca
BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_API_KEY = <ALPACA API KEY>
ALPACA_SECRET_KEY = <ALPACA SECRET KEY>

# Instantiate REST API Connection
api = tradeapi.REST(key_id=ALPACA_API_KEY, secret_key=ALPACA_SECRET_KEY, 
                    base_url=BASE_URL, api_version='v2')

# trade variables
BUY_THRESHOLD = -0.3 # how small the bollinger dif needs to be to make a trade
SELL_THRESHOLD = 0.3
BUY_PRICE = 0
BUY_IMBALANCE = 0
SELL_PRICE = 0
SELL_IMBALANCE = 0
TOTAL_PROFIT = 0
PROFIT = 0
AMOUNT_TO_TRADE = 5 # the amount of stock we buy/sell each trade
START_MONEY = api.get_account().cash
CLIENT_ASSET_ID = None # used to check pending orders

def getTickersFromSP500List():
    tickers = []
    with open('S&P500.txt', 'r') as txt:
        for line in txt.readlines():
            tickers.append(line.strip().split(',')[0])
    return tickers

# general variables
START_TIME = time.time()
# tickers = ['AAPL', 'NDAQ', 'AMZN', 'SPYD', 'SPHD', 'DJD', 'VOO', 'TSLA', 'GOOG', 'ABM']
tickers = getTickersFromSP500List()
OUTSIDE_OF_TRADING_HOURS = False
MAX_LOSS = 0.05 # * initial dinero
DELAY = 30 # time in between iterations (5 mins)

def getAlpacaQuote(ticker):
    try:
        quote = api.get_latest_quote(ticker)
    except:
        quote = None
    return quote


def getBollingerBands(X, timeframe=20): # timeframe = num days to look backwards to calculate sma, std
    sma = X.rolling(timeframe).mean().values
    std = X.rolling(timeframe).std().values
    bollinger_up = sma + (2 * std)  # Calculate top band
    bollinger_down = sma - (2 * std) # Calculate bottom band
    return sma, std, bollinger_up, bollinger_down

def calculateBollinger(current_ticker):
    closes = yfinance.download(current_ticker.replace('.', '-'), period='1y', progress=False).filter(['Close'])
    quote = getAlpacaQuote(current_ticker)

    # get price, bollinger bands
    sma, std, bollinger_up, bollinger_down = getBollingerBands(closes)
    current_price = quote.ap

    # calculate range between 1, -1 ===> the closer it is to -1, the better the price??!
    mid = bollinger_down[-1] + ((bollinger_up[-1] - bollinger_down[-1]) / 2)
    current_bollinger = (current_price - mid) / mid
    BUY_THRESHOLD = 0.8 * ((bollinger_down[-1] - mid) / mid)
    SELL_THRESHOLD = 0.8 * ((bollinger_up[-1] - mid) / mid)
    return current_bollinger, current_price, BUY_THRESHOLD[0], SELL_THRESHOLD[0]

def getBestTicker(_print):
    bollinger_ratios = []
    bollingers = []
    prices = []
    for ticker in (tickers):
        b, p, BUY_THRESHOLD, SELL_THRESHOLD = calculateBollinger(ticker)
        if _print: 
            print(ticker)
            print('bollinger_index: {}'.format(b))
        bollingers.append(b)
        br = (b + 1) / (BUY_THRESHOLD + 1)
        bollinger_ratios.append(br)
        prices.append(p)
    current_bollinger = bollingers[bollinger_ratios.index(min(bollinger_ratios)[0])][0]
    current_price = prices[bollingers.index(current_bollinger)]
    current_ticker = tickers[bollingers.index(current_bollinger)]
    return current_ticker, current_price, current_bollinger

def placeBuyAlpacaOrder(ticker, amnt, price, orders, positions):
    return api.submit_order(symbol=ticker, qty=amnt, side='buy', type='limit', time_in_force='day', limit_price=price)    
        
def placeSellAlpacaOrder(ticker, amnt, price, limit_order, orders, positions):
    response = None
    
    if len(positions) > 0: # we own at least one stock
        if limit_order:
            response = api.submit_order(symbol=ticker, qty=amnt, side='sell', type='limit', time_in_force='day', limit_price=price)    
        else:
            response = api.submit_order(symbol=ticker, qty=amnt, side='sell', type='market', time_in_force='day')                
    else:
        print('We do not own this stock!')
    
    return response

def stop():
    print('Sorry, I lost too much of your hard-earned money (' + str(MAX_LOSS) + '% of your initial cash)... Quitting now')
    sys.exit()
    
def writeTradeToCSV(ticker, buy_price, sell_price, buy_imbalance, sell_imbalance): # only for testing and review
    profit = AMOUNT_TO_TRADE * (sell_price - buy_price)
    if not (profit == 0):
        gained = profit > 0
        with open('trades2.csv', 'a+', newline='') as file:
            writer = csv.writer(file)
            writer.writerow([ticker, buy_price, sell_price, profit, gained, buy_imbalance, sell_imbalance, datetime.today().strftime('%D')])

def getTime():
    CURRENT_HOUR = int(datetime.now(timezone('US/Eastern')).strftime('%H'))
    CURRENT_MIN = int(datetime.now(timezone('US/Eastern')).strftime('%M'))
    CURRENT_SEC = int(datetime.now(timezone('US/Eastern')).strftime('%S'))
    return CURRENT_HOUR, CURRENT_MIN, CURRENT_SEC

def setAccountVars():
    account = api.get_account()
    positions = api.list_positions()
    orders = api.list_orders()
    CASH = float(account.cash)
    EQUITY = float(account.equity)
    PROFIT = 0
    if (len(positions) > 0):
        for pos in positions:
            PROFIT += pos.unrealized_pl
    return positions, orders, CASH, EQUITY, PROFIT

def setSellVariables(position_info, BUY_IMBALANCE, current_bollinger):
    if position_info.status == 'FILLED':
        PROFIT = float(position_info.unrealized_pl)
        SELL_PRICE = float(position_info.market_value) / float(position_info.qty)
        BUY_PRICE = SELL_PRICE - PROFIT
        SELL_IMBALANCE = current_bollinger
        writeTradeToCSV(current_ticker, BUY_PRICE, SELL_PRICE, BUY_IMBALANCE, SELL_IMBALANCE)
        print('SOLD ' + str(AMOUNT_TO_TRADE) + ' SHARES OF ' + current_ticker + '! -----------> Sell price: $' + str(SELL_PRICE) + ', Profit Realized: $' + str(round(PROFIT, 3)))

def setBuyVariables(position_info, current_bollinger, current_ticker):
    PROFIT = float(position_info.unrealized_pl)
    print(position_info)
    BUY_PRICE = (float(position_info.avg_entry_price))
    BUY_BOLLINGER = current_bollinger
    print('BOUGHT ' + str(AMOUNT_TO_TRADE) + ' SHARES OF ' + current_ticker + '! -----------> Buy price: $' + str(BUY_PRICE) + ', Imbalance: ' + str(BUY_BOLLINGER))
    return BUY_BOLLINGER, PROFIT, BUY_PRICE


print('Orders open: ' + str(api.list_orders(status='open')))
print('Positions: ' + str(api.list_positions()))

# now go!
while(True):
    
    # get time
    CURRENT_HOUR, CURRENT_MIN, _ = getTime()
    
    # is it trading hours?
    while(((CURRENT_HOUR + (CURRENT_MIN / 60)) >= 9.5) & (CURRENT_HOUR < 16) & (datetime.today().weekday() != 5) & (datetime.today().weekday() != 6)):
        
        # get time
        CURRENT_HOUR, CURRENT_MIN, CURRENT_SEC = getTime()
        print(str(CURRENT_HOUR) + ':' + str(CURRENT_MIN) + ':' + str(CURRENT_SEC))
        
        # set account vars
        positions, orders, CASH, EQUITY, PROFIT = setAccountVars()
        OWN_STOCK = len(positions) > 0
        OUTSIDE_OF_TRADING_HOURS = False # this is used the first time the program exits the trading day loop                
        
        # calculate bollingers, return a list of possible tickers, find one with lowest bollinger
        current_ticker, current_price, current_bollinger = getBestTicker(False)
        
        # did it return correctly?
        if current_price <= 0:
            continue
                    
        # get profit and total max positions to buy
        if (OWN_STOCK):
            string = ', profit: $' + str(PROFIT)
        else:
            string = ''

        # check for market close
        if (CURRENT_HOUR == 15) & (CURRENT_MIN == 59): # last minute of trading day -> sell all positions
        
            # close all positions
            response = api.close_all_positions()
            CLIENT_ASSET_ID = response.asset_id
            positions, orders, CASH, EQUITY, PROFIT = setAccountVars()
            
            while (OWN_STOCK):
                time.sleep(1)
            
            # set sell vars
            sold_order = api.get_order(CLIENT_ASSET_ID)
            setSellVariables(sold_order, BUY_IMBALANCE, current_bollinger)
            
            CAN_BUY_TODAY = False;
            break
    
        print('ticker: ' + str(current_ticker) + ', bollinger index: ' + str(round(current_bollinger, 3)) + ', BUY THRESHOLD: ' + str(round(BUY_THRESHOLD, 3)) + ', OWN_STOCK: ' + str(OWN_STOCK) + ', price: $' + str(current_price) + string + ', total profit: $' + str(TOTAL_PROFIT))
        if (current_bollinger <= BUY_THRESHOLD): # bollinger close to -1 -> stock will probably go up -> buy
            while (AMOUNT_TO_TRADE * current_price > CASH): # about to over buy
                AMOUNT_TO_TRADE -= 1
                
            # buy now
            response = placeBuyAlpacaOrder(current_ticker, AMOUNT_TO_TRADE, current_price, orders, positions)
            
            # update info
            CLIENT_ASSET_ID = response.asset_id
            positions, orders, CASH, EQUITY, PROFIT = setAccountVars() 
            BUY_BOLLINGER = setBuyVariables(api.get_position(current_ticker), current_bollinger)
            print('BOUGHT ' + str(AMOUNT_TO_TRADE) + ' SHARES OF ' + current_ticker + '! -----------> Buy price: $' + str(BUY_PRICE) + ', Bollinger index: ' + str(round(current_bollinger, 3)))
            
        elif ((current_bollinger >= SELL_THRESHOLD) | (abs(PROFIT) > 0.5)) & OWN_STOCK: # stock will probably go down -> sell
            # sell now
            response = placeSellAlpacaOrder(current_ticker, AMOUNT_TO_TRADE, current_price, True, orders, positions)
            # update info
            CLIENT_ASSET_ID = response.asset_id
            positions, orders, CASH, EQUITY,  PROFIT = setAccountVars()
            setSellVariables(response, BUY_IMBALANCE, current_bollinger)

        if ((current_bollinger >= SELL_THRESHOLD) & (not OWN_STOCK) & (len(orders) > 0)): # try to cancel order if I ordered but haven't bought yet - the stock doesn't look so appealing anymore
            api.cancel_all_orders()
            
            # update info
            CLIENT_ASSET_ID = response.asset_id
            positions, orders, CASH, EQUITY,  PROFIT = setAccountVars()
            
        if (PROFIT <= (MAX_LOSS * -1 * EQUITY)): # lost too much today :( ===> hold positions and stop the trading loop
            
            # update info
            CLIENT_ASSET_ID = response.asset_id
            positions, orders, CASH, EQUITY, PROFIT = setAccountVars()
            
            stop()
            break # break out of trading loop

        # update total profit
        account = api.get_account()
        TOTAL_PROFIT = float(account.equity) - float(account.last_equity)
                
        # avoid too many api calls
        time.sleep(DELAY)
        
    if (TOTAL_PROFIT <= (MAX_LOSS * -1)): # lost too much today :( ===> hold and stop the trading loop
        
        # update info
        CLIENT_ASSET_ID = response.asset_id
        positions, orders, CASH, EQUITY, PROFIT = setAccountVars()

        stop()
        break # leave trading loop
        
    if (not OUTSIDE_OF_TRADING_HOURS):
        day_string = ' tomorrow.'
        
        if (datetime.today().weekday() == 5):
            day_string = ' on the next elligible weekday.'
            
        print('Outside of trading hours - I will resume my money making' + day_string)
        OUTSIDE_OF_TRADING_HOURS = True


















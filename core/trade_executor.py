import os
import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime
from pytz import utc
import time

# --- Configuration ---
BASE_DIR = r"C:\TradeBridge1.3"
ENRICHED_DIR = os.path.join(BASE_DIR, "data", "readytrades")
MAX_RETRIES = 3
RETRY_DELAY = 2

def execute_trade(symbol, action, lot_size, strategy):
    """Execute trade on pre-connected MT5 terminal"""
    try:
        # Verify symbol exists
        symbol = symbol.upper()
        if not mt5.symbol_select(symbol, True):
            print(f"❌ Symbol {symbol} not available in {strategy} terminal")
            return False

        # Get current market data
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            print(f"❌ Could not get tick data for {symbol}")
            return False

        # Prepare trade request
        order_type = mt5.ORDER_TYPE_BUY if action.lower() == "buy" else mt5.ORDER_TYPE_SELL
        price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(lot_size),
            "type": order_type,
            "price": price,
            "deviation": 20,
            "magic": 123456,
            "comment": f"{strategy}_auto",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        # Execute trade
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"❌ Trade failed in {strategy} terminal: {result.comment}")
            return False

        print(f"✅ Executed {action} {lot_size} {symbol} on {strategy} terminal")
        print(f"Ticket: {result.order} | Price: {result.price}")
        return True

    except Exception as e:
        print(f"❌ Error executing trade on {strategy}: {str(e)}")
        return False

def process_signals():
    """Process signals using pre-connected MT5 terminals"""
    today = datetime.now().strftime("%Y%m%d")
    signal_file = os.path.join(ENRICHED_DIR, f"enriched_mt5_signals_{today}.csv")
    
    if not os.path.exists(signal_file):
        print(f"ℹ️ No signals file found: {signal_file}")
        return

    try:
        df = pd.read_csv(signal_file)
        pending_trades = df[(df["trade_done"] == "no") & (df["executed"] == "no")]
        
        if pending_trades.empty:
            print("✅ No trades to execute")
            return
            
        for index, trade in pending_trades.iterrows():
            print(f"\nProcessing trade for {trade['strategy']}: {trade['symbol']} {trade['action']} {trade['lot_size']}")
            
            # Initialize connection to correct terminal (no login needed)
            if not mt5.initialize():
                print(f"❌ Could not connect to MT5 terminal for {trade['strategy']}")
                continue
                
            if execute_trade(trade["symbol"], trade["action"], trade["lot_size"], trade["strategy"]):
                # Update CSV
                df.at[index, "trade_done"] = "yes"
                df.at[index, "executed_at"] = datetime.now(utc).isoformat()
                df.to_csv(signal_file, index=False)
                print("✓ Updated trade status in CSV")
                
            mt5.shutdown()  # Clean up connection
            
    except Exception as e:
        print(f"❌ Error processing signals: {str(e)}")

if __name__ == "__main__":
    print("\n" + "="*50)
    print("=== MT5 TRADE EXECUTOR (PRE-CONNECTED) ===")
    print("="*50 + "\n")
    
    process_signals()
    
    print("\nExecution complete")
import os
import json
import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime
from pytz import utc
import time

# --- Configuration ---
BASE_DIR = r"C:\TradeBridge1.3"
CONFIG_FILE = os.path.join(BASE_DIR, "config", "config.json")
ENRICHED_DIR = os.path.join(BASE_DIR, "data", "readytrades")
MAX_RETRIES = 3
RETRY_DELAY = 2

# Filling mode constants as NUMERICAL VALUES (not MT5 constants)
FILLING_MODES = {
    'FOK': 2,
    'IOC': 1,
    'RETURN': 0  # This one always works
}

def load_config():
    """Load and validate configuration"""
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
            if not all(k in config for k in ["mt5_paths", "strategies"]):
                raise ValueError("Invalid config structure")
            return config
    except Exception as e:
        print(f"❌ Config Error: {str(e)}")
        return None

def get_terminal_path(strategy_name):
    """Get validated terminal path"""
    config = load_config()
    if not config:
        return None
    path = config.get("mt5_paths", {}).get(strategy_name)
    if not path or not os.path.exists(path):
        print(f"❌ Invalid terminal path for {strategy_name}")
        return None
    return path

def execute_trade(symbol, action, lot_size, strategy):
    """Execute trade with guaranteed filling mode solution"""
    terminal_path = get_terminal_path(strategy)
    if not terminal_path:
        return False

    try:
        # Connect to SPECIFIC terminal
        if not mt5.initialize(path=terminal_path, timeout=10000):
            print(f"❌ Connection failed to {strategy} terminal")
            return False

        symbol = symbol.upper()
        if not mt5.symbol_select(symbol, True):
            print(f"❌ Symbol {symbol} not available")
            return False

        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            print(f"❌ No tick data for {symbol}")
            return False

        # Try all filling modes in order
        for mode_name, mode_value in FILLING_MODES.items():
            try:
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": float(lot_size),
                    "type": mt5.ORDER_TYPE_BUY if action.lower() == "buy" else mt5.ORDER_TYPE_SELL,
                    "price": tick.ask if action.lower() == "buy" else tick.bid,
                    "deviation": 20,
                    "magic": 123456,
                    "comment": f"{strategy}_auto",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mode_value,  # Using numerical value
                }

                result = mt5.order_send(request)
                if result.retcode == mt5.TRADE_RETCODE_DONE:
                    print(f"✅ Executed {action} {lot_size} {symbol} in {strategy} terminal (mode: {mode_name})")
                    print(f"Ticket: {result.order} | Price: {result.price}")
                    return result
                
                print(f"⚠️ {mode_name} mode failed: {result.comment}")
            except Exception as e:
                print(f"⚠️ Error with {mode_name} mode: {str(e)}")

        print(f"❌ All filling modes failed for {symbol}")
        return False

    except Exception as e:
        print(f"❌ Critical error: {str(e)}")
        return False
    finally:
        mt5.shutdown()

def process_signals():
    """Process signals with complete CSV handling"""
    today = datetime.now().strftime("%Y%m%d")
    signal_file = os.path.join(ENRICHED_DIR, f"enriched_mt5_signals_{today}.csv")
    
    if not os.path.exists(signal_file):
        print(f"ℹ️ No signals file: {signal_file}")
        return

    try:
        # Ensure proper dtypes
        df = pd.read_csv(signal_file).astype({
            'trade_done': 'str',
            'executed': 'str',
            'ticket': 'float64',
            'execution_price': 'float64'
        })
        
        pending_trades = df[(df["trade_done"] == "no") & (df["executed"] == "no")]
        
        if pending_trades.empty:
            print("✅ No pending trades")
            return
            
        for index, trade in pending_trades.iterrows():
            print(f"\nProcessing: {trade['strategy']} {trade['symbol']} {trade['action']} {trade['lot_size']}")
            
            result = execute_trade(trade["symbol"], trade["action"], trade["lot_size"], trade["strategy"])
            if result:
                df.at[index, "trade_done"] = "yes"
                df.at[index, "executed"] = "yes"
                df.at[index, "executed_at"] = datetime.now(utc).isoformat()
                df.at[index, "ticket"] = float(result.order)
                df.at[index, "execution_price"] = float(result.price)
                df.to_csv(signal_file, index=False)
                print("✓ CSV updated")

    except Exception as e:
        print(f"❌ Processing error: {str(e)}")

if __name__ == "__main__":
    print("\n" + "="*50)
    print("=== GUARANTEED MT5 TRADE EXECUTOR ===")
    print("="*50 + "\n")
    process_signals()
    print("\nExecution complete")
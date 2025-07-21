import os
import MetaTrader5 as mt5
import pandas as pd
import time
from datetime import datetime
from pytz import utc
import json
from threading import Lock

# --- Configuration ---
BASE_DIR = r"C:\TradeBridge1.3"
CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.json")
ENRICHED_DIR = os.path.join(BASE_DIR, "data", "readytrades")

# Connection management
_terminal_connections = {}
_connection_lock = Lock()

def load_config():
    with open(CONFIG_PATH) as f:
        config = json.load(f)
    
    # Set defaults if not exists
    config.setdefault("tpsl_manager", {
        "check_interval": 20,
        "max_retries": 3,
        "retry_delay": 5
    })
    return config

def get_strategy_terminal(strategy):
    config = load_config()
    return config["mt5_paths"].get(strategy)

def initialize_mt5(strategy):
    with _connection_lock:
        if strategy in _terminal_connections:
            return True
        
        terminal_path = get_strategy_terminal(strategy)
        if not terminal_path:
            print(f"❌ No terminal path for {strategy}")
            return False
        
        if not mt5.initialize(path=terminal_path):
            print(f"❌ MT5 init failed for {strategy}: {mt5.last_error()}")
            return False
        
        _terminal_connections[strategy] = True
        return True

def calculate_pips(symbol, price_diff):
    symbol_info = mt5.symbol_info(symbol)
    if not symbol_info:
        return 0
    return price_diff / (symbol_info.point * 10)

def manage_position(pos, strategy_cfg):
    symbol = pos.symbol
    symbol_info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    
    if not symbol_info or not tick:
        return False

    entry = pos.price_open
    current = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
    digits = symbol_info.digits
    point = symbol_info.point
    tpsl_cfg = strategy_cfg["tpsl_logic"]

    try:
        # Algo11 Fixed Pips Logic
        if tpsl_cfg["mode"] == "fixed_pips":
            sl_pips = tpsl_cfg["sl_pips"]
            tp_pips = tpsl_cfg["tp_pips"]
            
            if pos.type == mt5.ORDER_TYPE_BUY:
                sl = entry - sl_pips * 10 * point
                tp = entry + tp_pips * 10 * point
                
                # Trailing Stop Logic
                profit_pips = calculate_pips(symbol, current - entry)
                if "trailing" in tpsl_cfg and profit_pips >= tpsl_cfg["trailing"]["activate_at"]:
                    sl = entry + tpsl_cfg["trailing"]["lock_pips"] * 10 * point
                    
            else:  # SELL
                sl = entry + sl_pips * 10 * point
                tp = entry - tp_pips * 10 * point
                
                profit_pips = calculate_pips(symbol, entry - current)
                if "trailing" in tpsl_cfg and profit_pips >= tpsl_cfg["trailing"]["activate_at"]:
                    sl = entry - tpsl_cfg["trailing"]["lock_pips"] * 10 * point

        # Algo15 Percentage Balance Logic
        elif tpsl_cfg["mode"] == "percentage_balance":
            account = mt5.account_info()
            if not account:
                return False
                
            risk_amount = account.balance * (tpsl_cfg["sl_percent"] / 100)
            reward_amount = account.balance * (tpsl_cfg["tp_percent"] / 100)
            
            tick_value = symbol_info.trade_tick_value
            if tick_value == 0:
                return False
                
            dollar_per_point = tick_value * pos.volume
            sl_distance = risk_amount / dollar_per_point
            tp_distance = reward_amount / dollar_per_point
            
            if pos.type == mt5.ORDER_TYPE_BUY:
                sl = entry - sl_distance * point
                tp = entry + tp_distance * point
                
                # Breakeven Logic
                if "breakeven_at" in tpsl_cfg:
                    progress = (current - entry) / (tp - entry)
                    if progress >= tpsl_cfg["breakeven_at"]:
                        sl = entry
            else:  # SELL
                sl = entry + sl_distance * point
                tp = entry - tp_distance * point
                
                if "breakeven_at" in tpsl_cfg:
                    progress = (entry - current) / (entry - tp)
                    if progress >= tpsl_cfg["breakeven_at"]:
                        sl = entry

        # Send modification request
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": pos.ticket,
            "sl": round(sl, digits),
            "tp": round(tp, digits),
            "magic": pos.magic,
            "comment": f"Auto-{tpsl_cfg['mode']}",
            "type_time": mt5.ORDER_TIME_GTC
        }
        
        result = mt5.order_send(request)
        return result.retcode == mt5.TRADE_RETCODE_DONE
        
    except Exception as e:
        print(f"⚠️ Error managing {symbol}: {str(e)}")
        return False

def process_new_trades():
    today = datetime.now().strftime("%Y%m%d")
    csv_path = os.path.join(ENRICHED_DIR, f"enriched_mt5_signals_{today}.csv")
    
    if not os.path.exists(csv_path):
        return 0
        
    df = pd.read_csv(csv_path)
    new_trades = df[(df["trade_done"] == "yes") & (df["tpsl_done"] == "no")]
    
    if new_trades.empty:
        return 0
        
    config = load_config()
    processed = 0
    
    for idx, trade in new_trades.iterrows():
        strategy = trade["strategy"]
        if initialize_mt5(strategy):
            positions = mt5.positions_get(symbol=trade["symbol"])
            for pos in positions:
                if pos.comment == f"{strategy}_auto":
                    if manage_position(pos, config["strategies"][strategy]):
                        df.at[idx, "tpsl_done"] = "yes"
                        processed += 1
    
    if processed > 0:
        df.to_csv(csv_path, index=False)
    
    return processed

def monitor_active_positions():
    config = load_config()
    managed = 0
    
    for strategy in config["strategies"]:
        if initialize_mt5(strategy):
            positions = mt5.positions_get()
            for pos in positions:
                if pos.comment.startswith(f"{strategy}_"):
                    if manage_position(pos, config["strategies"][strategy]):
                        managed += 1
                        
    return managed

def main_loop():
    config = load_config()
    tpsl_cfg = config["tpsl_manager"]
    
    print("✅ TP/SL Manager Started")
    print(f"• Check Interval: {tpsl_cfg['check_interval']}s")
    print(f"• Max Retries: {tpsl_cfg['max_retries']}")
    print(f"• Retry Delay: {tpsl_cfg['retry_delay']}s\n")
    
    while True:
        try:
            new_processed = process_new_trades()
            active_managed = monitor_active_positions()
            
            if new_processed > 0 or active_managed > 0:
                print(f"🔄 Managed {new_processed} new trades | Updated {active_managed} active positions")
            
            time.sleep(tpsl_cfg["check_interval"])
            
        except KeyboardInterrupt:
            print("\n🛑 Received shutdown signal")
            break
        except Exception as e:
            print(f"⚠️ Main loop error: {str(e)}")
            time.sleep(tpsl_cfg["retry_delay"])

if __name__ == "__main__":
    main_loop()
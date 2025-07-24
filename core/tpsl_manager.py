import os
import MetaTrader5 as mt5
import time
from datetime import datetime
import json
from threading import Lock, Event
import math
import logging
import signal

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

# --- Configuration ---
BASE_DIR = r"C:\TradeBridge1.3"
CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.json")

# Global control
shutdown_flag = Event()
_terminal_connections = {}
_connection_lock = Lock()
_position_cache = {}  # Track recently modified positions

def safe_json_load(path):
    """Safe JSON loading with validation and retries"""
    for attempt in range(3):
        try:
            with open(path, 'r') as f:
                config = json.load(f)
                if "strategies" not in config or "mt5_paths" not in config:
                    raise ValueError("Invalid config structure")
                return config
        except Exception as e:
            logging.warning(f"Config load failed (attempt {attempt+1}): {str(e)}")
            time.sleep(1)
    raise RuntimeError("Failed to load config after multiple attempts")

def initialize_mt5(strategy, config):
    """Thread-safe MT5 initialization with proper cleanup"""
    with _connection_lock:
        if shutdown_flag.is_set():
            return False

        terminal_path = config["mt5_paths"].get(strategy)
        if not terminal_path or not os.path.exists(terminal_path):
            logging.error(f"Invalid terminal path for {strategy}")
            return False

        # Get credentials from strategy config
        creds = config["strategies"][strategy].get("mt5_credentials", {})

        # Initialize with isolated settings
        if not mt5.initialize(
            path=terminal_path,
            login=creds.get("login", 0),
            password=creds.get("password", ""),
            server=creds.get("server", ""),
            timeout=10000,
            portable=False
        ):
            logging.error(f"MT5 init failed for {strategy}: {mt5.last_error()}")
            return False

        logging.info(f"Connected to {strategy} terminal")
        _terminal_connections[strategy] = True
        return True

def shutdown_mt5():
    """Safely shutdown MT5 connection"""
    try:
        mt5.shutdown()
    except Exception as e:
        logging.error(f"Error shutting down MT5: {str(e)}")

def calculate_pip_value(symbol):
    """Precise pip calculation with symbol validation"""
    if not mt5.symbol_select(symbol, True):
        return None

    symbol_info = mt5.symbol_info(symbol)
    if not symbol_info:
        return None

    point = symbol_info.point
    # Adjust pip value based on instrument type
    if symbol.startswith(('XAU', 'XAG')):  # Metals
        return point * 1
    elif symbol.startswith(('BTC', 'ETH')):  # Crypto
        return point * 1
    return point * 10  # Forex

def should_skip_position(position, strategy):
    """Determine if we should skip modifying this position"""
    cache_key = f"{strategy}_{position.ticket}"
    
    # Skip recently modified positions
    if cache_key in _position_cache:
        if time.time() - _position_cache[cache_key] < 60:  # 60 second cooldown
            return True
        del _position_cache[cache_key]
    
    # Skip positions newer than 30 seconds
    position_time = datetime.fromtimestamp(position.time) if isinstance(position.time, int) else position.time
    position_age = (datetime.now() - position_time).total_seconds()
    return position_age < 30

def manage_position(position, strategy, config):
    """Enhanced position management with coordination"""
    try:
        symbol = position.symbol
        cache_key = f"{strategy}_{position.ticket}"
        
        if should_skip_position(position, strategy):
            return False

        strategy_cfg = config["strategies"][strategy]
        tpsl_cfg = strategy_cfg["tpsl_logic"]

        # Get market data with validation
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return False

        current_price = tick.bid if position.type == mt5.ORDER_TYPE_BUY else tick.ask
        entry_price = position.price_open
        symbol_info = mt5.symbol_info(symbol)
        
        if not symbol_info:
            return False
            
        point = symbol_info.point
        digits = symbol_info.digits
        pip_value = calculate_pip_value(symbol)
        
        if not pip_value:
            return False

        # Initialize with current values
        sl_price = position.sl
        tp_price = position.tp

        # --- Fixed Pips Strategy ---
        if tpsl_cfg["mode"] == "fixed_pips":
            sl_pips = float(tpsl_cfg["sl_pips"])
            tp_pips = float(tpsl_cfg["tp_pips"])
            
            if position.type == mt5.ORDER_TYPE_BUY:
                sl_price = max(entry_price - (sl_pips * pip_value), 0.00001)
                tp_price = entry_price + (tp_pips * pip_value)
                
                if "trailing" in tpsl_cfg:
                    profit_pips = (current_price - entry_price) / pip_value
                    activate_at = float(tpsl_cfg["trailing"]["activate_at"])
                    lock_pips = float(tpsl_cfg["trailing"]["lock_pips"])
                    
                    if profit_pips >= activate_at:
                        new_sl = entry_price + (lock_pips * pip_value)
                        sl_price = max(sl_price, new_sl)
            else:  # SELL
                sl_price = entry_price + (sl_pips * pip_value)
                tp_price = max(entry_price - (tp_pips * pip_value), 0.00001)
                
                if "trailing" in tpsl_cfg:
                    profit_pips = (entry_price - current_price) / pip_value
                    if profit_pips >= activate_at:
                        new_sl = entry_price - (lock_pips * pip_value)
                        sl_price = min(sl_price, new_sl)

        # --- Percentage Balance Strategy ---
        elif tpsl_cfg["mode"] == "percentage_balance":
            account = mt5.account_info()
            if not account:
                return False
                
            risk_amount = account.balance * (float(tpsl_cfg["sl_percent"]) / 100)
            reward_amount = account.balance * (float(tpsl_cfg["tp_percent"]) / 100)
            
            tick_value = symbol_info.trade_tick_value_profit
            if tick_value == 0:
                return False
                
            dollar_per_point = tick_value * position.volume
            sl_distance = risk_amount / dollar_per_point
            tp_distance = reward_amount / dollar_per_point
            
            if position.type == mt5.ORDER_TYPE_BUY:
                sl_price = max(entry_price - (sl_distance * point), 0.00001)
                tp_price = entry_price + (tp_distance * point)
            else:  # SELL
                sl_price = entry_price + (sl_distance * point)
                tp_price = max(entry_price - (tp_distance * point), 0.00001)

        # Validate and round prices
        try:
            sl_price = round(float(sl_price), digits)
            tp_price = round(float(tp_price), digits)
        except (TypeError, ValueError):
            return False

        # Skip if changes are insignificant
        if (math.isclose(sl_price, position.sl, abs_tol=point*5) and 
            math.isclose(tp_price, position.tp, abs_tol=point*5)):
            return False

        # Prepare modification request
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": position.ticket,
            "sl": sl_price,
            "tp": tp_price,
            "symbol": symbol,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK
        }
        
        # Send with retries
        max_retries = config["tpsl_manager"]["max_retries"]
        for attempt in range(max_retries):
            if shutdown_flag.is_set():
                return False
                
            result = mt5.order_send(request)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                _position_cache[cache_key] = time.time()  # Track modification time
                logging.info(f"Updated {strategy} {symbol} | SL: {sl_price} | TP: {tp_price}")
                return True
            time.sleep(config["tpsl_manager"]["retry_delay"])
        
        logging.warning(f"Failed to modify {symbol} after {max_retries} attempts")
        return False

    except Exception as e:
        logging.error(f"Error managing {symbol}: {str(e)}")
        return False

def process_strategy(strategy, config):
    """Process all positions for a strategy with coordination"""
    try:
        if shutdown_flag.is_set():
            return 0
            
        if not initialize_mt5(strategy, config):
            return 0
            
        positions = mt5.positions_get()
        if positions is None:
            return 0
            
        allowed_symbols = config["strategies"][strategy]["allowed_symbols"]
        managed = 0
        
        for pos in positions:
            if pos.symbol in allowed_symbols:
                if manage_position(pos, strategy, config):
                    managed += 1
                    
        return managed
        
    except Exception as e:
        logging.error(f"Error processing {strategy}: {str(e)}")
        return 0
    finally:
        shutdown_mt5()

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logging.info("Shutdown signal received")
    shutdown_flag.set()

def main_loop():
    """Main execution loop with comprehensive error handling"""
    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        config = safe_json_load(CONFIG_PATH)
        tpsl_cfg = config["tpsl_manager"]
        
        logging.info("\n=== TP/SL MANAGER STARTED ===\n")
        
        while not shutdown_flag.is_set():
            try:
                total_managed = 0
                for strategy in config["strategies"]:
                    if shutdown_flag.is_set():
                        break
                        
                    if config["strategies"][strategy]["enabled"]:
                        logging.debug(f"Checking {strategy} positions...")
                        total_managed += process_strategy(strategy, config)
                
                if total_managed > 0:
                    logging.info(f"Updated {total_managed} positions")
                
                # Dynamic sleep that can be interrupted
                for _ in range(tpsl_cfg["check_interval"] * 10):
                    if shutdown_flag.is_set():
                        break
                    time.sleep(0.1)
                    
            except Exception as e:
                logging.error(f"Main loop error: {str(e)}")
                if not shutdown_flag.is_set():
                    time.sleep(tpsl_cfg["retry_delay"])
                
    finally:
        logging.info("Shutting down...")
        shutdown_mt5()
        logging.info("TP/SL manager stopped")

if __name__ == "__main__":
    main_loop()
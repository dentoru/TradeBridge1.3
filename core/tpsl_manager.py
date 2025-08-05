import os
import MetaTrader5 as mt5
import time
from datetime import datetime
import json
import logging
from threading import Lock, Event
import math
from pytz import utc
import signal

# --- Configuration ---
BASE_DIR = r"C:\TradeBridge1.3"
LOG_DIR = os.path.join(BASE_DIR, "logs")
CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.json")

# Setup logging
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, f"tpsl_manager_{datetime.now().strftime('%Y%m%d')}.log")),
    ]
)
logger = logging.getLogger()

# Global control
shutdown_flag = Event()
_terminal_connections = {}
_connection_lock = Lock()
_position_cache = {}

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
            logger.error(f"Config load failed (attempt {attempt+1}): {str(e)}")
            time.sleep(1)
    raise RuntimeError("Failed to load config after multiple attempts")

def initialize_mt5(strategy, config):
    """Thread-safe MT5 initialization"""
    with _connection_lock:
        if shutdown_flag.is_set():
            return False

        terminal_path = config["mt5_paths"].get(strategy)
        if not terminal_path or not os.path.exists(terminal_path):
            logger.error(f"Invalid terminal path for {strategy}")
            return False

        creds = config["strategies"][strategy].get("mt5_credentials", {})
        if not mt5.initialize(
            path=terminal_path,
            login=creds.get("login", 0),
            password=creds.get("password", ""),
            server=creds.get("server", ""),
            timeout=10000,
            portable=False
        ):
            logger.error(f"MT5 init failed for {strategy}: {mt5.last_error()}")
            return False

        _terminal_connections[strategy] = True
        return True

def shutdown_mt5():
    """Safely shutdown MT5 connection"""
    try:
        mt5.shutdown()
    except Exception as e:
        logger.error(f"Error shutting down MT5: {str(e)}")

def calculate_pip_value(symbol):
    """Precise pip calculation with symbol validation"""
    if not mt5.symbol_select(symbol, True):
        return None
    symbol_info = mt5.symbol_info(symbol)
    if not symbol_info:
        return None
    point = symbol_info.point
    return point * (1 if symbol.startswith(('XAU', 'XAG', 'BTC', 'ETH')) else 10)

def should_skip_position(position, strategy):
    """Determine if we should skip modifying this position"""
    cache_key = f"{strategy}_{position.ticket}"
    if cache_key in _position_cache and time.time() - _position_cache[cache_key] < 60:
        return True
    if cache_key in _position_cache:
        del _position_cache[cache_key]
    position_time = datetime.fromtimestamp(position.time) if isinstance(position.time, int) else position.time
    return (datetime.now() - position_time).total_seconds() < 30

def manage_position(position, strategy, config):
    """Enhanced position management"""
    try:
        symbol = position.symbol
        cache_key = f"{strategy}_{position.ticket}"
        
        if should_skip_position(position, strategy):
            return False

        strategy_cfg = config["strategies"][strategy]
        tpsl_cfg = strategy_cfg["tpsl_logic"]
        tick = mt5.symbol_info_tick(symbol)
        symbol_info = mt5.symbol_info(symbol)
        
        if not tick or not symbol_info:
            return False

        current_price = tick.bid if position.type == mt5.ORDER_TYPE_BUY else tick.ask
        point = symbol_info.point
        digits = symbol_info.digits

        # Calculate SL/TP based on strategy
        if tpsl_cfg["mode"] == "fixed_pips":
            sl_pips = float(tpsl_cfg["sl_pips"])
            tp_pips = float(tpsl_cfg["tp_pips"])
            pip_value = calculate_pip_value(symbol)
            
            if position.type == mt5.ORDER_TYPE_BUY:
                sl_price = max(position.price_open - (sl_pips * pip_value), 0.00001)
                tp_price = position.price_open + (tp_pips * pip_value)
                if "trailing" in tpsl_cfg:
                    profit_pips = (current_price - position.price_open) / pip_value
                    if profit_pips >= float(tpsl_cfg["trailing"]["activate_at"]):
                        sl_price = max(sl_price, position.price_open + 
                                     (float(tpsl_cfg["trailing"]["lock_pips"]) * pip_value))
            else:  # SELL
                sl_price = position.price_open + (sl_pips * pip_value)
                tp_price = max(position.price_open - (tp_pips * pip_value), 0.00001)
                if "trailing" in tpsl_cfg:
                    profit_pips = (position.price_open - current_price) / pip_value
                    if profit_pips >= float(tpsl_cfg["trailing"]["activate_at"]):
                        sl_price = min(sl_price, position.price_open - 
                                     (float(tpsl_cfg["trailing"]["lock_pips"]) * pip_value))

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
                sl_price = max(position.price_open - (sl_distance * point), 0.00001)
                tp_price = position.price_open + (tp_distance * point)
            else:  # SELL
                sl_price = position.price_open + (sl_distance * point)
                tp_price = max(position.price_open - (tp_distance * point), 0.00001)

        # Skip if changes are insignificant
        if (math.isclose(sl_price, position.sl, abs_tol=point*5) and 
            math.isclose(tp_price, position.tp, abs_tol=point*5)):
            return False

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": position.ticket,
            "sl": round(float(sl_price), digits),
            "tp": round(float(tp_price), digits),
            "symbol": symbol,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK
        }
        
        for attempt in range(config["tpsl_manager"]["max_retries"]):
            if shutdown_flag.is_set():
                return False
            result = mt5.order_send(request)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                _position_cache[cache_key] = time.time()
                logger.info(f"Updated {strategy} {symbol} | SL: {sl_price:.5f} | TP: {tp_price:.5f}")
                return True
            time.sleep(config["tpsl_manager"]["retry_delay"])
        
        logger.warning(f"Failed to modify {symbol} after {config['tpsl_manager']['max_retries']} attempts")
        return False

    except Exception as e:
        logger.error(f"Error managing {symbol}: {str(e)}", exc_info=True)
        return False

def process_strategy(strategy, config):
    """Process all positions for a strategy"""
    try:
        if shutdown_flag.is_set() or not initialize_mt5(strategy, config):
            return 0
            
        positions = mt5.positions_get()
        if positions is None:
            return 0
            
        return sum(1 for pos in positions 
                 if pos.symbol in config["strategies"][strategy]["allowed_symbols"]
                 and manage_position(pos, strategy, config))
        
    except Exception as e:
        logger.error(f"Error processing {strategy}: {str(e)}", exc_info=True)
        return 0
    finally:
        shutdown_mt5()

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info("Shutdown signal received")
    shutdown_flag.set()

def main_loop():
    """Main execution loop"""
    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        config = safe_json_load(CONFIG_PATH)
        tpsl_cfg = config["tpsl_manager"]
        logger.info("=== TP/SL MANAGER STARTED ===")
        
        while not shutdown_flag.is_set():
            try:
                total_managed = sum(
                    process_strategy(strategy, config)
                    for strategy in config["strategies"]
                    if config["strategies"][strategy]["enabled"]
                )
                
                if total_managed > 0:
                    logger.info(f"Updated {total_managed} positions")
                
                for _ in range(tpsl_cfg["check_interval"] * 10):
                    if shutdown_flag.is_set():
                        break
                    time.sleep(0.1)
                    
            except Exception as e:
                logger.error(f"Main loop error: {str(e)}", exc_info=True)
                if not shutdown_flag.is_set():
                    time.sleep(tpsl_cfg["retry_delay"])
                
    finally:
        logger.info("Shutting down...")
        shutdown_mt5()
        logger.info("TP/SL manager stopped")

if __name__ == "__main__":
    main_loop()
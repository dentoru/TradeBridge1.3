import os
import MetaTrader5 as mt5
import time
from datetime import datetime
import json
from threading import Lock
import math
import logging

# Configure logging to handle Unicode characters in Windows
class UnicodeStreamHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            stream.write(msg.encode('utf-8').decode('utf-8') + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(__file__), 'tpsl_manager.log'), encoding='utf-8'),
        UnicodeStreamHandler()
    ]
)

# --- Configuration ---
BASE_DIR = r"C:\TradeBridge1.3"
CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.json")

# Connection management
_terminal_connections = {}
_connection_lock = Lock()

def load_config():
    """Load configuration with validation"""
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            config = json.load(f)
        
        # Set defaults
        config.setdefault("tpsl_manager", {
            "check_interval": 20,
            "max_retries": 3,
            "retry_delay": 5
        })
        
        # Validate strategy configs
        for strategy in config.get("strategies", {}):
            if "tpsl_logic" not in config["strategies"][strategy]:
                logging.error(f"Missing tpsl_logic in {strategy} config")
                raise ValueError(f"Invalid config for {strategy}")
                
        return config
    except Exception as e:
        logging.error(f"Config load failed: {str(e)}")
        raise

def initialize_mt5(strategy):
    """Initialize MT5 connection with robust error handling"""
    with _connection_lock:
        if strategy in _terminal_connections:
            return True
        
        try:
            config = load_config()
            terminal_path = config["mt5_paths"][strategy]
            
            if not mt5.initialize(
                path=terminal_path,
                timeout=10000,
                portable=False
            ):
                error = mt5.last_error()
                logging.error(f"MT5 init failed for {strategy}: {error}")
                return False
                
            logging.info(f"[SUCCESS] MT5 connected for {strategy}")
            _terminal_connections[strategy] = True
            return True
            
        except Exception as e:
            logging.error(f"Connection error for {strategy}: {str(e)}")
            return False

def get_pip_value(symbol):
    """Get precise pip value with symbol validation"""
    try:
        if not mt5.symbol_select(symbol, True):
            logging.warning(f"Symbol {symbol} not available")
            return None
            
        symbol_info = mt5.symbol_info(symbol)
        point = symbol_info.point
        
        # Handle different instrument types
        if symbol.startswith(('XAU', 'XAG')):  # Metals
            return point * 1
        elif symbol.startswith(('BTC', 'ETH')):  # Crypto
            return point * 1
        else:  # Forex
            return point * 10
            
    except Exception as e:
        logging.error(f"Pip value error for {symbol}: {str(e)}")
        return None

def manage_position(position, strategy):
    """Enhanced position management with variable initialization fix"""
    try:
        config = load_config()
        strategy_cfg = config["strategies"][strategy]
        tpsl_cfg = strategy_cfg["tpsl_logic"]
        symbol = position.symbol
        
        # Initialize default values
        sl_price = position.sl  # Default to current SL
        tp_price = position.tp  # Default to current TP
        
        # Convert MT5 time to datetime object
        position_time = datetime.fromtimestamp(position.time) if isinstance(position.time, int) else position.time
        
        # Skip positions newer than 1 minute
        position_age = (datetime.now() - position_time).total_seconds() / 60
        if position_age < 1:
            logging.debug(f"Skipping new position ({position_age:.1f} minutes old)")
            return False

        # Validate market data
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            logging.warning(f"No tick data for {symbol}")
            return False

        current_price = tick.bid if position.type == mt5.ORDER_TYPE_BUY else tick.ask
        entry_price = position.price_open
        pip_value = get_pip_value(symbol)
        
        if not pip_value:
            return False
            
        point = mt5.symbol_info(symbol).point
        digits = mt5.symbol_info(symbol).digits

        # --- Fixed Pips Strategy ---
        if tpsl_cfg["mode"] == "fixed_pips":
            sl_pips = float(tpsl_cfg["sl_pips"])
            tp_pips = float(tpsl_cfg["tp_pips"])
            
            # Calculate SL/TP prices
            if position.type == mt5.ORDER_TYPE_BUY:
                sl_price = entry_price - (sl_pips * pip_value)
                tp_price = entry_price + (tp_pips * pip_value)
                
                # Trailing stop logic
                if "trailing" in tpsl_cfg:
                    profit_pips = (current_price - entry_price) / pip_value
                    activate_at = float(tpsl_cfg["trailing"]["activate_at"])
                    lock_pips = float(tpsl_cfg["trailing"]["lock_pips"])
                    
                    if profit_pips >= activate_at:
                        new_sl = entry_price + (lock_pips * pip_value)
                        sl_price = max(sl_price, new_sl)
                        logging.info(f"[TRAILING] SL activated for {symbol} at {sl_price:.5f}")
                        
            else:  # SELL
                sl_price = entry_price + (sl_pips * pip_value)
                tp_price = entry_price - (tp_pips * pip_value)
                
                if "trailing" in tpsl_cfg:
                    profit_pips = (entry_price - current_price) / pip_value
                    activate_at = float(tpsl_cfg["trailing"]["activate_at"])
                    lock_pips = float(tpsl_cfg["trailing"]["lock_pips"])
                    
                    if profit_pips >= activate_at:
                        new_sl = entry_price - (lock_pips * pip_value)
                        sl_price = min(sl_price, new_sl)
                        logging.info(f"[TRAILING] SL activated for {symbol} at {sl_price:.5f}")

        # --- Percentage Balance Strategy ---
        elif tpsl_cfg["mode"] == "percentage_balance":
            account = mt5.account_info()
            if not account:
                return False
                
            risk_amount = account.balance * (tpsl_cfg["sl_percent"] / 100)
            reward_amount = account.balance * (tpsl_cfg["tp_percent"] / 100)
            
            tick_value = mt5.symbol_info(symbol).trade_tick_value_profit
            if tick_value == 0:
                return False
                
            dollar_per_point = tick_value * position.volume
            sl_distance = risk_amount / dollar_per_point
            tp_distance = reward_amount / dollar_per_point
            
            if position.type == mt5.ORDER_TYPE_BUY:
                sl_price = entry_price - (sl_distance * point)
                tp_price = entry_price + (tp_distance * point)
            else:  # SELL
                sl_price = entry_price + (sl_distance * point)
                tp_price = entry_price - (tp_distance * point)

        # Round to correct precision
        sl_price = round(sl_price, digits)
        tp_price = round(tp_price, digits)

        # Skip if changes are insignificant
        if (math.isclose(sl_price, position.sl, abs_tol=point*5) and 
            math.isclose(tp_price, position.tp, abs_tol=point*5)):
            logging.debug(f"No TP/SL changes needed for {symbol}")
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
        
        # Send order with retries
        max_retries = config["tpsl_manager"]["max_retries"]
        for attempt in range(max_retries):
            result = mt5.order_send(request)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logging.info(f"[SUCCESS] {strategy} {symbol} | SL: {sl_price} | TP: {tp_price}")
                return True
                
            logging.warning(f"Attempt {attempt+1} failed: {result.comment}")
            time.sleep(config["tpsl_manager"]["retry_delay"])
        
        logging.error(f"Failed to modify {symbol} after {max_retries} attempts")
        return False

    except Exception as e:
        logging.error(f"Position management error: {str(e)}", exc_info=True)
        return False

def process_strategy(strategy):
    """Process positions for a strategy with connection handling"""
    try:
        if not initialize_mt5(strategy):
            return 0
            
        positions = mt5.positions_get()
        if positions is None:
            logging.warning(f"No positions for {strategy} or error: {mt5.last_error()}")
            return 0
            
        config = load_config()
        allowed_symbols = config["strategies"][strategy]["allowed_symbols"]
        managed = 0
        
        for pos in positions:
            if pos.symbol in allowed_symbols:
                if manage_position(pos, strategy):
                    managed += 1
                    
        return managed
        
    except Exception as e:
        logging.error(f"Strategy processing failed: {str(e)}")
        return 0

def main_loop():
    """Main execution loop with error resilience"""
    try:
        config = load_config()
        tpsl_cfg = config["tpsl_manager"]
        
        logging.info("\n" + "="*50)
        logging.info("=== TP/SL MANAGER STARTED ===")
        logging.info("="*50 + "\n")
        
        while True:
            try:
                total_managed = 0
                for strategy in config["strategies"]:
                    if config["strategies"][strategy]["enabled"]:
                        logging.info(f"Checking {strategy} positions...")
                        total_managed += process_strategy(strategy)
                
                if total_managed > 0:
                    logging.info(f"Updated {total_managed} positions")
                else:
                    logging.debug("No position updates needed")
                
                time.sleep(tpsl_cfg["check_interval"])
                
            except KeyboardInterrupt:
                logging.info("\n[STOP] Shutdown requested")
                break
            except Exception as e:
                logging.error(f"Loop error: {str(e)}")
                time.sleep(tpsl_cfg["retry_delay"])
                
    finally:
        # Cleanup connections
        for strategy in list(_terminal_connections.keys()):
            mt5.shutdown()
            logging.info(f"Closed MT5 connection for {strategy}")
        logging.info("TP/SL manager stopped")

if __name__ == "__main__":
    main_loop()
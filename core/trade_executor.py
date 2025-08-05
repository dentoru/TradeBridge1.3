import os
import json
import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime
from pytz import utc
import time
import logging
from typing import List, Optional

# --- Configuration ---
BASE_DIR = r"C:\TradeBridge1.3"
LOG_DIR = os.path.join(BASE_DIR, "logs")
CONFIG_FILE = os.path.join(BASE_DIR, "config", "config.json")
ENRICHED_DIR = os.path.join(BASE_DIR, "data", "readytrades")
MAX_RETRIES = 3
RETRY_DELAY = 2
MAGIC_NUMBER = 123456

# Setup logging
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, f"trade_executor_{datetime.now().strftime('%Y%m%d')}.log"))
    ]
)
logger = logging.getLogger()

# Filling mode constants
FILLING_MODES = {
    'FOK': mt5.ORDER_FILLING_FOK,
    'IOC': mt5.ORDER_FILLING_IOC,
    'RETURN': mt5.ORDER_FILLING_RETURN
}

class TradeResult:
    """Custom trade result class for netting mode"""
    def __init__(self, symbol, strategy):
        self.retcode = mt5.TRADE_RETCODE_DONE
        self.order = 0
        self.symbol = symbol
        self.comment = f"{strategy}_netting_complete"

def load_config():
    """Load and validate configuration"""
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
            if not all(k in config for k in ["mt5_paths", "strategies"]):
                raise ValueError("Invalid config structure")
            return config
    except Exception as e:
        logger.error(f"Config Error: {str(e)}")
        return None

def get_terminal_path(strategy_name):
    """Get validated terminal path"""
    config = load_config()
    if not config:
        return None
    path = config.get("mt5_paths", {}).get(strategy_name)
    if not path or not os.path.exists(path):
        logger.error(f"Invalid terminal path for {strategy_name}")
        return None
    return path

def get_open_positions(symbol: str, strategy: str, magic_restriction: bool = True) -> List[mt5.TradePosition]:
    """Get all open positions for a symbol"""
    try:
        positions = mt5.positions_get(symbol=symbol)
        if positions is None:
            return []
        if magic_restriction:
            return [p for p in positions if p.magic == MAGIC_NUMBER]
        return list(positions)
    except Exception as e:
        logger.error(f"Error getting positions: {str(e)}")
        return []

def close_positions(positions: List[mt5.TradePosition], strategy: str) -> bool:
    """Close multiple positions"""
    if not positions:
        return True
        
    terminal_path = get_terminal_path(strategy)
    if not terminal_path:
        return False

    try:
        if not mt5.initialize(path=terminal_path, timeout=10000):
            logger.error(f"Connection failed to {strategy} terminal")
            return False

        results = []
        for position in positions:
            tick = mt5.symbol_info_tick(position.symbol)
            if not tick:
                logger.error(f"No tick data for {position.symbol}")
                continue

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "position": position.ticket,
                "symbol": position.symbol,
                "volume": position.volume,
                "type": mt5.ORDER_TYPE_SELL if position.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                "price": tick.bid if position.type == mt5.ORDER_TYPE_BUY else tick.ask,
                "deviation": 20,
                "magic": MAGIC_NUMBER,
                "comment": f"{strategy}_close_reverse",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_FOK,
            }
            result = mt5.order_send(request)
            results.append(result)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"Closed {position.symbol} position (Ticket: {position.ticket})")
            else:
                logger.warning(f"Failed to close {position.symbol} position: {result.comment}")

        return all(r.retcode == mt5.TRADE_RETCODE_DONE for r in results if r is not None)
    except Exception as e:
        logger.error(f"Error closing positions: {str(e)}")
        return False
    finally:
        mt5.shutdown()

def execute_trade(symbol: str, action: str, lot_size: float, strategy: str) -> Optional[mt5.OrderSendResult]:
    """Execute trade with reverse handling logic"""
    config = load_config()
    if not config:
        return None
        
    strategy_cfg = config["strategies"].get(strategy, {})
    reverse_cfg = strategy_cfg.get("reverse_handling", {"mode": "off", "magic_restriction": True})
    
    terminal_path = get_terminal_path(strategy)
    if not terminal_path:
        return None

    try:
        if not mt5.initialize(path=terminal_path, timeout=10000):
            logger.error(f"Connection failed to {strategy} terminal")
            return None

        symbol = symbol.upper()
        if not mt5.symbol_select(symbol, True):
            logger.error(f"Symbol {symbol} not available")
            return None

        # Check for reverse positions if mode isn't "off"
        if reverse_cfg["mode"] != "off":
            positions = get_open_positions(
                symbol, 
                strategy, 
                reverse_cfg["magic_restriction"]
            )
            
            opposite_positions = [
                p for p in positions 
                if (action.lower() == "buy" and p.type == mt5.ORDER_TYPE_SELL) or
                   (action.lower() == "sell" and p.type == mt5.ORDER_TYPE_BUY)
            ]
            
            if opposite_positions:
                logger.info(f"Found {len(opposite_positions)} opposite positions - handling reverse ({reverse_cfg['mode']} mode)")
                if not close_positions(opposite_positions, strategy):
                    logger.error("Failed to close opposite positions")
                    return None
                
                if reverse_cfg["mode"] == "netting":
                    logger.info("Netting complete - no new position opened")
                    return TradeResult(symbol, strategy)

        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            logger.error(f"No tick data for {symbol}")
            return None

        for mode_name, mode_value in FILLING_MODES.items():
            try:
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": float(lot_size),
                    "type": mt5.ORDER_TYPE_BUY if action.lower() == "buy" else mt5.ORDER_TYPE_SELL,
                    "price": tick.ask if action.lower() == "buy" else tick.bid,
                    "deviation": 20,
                    "magic": MAGIC_NUMBER,
                    "comment": f"{strategy}_auto",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mode_value,
                }

                result = mt5.order_send(request)
                if result.retcode == mt5.TRADE_RETCODE_DONE:
                    logger.info(f"Executed {action} {lot_size} {symbol} (Ticket: {result.order})")
                    return result
                
                logger.warning(f"{mode_name} mode failed: {result.comment}")
            except Exception as e:
                logger.warning(f"Error with {mode_name} mode: {str(e)}")

        logger.error(f"All filling modes failed for {symbol}")
        return None

    except Exception as e:
        logger.error(f"Critical error: {str(e)}")
        return None
    finally:
        mt5.shutdown()

def process_signals():
    """Process signals with complete CSV handling"""
    today = datetime.now().strftime("%Y%m%d")
    signal_file = os.path.join(ENRICHED_DIR, f"enriched_mt5_signals_{today}.csv")
    
    if not os.path.exists(signal_file):
        logger.info("No signals file found")
        return

    try:
        df = pd.read_csv(signal_file)
        df['trade_done'] = df['trade_done'].astype(str)
        df['executed'] = df['executed'].astype(str)
        df['ticket'] = pd.to_numeric(df['ticket'], errors='coerce')
        df['execution_price'] = pd.to_numeric(df['execution_price'], errors='coerce')
        
        pending_trades = df[(df["trade_done"] == "no") & (df["executed"] == "no")]
        
        if pending_trades.empty:
            logger.info("No pending trades to execute")
            return
            
        for index, trade in pending_trades.iterrows():
            logger.info(f"Processing: {trade['strategy']} {trade['symbol']} {trade['action']} {trade['lot_size']}")
            
            result = execute_trade(trade["symbol"], trade["action"], trade["lot_size"], trade["strategy"])
            if result:
                df.at[index, "trade_done"] = "yes"
                df.at[index, "executed"] = "yes"
                df.at[index, "executed_at"] = datetime.now(utc).isoformat()
                if hasattr(result, 'order') and result.order != 0:
                    df.at[index, "ticket"] = float(result.order)
                    df.at[index, "execution_price"] = float(result.price)
                df.to_csv(signal_file, index=False)
                logger.info("Trade logged to CSV")

    except Exception as e:
        logger.error(f"Processing error: {str(e)}")

if __name__ == "__main__":
    process_signals()
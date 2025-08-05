import pandas as pd
import datetime as dt
import os
import json
import logging
from pytz import utc
from utils import get_balance, load_config
import MetaTrader5 as mt5

# ===== CONFIGURATION =====
BASE_DIR = r"C:\TradeBridge1.3"
CORE_DIR = os.path.join(BASE_DIR, "core")
LOG_DIR = os.path.join(BASE_DIR, "logs")
SIGNAL_DIR = os.path.join(BASE_DIR, "data", "mt5_signals")
ENRICHED_DIR = os.path.join(BASE_DIR, "data", "readytrades")
LEVERAGE = 500  # 1:500 leverage

# Setup logging
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    filename=os.path.join(LOG_DIR, f"trade_parser_{dt.datetime.now().strftime('%Y%m%d')}.log"),
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger()

def ensure_dir(path):
    """Ensure output directory exists"""
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        logger.error(f"Directory creation failed: {str(e)}")

def load_signals():
    """Load today's signals with validation"""
    today = dt.datetime.now().strftime("%Y%m%d")
    signal_file = os.path.join(SIGNAL_DIR, f"mt5_signals_{today}.csv")
    
    if not os.path.exists(signal_file):
        logger.info(f"No signals file found: {signal_file}")
        return pd.DataFrame()

    try:
        df = pd.read_csv(signal_file, header=0, names=[
            "timestamp", "symbol", "action", "timeframe", "strategy", "executed"
        ])
        df["timestamp"] = pd.to_datetime(
            df["timestamp"],
            format='%Y-%m-%dT%H:%M:%S.%f',
            utc=True,
            errors='coerce'
        )
        return df[df["timestamp"].notna() & (df["executed"] == "no")]
    except Exception as e:
        logger.error(f"Signal loading error: {str(e)}")
        return pd.DataFrame()

def is_recent(signal_time):
    """Check if signal is within 1 minute window"""
    return (dt.datetime.now(utc) - signal_time) <= dt.timedelta(minutes=1)

def validate_signal(signal, strategy_cfg):
    """Validate against strategy rules"""
    if not strategy_cfg.get("enabled", False):
        return "yes (strg:off)"
    
    action = str(signal["action"]).lower()
    symbol = str(signal["symbol"]).upper()
    
    if action not in [a.lower() for a in strategy_cfg.get("allowed_actions", [])]:
        return "yes (err:action)"
    if symbol not in [s.upper() for s in strategy_cfg.get("allowed_symbols", [])]:
        return "yes (err:symbol)"
    return "no"

def calculate_lotsize(symbol, strategy_cfg, strategy_name):
    """Calculate lot size using persistent connection"""
    try:
        # Fixed lot strategy
        if strategy_cfg["lot_size"]["type"] == "fixed":
            return round(float(strategy_cfg["lot_size"]["value"]), 2)
            
        # Percentage-based strategy
        balance = get_balance(strategy_name)
        logger.info(f"[{strategy_name}] Account Balance: ${balance:.2f} (Leverage 1:{LEVERAGE})")
        
        symbol = str(symbol).upper()
        if not mt5.symbol_select(symbol, True):
            raise ValueError(f"Symbol {symbol} not available")
            
        tick = mt5.symbol_info_tick(symbol)
        
        # Instrument-specific settings
        if symbol.startswith(('XAU', 'XAG')):  # Metals
            price = tick.ask
            contract_size = 100
        elif symbol.startswith(('BTC', 'ETH')):  # Crypto
            price = tick.last if tick.last > 0 else tick.ask
            contract_size = 1.0
        else:  # Forex
            price = tick.ask
            contract_size = 100000
        
        # Risk calculation
        risk_pct = strategy_cfg["lot_size"]["value"] / 100
        risk_amount = (balance * LEVERAGE) * risk_pct
        
        # Core calculation
        lot_size = risk_amount / (price * contract_size)
        
        # Apply constraints
        min_lot = 0.00001 if symbol.startswith(('BTC', 'ETH')) else 0.01
        final_lot = max(min(round(lot_size, 2), 100), min_lot)
        
        logger.info(f"""
        [{strategy_name}] LOT SIZE:
        Symbol: {symbol}
        Price: {price:.5f}
        Contract Size: {contract_size}
        Risk Amount: ${risk_amount:.2f}
        Final Lot: {final_lot:.2f}
        """)
        
        return final_lot
        
    except Exception as e:
        logger.error(f"[{strategy_name}] Lot calc error: {str(e)}")
        return 0.01  # Fallback

def process_signals():
    """Main processing pipeline"""
    try:
        config = load_config()
        signals = load_signals()
        
        if signals.empty:
            logger.info("No valid signals to process")
            return None
            
        enriched_data = []
        processed_count = 0
        
        for _, signal in signals.iterrows():
            strategy = str(signal["strategy"])
            status = "no"
            lot_size = ""
            tpsl_mode = ""
            
            if not is_recent(signal["timestamp"]):
                status = "yes (1min+)"
            else:
                if strategy not in config["strategies"]:
                    status = "yes (err:strategy)"
                else:
                    strategy_cfg = config["strategies"][strategy]
                    status = validate_signal(signal, strategy_cfg)
                    
                    if status == "no":
                        lot_size = calculate_lotsize(
                            signal["symbol"],
                            strategy_cfg,
                            strategy
                        )
                        tpsl_mode = strategy_cfg.get("tpsl_logic", {}).get("mode", "")
                        processed_count += 1
            
            enriched_data.append({
                "timestamp": signal["timestamp"].isoformat(),
                "symbol": str(signal["symbol"]).upper(),
                "action": str(signal["action"]).lower(),
                "timeframe": str(signal["timeframe"]),
                "strategy": strategy,
                "executed": status,
                "lot_size": f"{float(lot_size):.2f}" if lot_size else "",
                "tpsl_mode": tpsl_mode,
                "trade_done": "no",
                "tpsl_done": "no",
                "ticket": "",
                "execution_price": "",
                "executed_at": "",
                "processed_at": dt.datetime.now(utc).isoformat()
            })
        
        logger.info(f"Processed {processed_count} new signals")
        return pd.DataFrame(enriched_data)
        
    except Exception as e:
        logger.error(f"Fatal processing error: {str(e)}", exc_info=True)
        return None

def save_and_mark_processed(df):
    """Save results and mark signals as processed"""
    if df is None or df.empty:
        logger.info("No data to save")
        return False
        
    try:
        ensure_dir(ENRICHED_DIR)
        today = dt.datetime.now().strftime("%Y%m%d")
        enriched_file = os.path.join(ENRICHED_DIR, f"enriched_mt5_signals_{today}.csv")
        
        existing_data = pd.DataFrame()
        if os.path.exists(enriched_file):
            existing_data = pd.read_csv(enriched_file)
        
        combined_data = pd.concat([existing_data, df], ignore_index=True)
        combined_data = combined_data.drop_duplicates(
            subset=["timestamp", "symbol", "strategy"],
            keep="last"
        )
        
        combined_data.to_csv(enriched_file, index=False)
        logger.info(f"Saved {len(df)} new signals (Total in file: {len(combined_data)})")
            
        signal_file = os.path.join(SIGNAL_DIR, f"mt5_signals_{today}.csv")
        if os.path.exists(signal_file):
            original = pd.read_csv(signal_file)
            original.loc[original["executed"] == "no", "executed"] = "yes"
            original.to_csv(signal_file, index=False)
            
        return True
        
    except Exception as e:
        logger.error(f"Failed to save output: {str(e)}", exc_info=True)
        return False

if __name__ == "__main__":
    print("\n" + "="*50)
    print("=== TRADE BRIDGE PARSER v1.7 - SILENT MODE ===")
    print("="*50 + "\n")
    
    if not mt5.initialize():
        logger.error("MT5 initialization failed for symbol data")
    
    try:
        enriched_df = process_signals()
        if enriched_df is not None:
            save_and_mark_processed(enriched_df)
    except Exception as e:
        logger.error(f"Main execution error: {str(e)}", exc_info=True)
    finally:
        mt5.shutdown()
        logger.info("Processing complete")
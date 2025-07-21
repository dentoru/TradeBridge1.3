import pandas as pd
import datetime as dt
import os
import json
from pytz import utc
from utils import get_balance, load_config  # Uses persistent connections

# ===== CONFIGURATION =====
BASE_DIR = r"C:\TradeBridge1.3"
SIGNAL_DIR = os.path.join(BASE_DIR, "data", "mt5_signals")
ENRICHED_DIR = os.path.join(BASE_DIR, "data", "readytrades")
LEVERAGE = 500  # 1:500 leverage

def ensure_dir(path):
    """Ensure output directory exists"""
    os.makedirs(path, exist_ok=True)

def load_signals():
    """Load today's signals with validation"""
    today = dt.datetime.now().strftime("%Y%m%d")
    signal_file = os.path.join(SIGNAL_DIR, f"mt5_signals_{today}.csv")
    
    if not os.path.exists(signal_file):
        print(f"ℹ️ No signals file found: {signal_file}")
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
        print(f"❌ Signal loading error: {str(e)}")
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
            return round(float(strategy_cfg["lot_size"]["value"]), 2)  # Round to 2 decimal places
            
        # Percentage-based strategy
        balance = get_balance(strategy_name)  # From persistent connection
        print(f"💰 [{strategy_name}] Account Balance: ${balance:.2f} (Leverage 1:{LEVERAGE})")
        
        # Get symbol info
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
        
        # Apply constraints and round to 2 decimal places
        min_lot = 0.00001 if symbol.startswith(('BTC', 'ETH')) else 0.01
        final_lot = max(min(round(lot_size, 2), 100), min_lot)  # Round to 2 decimal places
        
        print(f"""
        📊 [{strategy_name}] LOT SIZE:
        Symbol: {symbol}
        Price: {price:.5f}
        Contract Size: {contract_size}
        Risk Amount: ${risk_amount:.2f}
        Final Lot: {final_lot:.2f}
        """)
        
        return final_lot
        
    except Exception as e:
        print(f"⚠️ [{strategy_name}] Lot calc error: {str(e)}")
        return 0.01  # Fallback

def process_signals():
    """Main processing pipeline"""
    try:
        config = load_config()
        signals = load_signals()
        
        if signals.empty:
            print("✅ No valid signals to process")
            return None
            
        enriched_data = []
        
        for _, signal in signals.iterrows():
            strategy = str(signal["strategy"])
            status = "no"
            lot_size = ""
            tpsl_logic = ""
            
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
                        tpsl_logic = strategy_cfg.get("tpsl_logic", "")
            
            enriched_data.append({
                "timestamp": signal["timestamp"].isoformat(),
                "symbol": str(signal["symbol"]).upper(),
                "action": str(signal["action"]).lower(),
                "timeframe": str(signal["timeframe"]),
                "strategy": strategy,
                "executed": status,
                "lot_size": f"{float(lot_size):.2f}" if lot_size else "",  # Format to 2 decimal places
                "tpsl_logic": tpsl_logic,
                "trade_done": "no",
                "tpsl_done": "no",
                "processed_at": dt.datetime.now(utc).isoformat()
            })
        
        return pd.DataFrame(enriched_data)
        
    except Exception as e:
        print(f"❌ Fatal processing error: {str(e)}")
        return None

def save_and_mark_processed(df):
    """Save results and mark signals as processed"""
    if df is None or df.empty:
        return False
        
    try:
        ensure_dir(ENRICHED_DIR)
        today = dt.datetime.now().strftime("%Y%m%d")
        
        # Save enriched signals - append to existing file if it exists
        enriched_file = os.path.join(ENRICHED_DIR, f"enriched_mt5_signals_{today}.csv")
        
        # Get current timestamp for the file (only once per run)
        file_timestamp = dt.datetime.now(utc).isoformat()
        
        if os.path.exists(enriched_file):
            # Read existing data and append new signals
            existing_df = pd.read_csv(enriched_file)
            combined_df = pd.concat([existing_df, df], ignore_index=True)
            combined_df.to_csv(enriched_file, index=False)
        else:
            df.to_csv(enriched_file, index=False)
            
        # Add timestamp file
        timestamp_file = os.path.join(ENRICHED_DIR, f"enriched_mt5_signals_{today}_timestamp.txt")
        with open(timestamp_file, 'w') as f:
            f.write(file_timestamp)
            
        # Mark originals as processed
        signal_file = os.path.join(SIGNAL_DIR, f"mt5_signals_{today}.csv")
        if os.path.exists(signal_file):
            original = pd.read_csv(signal_file)
            original.loc[original["executed"] == "no", "executed"] = "yes"
            original.to_csv(signal_file, index=False)
            
        print(f"✅ Saved {len(df)} signals to {enriched_file}")
        return True
        
    except Exception as e:
        print(f"❌ Failed to save output: {str(e)}")
        return False

if __name__ == "__main__":
    print("\n" + "="*50)
    print("=== TRADE BRIDGE PARSER v1.5 - LEVERAGE EDITION ===")
    print("="*50 + "\n")
    
    # Initialize MT5 (only needed for symbol info)
    import MetaTrader5 as mt5
    if not mt5.initialize():
        print("❌ MT5 initialization failed for symbol data")
    
    try:
        enriched_df = process_signals()
        
        if enriched_df is not None and not enriched_df.empty:
            save_and_mark_processed(enriched_df)
        else:
            print("ℹ️ No signals were processed")
    finally:
        mt5.shutdown()  # Only closes the symbol info connection
    
    print("\n" + "="*50)
    print("=== PROCESSING COMPLETE ===")
    print("="*50 + "\n")
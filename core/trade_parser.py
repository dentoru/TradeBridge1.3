import os
import json
import time
import pandas as pd
from datetime import datetime, timedelta
import MetaTrader5 as mt5

# --- Define Paths ---
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Up from /core
CONFIG_PATH = os.path.join(ROOT_DIR, "config", "config.json")
SIGNALS_PATH = os.path.join(ROOT_DIR, "data", "mt5_signals", f"mt5_signals_{datetime.now().strftime('%Y%m%d')}.csv")
PROCESSED_PATH = os.path.join(ROOT_DIR, "data", "parsed_signals.csv")

# --- Load Config ---
with open(CONFIG_PATH, "r") as f:
    CONFIG = json.load(f)

def load_signals():
    try:
        df = pd.read_csv(SIGNALS_PATH)
    except FileNotFoundError:
        print(f"⚠️ Signals file not found: {SIGNALS_PATH}")
        return pd.DataFrame()

    required_columns = ["timestamp", "symbol", "action", "timeframe", "strategy", "executed"]
    for col in required_columns:
        if col not in df.columns:
            print(f"❌ Missing column in signals file: '{col}'")
            return pd.DataFrame()

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    df = df[df["timestamp"].notna()]

    df = df[df["executed"] == "no"]
    df = df[df["timestamp"] >= pd.Timestamp.utcnow() - pd.Timedelta(minutes=1)]
    return df

def get_mt5_login(strategy):
    strat_path = os.path.join(ROOT_DIR, "strategies", strategy, "mt5_login.json")
    if not os.path.exists(strat_path):
        print(f"❌ No login file found for strategy '{strategy}'")
        return None
    with open(strat_path, "r") as f:
        return json.load(f)

def connect_mt5(login_info):
    mt5.initialize(login_info["path"])
    return mt5.login(
        login=int(login_info["login"]),
        password=login_info["password"],
        server=login_info["server"]
    )

def get_symbol_info(symbol):
    mt5.symbol_select(symbol, True)
    info = mt5.symbol_info(symbol)
    if not info:
        print(f"❌ Symbol info unavailable: {symbol}")
    return info

def calculate_lot_size(symbol, risk_setting, balance, sl_pips=100):
    if isinstance(risk_setting, (int, float)):
        return round(risk_setting, 2)

    risk_type = risk_setting.get("type")
    value = risk_setting.get("value", 0)
    if risk_type != "percent":
        return None

    risk_amount = balance * (value / 100)
    info = get_symbol_info(symbol)
    if not info:
        return None

    if "USD" in symbol.upper():
        pip_value = 10
    elif "XAU" in symbol:
        pip_value = 1
    elif "BTC" in symbol:
        pip_value = 0.1
    else:
        pip_value = 10

    lot = risk_amount / (pip_value * sl_pips)
    return round(max(lot, 0.01), 2)

def parse_signals():
    df = load_signals()
    if df.empty:
        print("⚠️ No recent valid unexecuted signals.")
        return

    parsed_rows = []

    for i, row in df.iterrows():
        try:
            strategy = row["strategy"]
            symbol = row["symbol"]
            direction = row["action"]
            timeframe = row["timeframe"]
            timestamp = row["timestamp"]
        except KeyError as e:
            print(f"❌ Skipping row {i} due to missing field: {e}")
            continue

        if strategy not in CONFIG["enabled_strategies"]:
            df.at[i, "executed"] = "yes (strg:off)"
            continue

        login_info = get_mt5_login(strategy)
        if not login_info or not connect_mt5(login_info):
            print(f"❌ Failed to init MT5 for {strategy}")
            continue

        acc_info = mt5.account_info()
        if not acc_info:
            print(f"❌ Cannot fetch account info for {strategy}")
            continue

        balance = acc_info.balance
        lot_setting = CONFIG["lot_sizes"].get(strategy)
        if lot_setting is None:
            print(f"❌ No lot size config for {strategy}")
            continue

        lot_size = calculate_lot_size(symbol, lot_setting, balance)
        if lot_size is None:
            print(f"❌ Lot size calc failed for {symbol} ({strategy})")
            continue

        parsed_rows.append({
            "timestamp": timestamp.isoformat(),
            "symbol": symbol,
            "direction": direction,
            "timeframe": timeframe,
            "strategy": strategy,
            "lot": lot_size
        })

        df.at[i, "executed"] = "yes"

    if parsed_rows:
        out_df = pd.DataFrame(parsed_rows)
        out_df.to_csv(PROCESSED_PATH, index=False)
        print(f"✅ Parsed {len(parsed_rows)} → {PROCESSED_PATH}")
    else:
        print("⚠️ No rows parsed.")

    df.to_csv(SIGNALS_PATH, index=False)

if __name__ == "__main__":
    parse_signals()

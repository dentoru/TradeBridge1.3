import csv
import json
from datetime import datetime, timedelta
import os

SIGNALS_DIR = 'data/mt5_signals'
CONFIG_PATH = 'config/config.json'
OUTPUT_LOG = 'logs/parsed_signals.csv'

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

def parse_signal_row(row):
    try:
        timestamp = datetime.fromisoformat(row['timestamp'])
        symbol = row['symbol']
        action = row['action'].lower()
        timeframe = row['timeframe']
        strategy = row['strategy']
        executed = row['executed'].strip().lower()
        return {
            "timestamp": timestamp,
            "symbol": symbol,
            "action": action,
            "timeframe": timeframe,
            "strategy": strategy,
            "executed": executed
        }
    except Exception as e:
        print(f"⚠️ Error parsing row: {row} → {e}")
        return None

def get_all_signal_files():
    files = []
    for file in os.listdir(SIGNALS_DIR):
        if file.endswith('.csv') and file.startswith('mt5_signals_'):
            files.append(os.path.join(SIGNALS_DIR, file))
    return sorted(files)

def filter_recent_unexecuted_signals(signals, minutes=2):
    now = datetime.utcnow()
    return [
        s for s in signals
        if s and s['executed'] == 'no' and (now - s['timestamp']) < timedelta(minutes=minutes)
    ]

def load_signals():
    signal_files = get_all_signal_files()
    all_signals = []

    for file in signal_files:
        with open(file, 'r') as f:
            reader = csv.DictReader(f, fieldnames=['timestamp', 'symbol', 'action', 'timeframe', 'strategy', 'executed'])
            for row in reader:
                signal = parse_signal_row(row)
                if signal:
                    all_signals.append(signal)
    return all_signals

def log_filtered_signals(signals):
    os.makedirs(os.path.dirname(OUTPUT_LOG), exist_ok=True)
    with open(OUTPUT_LOG, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=signals[0].keys())
        writer.writeheader()
        for signal in signals:
            writer.writerow(signal)

def main():
    config = load_config()
    strategy_list = config.get('mt5_paths', {}).keys()

    print("🔍 Loading all strategy signals...")
    signals = load_signals()
    
    print("📌 Filtering valid unexecuted signals...")
    filtered = filter_recent_unexecuted_signals(signals)

    print(f"✅ Found {len(filtered)} unexecuted signals in last 2 minutes.")
    if filtered:
        log_filtered_signals(filtered)
        print(f"📝 Saved to {OUTPUT_LOG}")

if __name__ == '__main__':
    main()

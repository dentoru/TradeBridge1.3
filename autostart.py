import subprocess
import threading
import time
import os
from datetime import datetime

# === CURRENT DIRECTORY ===
ROOT_DIR = os.getcwd()
TV_SERVER = os.path.join(ROOT_DIR, "tv_server.py")
TRADE_PARSER = os.path.join(ROOT_DIR, "trade_parser.py")
# TRADE_EXECUTOR = os.path.join(ROOT_DIR, "trade_executor.py")  # ⏳ Will be added later

SIGNALS_DIR = os.path.join(ROOT_DIR, "data", "signals")


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# === START TV SERVER ===
def start_tv_server():
    print(f"[{timestamp()}] 🛰️ Starting TradingView webhook server...")
    try:
        subprocess.Popen(["python", TV_SERVER], cwd=ROOT_DIR)
    except Exception as e:
        print(f"❌ Failed to start {TV_SERVER}: {str(e)}")


# === LOOP TRADE PARSER ===
def loop_trade_parser():
    print(f"[{timestamp()}] 🔄 Starting trade parser loop...")
    while True:
        os.system(f"python {TRADE_PARSER}")
        time.sleep(5)


# === MAIN ===
if __name__ == "__main__":
    print(f"\n=== 🚀 TradingBridge AutoStart Initialized ({timestamp()}) ===\n")
    os.makedirs(SIGNALS_DIR, exist_ok=True)

    # Start TradingView webhook server in background
    threading.Thread(target=start_tv_server, daemon=True).start()

    # Start Trade Parser loop (blocking)
    loop_trade_parser()

    # Future: add executor here
    # threading.Thread(target=loop_trade_executor, daemon=True).start()

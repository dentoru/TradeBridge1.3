import subprocess
import threading
import time
import sys
import os

# --- Paths ---
CORE_DIR = os.path.join(os.getcwd(), "core")
TV_SERVER = os.path.join(CORE_DIR, "tv_server.py")
TRADE_PARSER = os.path.join(CORE_DIR, "trade_parser.py")
TRADE_EXECUTOR = os.path.join(CORE_DIR, "trade_executor.py")

def run_tv_server():
    print("🚀 Starting tv_server.py...")
    subprocess.Popen([sys.executable, TV_SERVER])

def run_loop(script_path, name, interval=5):
    def loop():
        while True:
            try:
                print(f"🔁 Running {name}...")
                subprocess.run([sys.executable, script_path])
            except Exception as e:
                print(f"❌ Error running {name}: {e}")
            time.sleep(interval)
    thread = threading.Thread(target=loop, daemon=True)
    thread.start()

def run_once(script_path, name):
    try:
        print(f"✅ Running {name} once...")
        subprocess.run([sys.executable, script_path])
    except Exception as e:
        print(f"❌ Error running {name}: {e}")

if __name__ == "__main__":
    print("🌐 TradeBridge AutoStart Initiated")

    run_tv_server()                     # Start Flask server
    run_once(TRADE_EXECUTOR, "trade_executor.py")  # Launch MT5 terminals once
    run_loop(TRADE_PARSER, "trade_parser.py", interval=5)  # Repeat every 5 sec

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Shutting down TradeBridge.")
        sys.exit(0)

import subprocess
import threading
import time
import sys
import os
from core.utils import initialize_persistent_connection, shutdown_all  # Direct imports

# --- Paths ---
CORE_DIR = os.path.join(os.getcwd(), "core")
TV_SERVER = os.path.join(CORE_DIR, "tv_server.py")
TRADE_PARSER = os.path.join(CORE_DIR, "trade_parser.py")

def run_tv_server():
    print("🚀 Starting tv_server.py...")
    subprocess.Popen([sys.executable, TV_SERVER])

def run_parser_loop():
    """Run trade parser in main thread with interval"""
    while True:
        try:
            print("\n" + "="*40)
            print("=== PROCESSING SIGNALS ===")
            subprocess.run([sys.executable, TRADE_PARSER])
        except Exception as e:
            print(f"❌ Parser error: {e}")
        time.sleep(5)  # 5-second interval

def initialize_mt5():
    """Initialize MT5 connections in main process"""
    from core.utils import load_config
    config = load_config()
    for strategy in config["strategies"]:
        if config["strategies"][strategy]["enabled"]:
            initialize_persistent_connection(strategy)

if __name__ == "__main__":
    print("🌐 TradeBridge AutoStart Initiated")
    
    # Initialize MT5 FIRST in main process
    initialize_mt5()
    
    # Start other services
    run_tv_server()
    
    try:
        run_parser_loop()  # This will run indefinitely
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
        shutdown_all()
        sys.exit(0)
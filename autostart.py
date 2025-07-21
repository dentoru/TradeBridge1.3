import os
import sys
import subprocess
import threading
import time
from datetime import datetime
from pytz import utc
import signal

# Add project root to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- Paths ---
BASE_DIR = r"C:\TradeBridge1.3"
CORE_DIR = os.path.join(BASE_DIR, "core")
CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.json")

# --- Script Paths ---
TV_SERVER = os.path.join(CORE_DIR, "tv_server.py")
TRADE_PARSER = os.path.join(CORE_DIR, "trade_parser.py")
TRADE_EXECUTOR = os.path.join(CORE_DIR, "trade_executor.py")
TP_SL_MANAGER = os.path.join(CORE_DIR, "tpsl_manager.py")  # New component

# Global flag to control threads
running = True

def run_tv_server():
    """Start TV server in a subprocess"""
    try:
        print("🚀 Starting tv_server.py...")
        return subprocess.Popen([sys.executable, TV_SERVER])
    except Exception as e:
        print(f"❌ Failed to start TV server: {str(e)}")
        return None

def run_parser_loop():
    """Continuous parser loop"""
    while running:
        try:
            print("\n" + "="*40)
            print(f"=== PROCESSING SIGNALS [{datetime.now(utc).isoformat()}] ===")
            subprocess.run([sys.executable, TRADE_PARSER], check=True)
        except Exception as e:
            print(f"❌ Parser error: {e}")
        time.sleep(5)

def run_executor_loop():
    """Continuous executor loop"""
    while running:
        try:
            print("\n" + "="*40)
            print(f"=== EXECUTING TRADES [{datetime.now(utc).isoformat()}] ===")
            subprocess.run([sys.executable, TRADE_EXECUTOR], check=True)
        except Exception as e:
            print(f"❌ Executor error: {e}")
        time.sleep(3)

def run_tpsl_loop():
    """Continuous TP/SL management loop"""
    while running:
        try:
            print("\n" + "="*40)
            print(f"=== MANAGING TP/SL [{datetime.now(utc).isoformat()}] ===")
            subprocess.run([sys.executable, TP_SL_MANAGER], check=True)
        except Exception as e:
            print(f"❌ TP/SL manager error: {e}")
        time.sleep(10)  # Slightly longer interval for TP/SL checks

def shutdown_handler(signum, frame):
    global running
    print("\n🛑 Shutdown signal received")
    running = False

def main():
    global running
    
    print("\n" + "="*50)
    print("=== TRADEBRIDGE AUTOSTART v2.4 (WITH TP/SL) ===")
    print("="*50 + "\n")

    # Set up signal handlers
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    # Start TV server
    tv_process = run_tv_server()
    if not tv_process:
        sys.exit(1)

    try:
        # Start processing threads
        parser_thread = threading.Thread(target=run_parser_loop, daemon=True)
        executor_thread = threading.Thread(target=run_executor_loop, daemon=True)
        tpsl_thread = threading.Thread(target=run_tpsl_loop, daemon=True)  # New thread
        
        parser_thread.start()
        executor_thread.start()
        tpsl_thread.start()  # Start TP/SL manager
        
        print("\nSystem is running. Press Ctrl+C to shutdown...")
        print("Active components:")
        print("- Signal Parser")
        print("- Trade Executor")
        print("- TP/SL Manager")  # New component
        print("- TV Server")
        
        # Keep main thread alive
        while running:
            time.sleep(1)
            
    except KeyboardInterrupt:
        shutdown_handler(None, None)
    finally:
        running = False
        print("\nStopping all processes...")
        if tv_process:
            tv_process.terminate()
        print("Clean shutdown complete")

if __name__ == "__main__":
    main()
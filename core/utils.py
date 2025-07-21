import os
import json
import MetaTrader5 as mt5
from threading import Lock

# === GLOBAL CONNECTION TRACKER ===
_active_connections = {}
_lock = Lock()
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "config.json")

def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

def initialize_persistent_connection(strategy_name):
    """Force-create and maintain an MT5 connection"""
    with _lock:
        if strategy_name in _active_connections:
            return _active_connections[strategy_name]
        
        config = load_config()
        login_path = os.path.join(BASE_DIR, "strategies", strategy_name, "mt5_login.json")
        
        with open(login_path, "r") as f:
            creds = json.load(f)
        
        # Use terminal path from config.json if available
        terminal_path = config["mt5_paths"].get(strategy_name, creds["terminal"])
        
        if not mt5.initialize(
            path=terminal_path,
            login=creds["login"],
            password=creds["password"],
            server=creds["server"]
        ):
            raise ConnectionError(f"MT5 init failed: {mt5.last_error()}")
        
        print(f"✅ [{strategy_name}] PERSISTENT CONNECTION ESTABLISHED (Login: {creds['login']})")
        _active_connections[strategy_name] = True
        return True

def get_balance(strategy_name):
    """Get balance from persistent connection"""
    if strategy_name not in _active_connections:
        initialize_persistent_connection(strategy_name)
    
    account = mt5.account_info()
    if not account:
        raise ValueError("No account info")
    return account.balance

def shutdown_all():
    """Manually close connections"""
    with _lock:
        for name in list(_active_connections.keys()):
            mt5.shutdown()
            print(f"🛑 [{name}] Persistent connection closed")
            del _active_connections[name]

# Initialize on import if running directly
if __name__ == "__main__":
    config = load_config()
    for strategy in config["strategies"]:
        if config["strategies"][strategy]["enabled"]:
            initialize_persistent_connection(strategy)
    
    # Keep connections alive
    import time
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown_all()
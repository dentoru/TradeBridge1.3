import os
import json
import subprocess

# Resolve path to config.json (one level up from 'core/')
CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config", "config.json"))

# Load config
with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

# Get all MT5 strategy paths
mt5_paths = config.get("mt5_paths", {})

def launch_mt5_terminal(strategy, terminal_path):
    """Launch the MT5 terminal for a given strategy if enabled."""
    print(f"🔍 Checking strategy: {strategy}")

    strategy_config = config.get("strategies", {}).get(strategy, {})
    enabled = strategy_config.get("enabled", False)

    if not enabled:
        print(f"⛔ Strategy '{strategy}' is disabled. Skipping.")
        return

    if not os.path.exists(terminal_path):
        print(f"❌ Terminal path not found for '{strategy}': {terminal_path}")
        return

    try:
        subprocess.Popen([terminal_path])
        print(f"✅ Launched MT5 terminal for '{strategy}'")
    except Exception as e:
        print(f"❌ Failed to launch MT5 for '{strategy}': {e}")

# Loop through each strategy and try launching
if __name__ == "__main__":
    print("🚀 Starting MT5 terminals for enabled strategies...")

    for strategy, terminal_path in mt5_paths.items():
        launch_mt5_terminal(strategy, terminal_path)

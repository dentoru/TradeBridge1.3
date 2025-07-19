from flask import Flask, request, jsonify
import os
import csv
from datetime import datetime

app = Flask(__name__)

# Output directories
SIGNAL_DIR = "data"
MT5_DIR = os.path.join(SIGNAL_DIR, "mt5_signals")
CTRADER_DIR = os.path.join(SIGNAL_DIR, "ctrader_signals")

# Ensure output dirs exist
os.makedirs(MT5_DIR, exist_ok=True)
os.makedirs(CTRADER_DIR, exist_ok=True)

def write_signal(platform, data):
    now = datetime.now().isoformat()
    platform_dir = MT5_DIR if platform == "mt5" else CTRADER_DIR
    filename = os.path.join(platform_dir, f"{platform}_signals_{datetime.now().date()}.csv")
    
    # Check if file exists
    file_exists = os.path.isfile(filename)

    # Fields for CSV
    fields = ["timestamp", "symbol", "action", "timeframe", "strategy", "executed"]
    row = [now, data['symbol'], data['action'], data['timeframe'], data['strategy'], "no"]

    # Write to CSV
    with open(filename, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(fields)
        writer.writerow(row)

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        alert = request.json
        if not alert:
            return jsonify({"error": "Empty alert"}), 400

        # Required fields
        symbol = alert["symbol"]
        action = alert["action"]
        timeframe = alert["timeframe"]
        strategy = alert["strategy"]
        target = alert.get("target", "mt5").lower()

        # Handle multi-targets
        platforms = [p.strip() for p in target.split(",")]
        for p in platforms:
            if p in ("mt5", "ctrader"):
                write_signal(p, alert)

        print(f"✅ Signal received for {symbol} -> {target.upper()}")
        return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"⚠️ Error processing webhook: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)

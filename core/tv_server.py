from flask import Flask, request, jsonify
import os
import csv
from datetime import datetime

app = Flask(__name__)

# === CONFIG ===
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
VALID_TARGETS = ["mt5", "ctrader"]
PORT = 80


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def get_today_filepath(target):
    today = datetime.now().strftime("%Y%m%d")
    folder = os.path.join(DATA_DIR, f"{target}_signals")
    ensure_dir(folder)
    filename = f"{target}_signals_{today}.csv"
    return os.path.join(folder, filename)


def write_signal(target, row):
    filepath = get_today_filepath(target)
    file_exists = os.path.isfile(filepath)

    with open(filepath, mode="a", newline="") as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow(["timestamp", "symbol", "action", "timeframe", "strategy", "executed"])
        writer.writerow(row)


@app.route("/webhook", methods=["POST"])
def webhook():
    if not request.is_json:
        return jsonify({"error": "Expected JSON data"}), 400

    data = request.get_json()

    # Required keys
    required = {"symbol", "action", "timeframe", "target", "strategy"}
    if not required.issubset(data):
        return jsonify({"error": f"Missing required keys: {required - set(data)}"}), 400

    symbol = data["symbol"].strip().upper()
    action = data["action"].strip().lower()
    timeframe = data["timeframe"].strip()
    strategy = data["strategy"].strip()
    targets = [t.strip().lower() for t in data["target"].split(",") if t.strip().lower() in VALID_TARGETS]

    if not targets:
        return jsonify({"error": "No valid targets (mt5 or ctrader) specified"}), 400

    timestamp = datetime.now().isoformat()
    row = [timestamp, symbol, action, timeframe, strategy, "no"]

    for target in targets:
        try:
            write_signal(target, row)
            print(f"✅ Written to {target}: {row}")
        except Exception as e:
            print(f"❌ Error writing to {target}: {e}")
            return jsonify({"error": str(e)}), 500

    return jsonify({"status": "success", "targets": targets}), 200


if __name__ == "__main__":
    try:
        print(f"🚀 Starting Webhook Server on port {PORT}...")
        app.run(host="0.0.0.0", port=PORT)
    except PermissionError:
        print("❌ Run as Administrator or use a port above 1024.")
# main.py
from flask import Flask, request, jsonify
import hmac
import hashlib
import time
import requests
import os
import math
import functools

print = functools.partial(print, flush=True)
app = Flask(__name__)

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
BASE_URL = "https://api.binance.com"

# -------------------- Auxiliares -------------------- #

def sign_params(params, secret):
    """Firma parámetros para las peticiones de Binance"""
    query = "&".join([f"{k}={v}" for k, v in params.items()])
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()

def get_price(symbol):
    r = requests.get(f"{BASE_URL}/api/v3/ticker/price", params={"symbol": symbol})
    return float(r.json()["price"])

def get_balance(asset="USDC"):
    ts = int(time.time() * 1000)
    params = {"timestamp": ts}
    params["signature"] = sign_params(params, BINANCE_API_SECRET)
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    r = requests.get(f"{BASE_URL}/api/v3/account", headers=headers, params=params)
    data = r.json()
    bal = next((b for b in data.get("balances", []) if b["asset"] == asset), None)
    return float(bal["free"]) if bal else 0.0

def get_lot_info(symbol):
    r = requests.get(f"{BASE_URL}/api/v3/exchangeInfo", params={"symbol": symbol})
    data = r.json()
    s = next((x for x in data["symbols"] if x["symbol"] == symbol), None)
    if not s: return None
    lot = {}
    for f in s["filters"]:
        if f["filterType"] == "LOT_SIZE":
            lot["minQty"] = float(f["minQty"])
            lot["stepSize"] = float(f["stepSize"])
        if f["filterType"] == "PRICE_FILTER":
            lot["tickSize"] = float(f["tickSize"])
    return lot

def round_step(qty, step):
    return float(f"{math.floor(qty / step) * step:.8f}")

# -------------------- Webhook -------------------- #

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    print("📩 Webhook:", data)

    try:
        symbol = data["symbol"]
        side = data["side"].upper()            # BUY o SELL
        entry_price = float(data["entry_price"])
        sl = float(data["sl"])
        tp = float(data["tp"])
        pos_type = data["position_type"].upper()

        # --- Ignorar SELL ---
        if side == "SELL":
            print(f"⏭ SELL ignored in {symbol}")
            return jsonify({"status": f"Ignored SELL for {symbol}"}), 200

        # --- Solo procesar BUY ---
        usdc = get_balance("USDC")
        two_percent = usdc * 0.02
        if two_percent <= 0:
            return jsonify({"error": "No USDC balance"}), 400

        lot = get_lot_info(symbol)
        if not lot:
            return jsonify({"error": f"Lot info not found for {symbol}"}), 400

        price = get_price(symbol)
        raw_qty = two_percent / price
        qty = round_step(raw_qty, lot["stepSize"])
        if qty < lot["minQty"]:
            return jsonify({"error": "Quantity below minQty"}), 400

        # --- Market order BUY ---
        ts = int(time.time() * 1000)
        order_params = {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quantity": f"{qty:.8f}",
            "timestamp": ts
        }
        order_params["signature"] = sign_params(order_params, BINANCE_API_SECRET)
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}

        r_order = requests.post(f"{BASE_URL}/api/v3/order", headers=headers, params=order_params)
        order_data = r_order.json()

        if r_order.status_code != 200:
            print("❌ Order failed:", order_data)
            return jsonify({"error": "Market order failed", "details": order_data}), 400

        executed_qty = order_data.get("executedQty", f"{qty:.8f}")

        # --- OCO (TP y SL) ---
        ts = int(time.time() * 1000)
        oco_params = {
            "symbol": symbol,
            "side": "SELL",
            "quantity": executed_qty,
            "price": f"{tp:.2f}",
            "stopPrice": f"{sl:.2f}",
            "stopLimitPrice": f"{sl:.2f}",
            "stopLimitTimeInForce": "GTC",
            "timestamp": ts
        }
        oco_params["signature"] = sign_params(oco_params, BINANCE_API_SECRET)

        r_oco = requests.post(f"{BASE_URL}/api/v3/order/oco", headers=headers, params=oco_params)
        oco_data = r_oco.json()

        if r_oco.status_code != 200:
            print("⚠️ OCO failed:", oco_data)
            return jsonify({"warning": "Market order executed, OCO failed", "details": oco_data}), 200

        return jsonify({
            "status": "✅ BUY executed",
            "market_order": order_data,
            "oco_order": oco_data
        }), 200

    except Exception as e:
        print("❌ Exception:", str(e))
        return jsonify({"error": str(e)}), 500

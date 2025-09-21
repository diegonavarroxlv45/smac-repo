import os
import time
import hmac
import hashlib
import requests
from flask import Flask, request, jsonify

# ================= CONFIG =================
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
BASE_URL = "https://api.binance.com"

app = Flask(__name__)

# ================= HELPERS =================
def sign_params(params, secret):
    query_string = "&".join([f"{k}={params[k]}" for k in sorted(params)])
    return hmac.new(secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()

def get_balance(asset):
    try:
        ts = int(time.time() * 1000)
        params = {"timestamp": ts}
        params["signature"] = sign_params(params, BINANCE_API_SECRET)
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        r = requests.get(f"{BASE_URL}/api/v3/account", headers=headers, params=params, timeout=10)
        r.raise_for_status()
        balances = r.json()["balances"]
        for b in balances:
            if b["asset"] == asset:
                return float(b["free"])
        return 0.0
    except Exception as e:
        print(f"❌ Error getting balance for {asset}: {e}")
        return 0.0

def get_lot_info(symbol):
    try:
        r = requests.get(f"{BASE_URL}/api/v3/exchangeInfo", params={"symbol": symbol}, timeout=10)
        r.raise_for_status()
        data = r.json()["symbols"][0]
        for f in data["filters"]:
            if f["filterType"] == "LOT_SIZE":
                return {
                    "stepSize": float(f["stepSize"]),
                    "stepSize_str": f["stepSize"],
                    "minQty": float(f["minQty"]),
                    "minNotional": float(next((fl["minNotional"] for fl in data["filters"] if fl["filterType"] == "MIN_NOTIONAL"), 0))
                }
        return None
    except Exception as e:
        print(f"❌ Error getting lot info for {symbol}: {e}")
        return None

def get_price(symbol):
    try:
        r = requests.get(f"{BASE_URL}/api/v3/ticker/price", params={"symbol": symbol}, timeout=10)
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception as e:
        print(f"❌ Error getting price for {symbol}: {e}")
        return 0.0

def floor_to_step_str(value, stepSize_str):
    step_decimals = abs(len(stepSize_str.split(".")[1].rstrip("0"))) if "." in stepSize_str else 0
    return f"{value:.{step_decimals}f}"

# ================= POSITION MANAGEMENT =================
def close_position(symbol: str):
    """Cancela todas las órdenes abiertas de un símbolo y vende todo el balance disponible."""
    try:
        # 1. Cancelar órdenes abiertas
        ts = int(time.time() * 1000)
        params = {"symbol": symbol, "timestamp": ts}
        params["signature"] = sign_params(params, BINANCE_API_SECRET)
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        r = requests.get(f"{BASE_URL}/api/v3/openOrders", headers=headers, params=params, timeout=10)
        r.raise_for_status()
        open_orders = r.json()

        for order in open_orders:
            cancel_params = {"symbol": symbol, "orderId": order["orderId"], "timestamp": int(time.time() * 1000)}
            cancel_params["signature"] = sign_params(cancel_params, BINANCE_API_SECRET)
            rc = requests.delete(f"{BASE_URL}/api/v3/order", headers=headers, params=cancel_params, timeout=10)
            print(f"🗑 Cancelled order {order['orderId']} for {symbol}: {rc.json()}")

        # 2. Detectar base asset
        quote_candidates = ["USDC","USDT","BUSD","EUR","USD","BTC","ETH","BNB"]
        base_asset = None
        for q in quote_candidates:
            if symbol.endswith(q):
                base_asset = symbol[:-len(q)]
                break
        if base_asset is None:
            base_asset = symbol[:-4]  # fallback

        # 3. Balance libre
        base_free = get_balance(base_asset)
        if base_free <= 0:
            print(f"ℹ️ No {base_asset} left to close for {symbol}")
            return None

        # 4. Chequear minNotional
        lot = get_lot_info(symbol)
        if not lot:
            print(f"⚠️ Lot info not found for {symbol}")
            return None

        stepSize_str = lot["stepSize_str"]
        minQty = lot["minQty"]
        minNotional = lot.get("minNotional", 0.0)

        qty_str = floor_to_step_str(base_free, stepSize_str)
        qty = float(qty_str)

        ticker_price = get_price(symbol)
        notional = qty * ticker_price

        if qty < minQty or notional < minNotional:
            print(f"⚠️ Close qty too small: {qty} ({notional} USDC). Skipping.")
            return None

        # 5. Market SELL
        ts = int(time.time() * 1000)
        order_params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": qty_str,
            "timestamp": ts
        }
        order_params["signature"] = sign_params(order_params, BINANCE_API_SECRET)
        r_order = requests.post(f"{BASE_URL}/api/v3/order", headers=headers, params=order_params, timeout=10)
        print(f"💥 Closed position {symbol}: {r_order.json()}")
        return r_order.json()

    except Exception as e:
        print(f"❌ Error in close_position({symbol}): {e}")
        return None

# ================= WEBHOOK =================
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if not data or "symbol" not in data or "side" not in data:
        return jsonify({"error": "Invalid payload"}), 400

    symbol = data["symbol"]
    side = data["side"]

    if side == "SELL":
        print(f"🔎 Closing existing position before SELL {symbol}")
        close_position(symbol)
        # Aquí puedes añadir tu bloque de SELL original si quieres procesar SELLs manuales extra
        return jsonify({"status": "SELL processed"})

    elif side == "BUY":
        # TODO: Implementar BUY logic como ya tenías, usando executedQty + OCOs
        print(f"🟢 BUY signal for {symbol} (logic pendiente de implementar)")
        return jsonify({"status": "BUY processed"})

    return jsonify({"status": "ignored"})

# ================= MAIN =================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

# main.py definitivo y adaptado
from flask import Flask, request, jsonify
import hmac, hashlib, time
import requests
import os
import math

app = Flask(__name__)

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

def sign_params(params, secret):
    query_string = "&".join([f"{k}={v}" for k, v in params.items()])
    signature = hmac.new(secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    return signature

def get_price(symbol):
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    return float(requests.get(url).json()["price"])

def get_balance():
    timestamp = int(time.time() * 1000)
    params = {"timestamp": timestamp}
    params["signature"] = sign_params(params, BINANCE_API_SECRET)
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    url = "https://api.binance.com/api/v3/account"
    balances = requests.get(url, headers=headers, params=params).json().get("balances", [])
    usdc_balance = next((item for item in balances if item["asset"] == "USDC"), None)
    return float(usdc_balance["free"]) if usdc_balance else 0

def get_lot_info(symbol):
    url = "https://api.binance.com/api/v3/exchangeInfo"
    data = requests.get(url).json()
    for s in data["symbols"]:
        if s["symbol"] == symbol:
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    return {
                        "minQty": float(f["minQty"]),
                        "stepSize": float(f["stepSize"])
                    }
    return None

def round_step(quantity, step):
    return float(f"{math.floor(quantity / step) * step:.8f}")

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    if not data:
        return jsonify({"error": "No data received"}), 400

    try:
        symbol = data["symbol"]
        side = data["side"].upper()  # BUY o SELL
        entry_price = float(data["entry_price"])
        stop_loss = float(data["stop_loss"])
        take_profit = float(data["take_profit"])
        position_type = int(float(data["position_type"]))  # 1 = Long, 0 = Short

        usdc = get_balance()
        one_percent = usdc * 0.01
        lot_info = get_lot_info(symbol)
        if lot_info is None:
            return jsonify({"error": f"No se pudo obtener info de lotes para {symbol}"}), 400

        price = get_price(symbol)
        raw_quantity = one_percent / price
        quantity = round_step(raw_quantity, lot_info["stepSize"])

        if quantity < lot_info["minQty"]:
            return jsonify({"error": f"Cantidad calculada {quantity} menor que mínimo {lot_info['minQty']} para {symbol}"}), 400

        # Orden de mercado
        url_order = "https://api.binance.com/api/v3/order"
        timestamp = int(time.time() * 1000)
        order_params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": f"{quantity:.8f}",
            "timestamp": timestamp
        }
        order_params["signature"] = sign_params(order_params, BINANCE_API_SECRET)
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        order_response = requests.post(url_order, headers=headers, params=order_params)
        order_data = order_response.json()

        if "code" in order_data and order_data["code"] < 0:
            return jsonify({"error": f"Error en orden de mercado: {order_data}"}), 400

        # SL y TP coherentes según dirección
        if position_type == 1:  # Long
            sl_price = stop_loss
            tp_price = take_profit
            oco_side = "SELL"
        else:  # Short
            sl_price = stop_loss
            tp_price = take_profit
            oco_side = "BUY"

        oco_url = "https://api.binance.com/api/v3/order/oco"
        oco_params = {
            "symbol": symbol,
            "side": oco_side,
            "quantity": f"{quantity:.8f}",
            "price": f"{tp_price:.2f}",
            "stopPrice": f"{sl_price:.2f}",
            "stopLimitPrice": f"{sl_price:.2f}",
            "stopLimitTimeInForce": "GTC",
            "timestamp": int(time.time() * 1000)
        }
        oco_params["signature"] = sign_params(oco_params, BINANCE_API_SECRET)
        oco_response = requests.post(oco_url, headers=headers, params=oco_params)

        return jsonify({
            "status": "success",
            "market_order": order_data,
            "oco_order": oco_response.json()
        })

    except Exception as e:
        print("ERROR:", str(e))
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

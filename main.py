import os
import time
import hmac
import hashlib
import requests
import functools
from decimal import Decimal, ROUND_DOWN
from flask import Flask, request, jsonify

# --- Configuración ---
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
BASE_URL = "https://api.binance.com"

# Porcentaje de stop loss y take profit
STOP_LOSS_PCT = 0.98   # -2%
TAKE_PROFIT_PCT = 1.02 # +2%

# flush en todos los prints
print = functools.partial(print, flush=True)

app = Flask(__name__)

# --- Helpers generales ---
def sign_params(params: dict, secret: str) -> str:
    query = "&".join([f"{k}={v}" for k, v in params.items()])
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def get_price(symbol: str) -> float:
    r = requests.get(f"{BASE_URL}/api/v3/ticker/price", params={"symbol": symbol})
    r.raise_for_status()
    return float(r.json()["price"])


def get_balance(asset="USDC") -> float:
    ts = int(time.time() * 1000)
    params = {"timestamp": ts}
    params["signature"] = sign_params(params, BINANCE_API_SECRET)
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    r = requests.get(f"{BASE_URL}/api/v3/account", headers=headers, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    bal = next((b for b in data.get("balances", []) if b["asset"] == asset), None)
    return float(bal["free"]) if bal else 0.0


def get_lot_info(symbol: str):
    r = requests.get(f"{BASE_URL}/api/v3/exchangeInfo", params={"symbol": symbol}, timeout=10)
    r.raise_for_status()
    data = r.json()
    s = next((x for x in data.get("symbols", []) if x["symbol"] == symbol), None)
    if not s:
        return None
    lot = {"stepSize_str": None, "minQty": None, "tickSize_str": None, "minNotional": 0.0}
    for f in s.get("filters", []):
        t = f.get("filterType")
        if t == "LOT_SIZE":
            lot["stepSize_str"] = f.get("stepSize")
            lot["minQty"] = float(f.get("minQty"))
        elif t == "PRICE_FILTER":
            lot["tickSize_str"] = f.get("tickSize")
        elif t in ("MIN_NOTIONAL", "NOTIONAL"):
            lot["minNotional"] = float(f.get("minNotional") or 0.0)
    return lot


def floor_to_step_str(value: float, step_str: str) -> str:
    v = Decimal(str(value))
    step = Decimal(str(step_str))
    d = (v // step) * step
    decimals = -step.as_tuple().exponent if step.as_tuple().exponent < 0 else 0
    return format(d.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_DOWN), f".{decimals}f")


# --- Gestión de posiciones ---
def close_position(symbol: str):
    try:
        ts = int(time.time() * 1000)
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}

        # Cancelar todas las órdenes abiertas
        params = {"symbol": symbol, "timestamp": ts}
        params["signature"] = sign_params(params, BINANCE_API_SECRET)
        r = requests.delete(f"{BASE_URL}/api/v3/openOrders", headers=headers, params=params, timeout=10)
        if r.status_code not in (200, 400):
            r.raise_for_status()

        # Vender balance libre
        asset = symbol.replace("USDC", "")
        qty = get_balance(asset)
        if qty > 0:
            lot = get_lot_info(symbol)
            if lot:
                qty_str = floor_to_step_str(qty, lot["stepSize_str"])
                order_params = {
                    "symbol": symbol,
                    "side": "SELL",
                    "type": "MARKET",
                    "quantity": qty_str,
                    "timestamp": int(time.time() * 1000),
                }
                order_params["signature"] = sign_params(order_params, BINANCE_API_SECRET)
                r = requests.post(f"{BASE_URL}/api/v3/order", headers=headers, params=order_params, timeout=10)
                r.raise_for_status()
                print(f"✅ Closed position {symbol}, qty={qty_str}")
            else:
                print(f"⚠️ No lot info for {symbol}, cannot SELL")
        else:
            print(f"ℹ️ No balance to close for {symbol}")

    except Exception as e:
        print(f"❌ Error closing {symbol}: {e}")


def execute_buy(symbol: str):
    """
    Ejecuta un market BUY con balance en USDC y coloca una OCO (SL + TP).
    """
    try:
        lot = get_lot_info(symbol)
        if not lot:
            print(f"⚠️ No lot info for {symbol}")
            return None

        price = get_price(symbol)
        usdc_balance = get_balance("USDC")
        if usdc_balance <= 5:  # margen de seguridad
            print("⚠️ Not enough USDC to buy")
            return None

        # Calcular qty de compra
        qty = usdc_balance / price
        qty_str = floor_to_step_str(qty, lot["stepSize_str"])
        qty = float(qty_str)

        if qty < lot["minQty"] or (qty * price) < lot["minNotional"]:
            print(f"⚠️ Qty {qty_str} too small for {symbol}")
            return None

        # Ejecutar market BUY
        ts = int(time.time() * 1000)
        order_params = {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quantity": qty_str,
            "timestamp": ts,
        }
        order_params["signature"] = sign_params(order_params, BINANCE_API_SECRET)
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        r = requests.post(f"{BASE_URL}/api/v3/order", headers=headers, params=order_params, timeout=10)
        r.raise_for_status()
        order_data = r.json()
        executed_qty = float(order_data.get("executedQty", qty_str))
        print(f"✅ BUY {symbol}, qty={executed_qty}")

        # Crear OCO (Stop Loss + Take Profit)
        sl_price = price * STOP_LOSS_PCT
        tp_price = price * TAKE_PROFIT_PCT

        oco_params = {
            "symbol": symbol,
            "side": "SELL",
            "quantity": floor_to_step_str(executed_qty, lot["stepSize_str"]),
            "price": floor_to_step_str(tp_price, lot["tickSize_str"]),
            "stopPrice": floor_to_step_str(sl_price, lot["tickSize_str"]),
            "stopLimitPrice": floor_to_step_str(sl_price * 0.999, lot["tickSize_str"]),
            "stopLimitTimeInForce": "GTC",
            "timestamp": int(time.time() * 1000),
        }
        oco_params["signature"] = sign_params(oco_params, BINANCE_API_SECRET)

        r = requests.post(f"{BASE_URL}/api/v3/order/oco", headers=headers, params=oco_params, timeout=10)
        r.raise_for_status()
        print(f"✅ OCO placed for {symbol}: TP={tp_price}, SL={sl_price}")

        return {"order": order_data, "oco": r.json()}

    except Exception as e:
        print(f"❌ Error in execute_buy({symbol}): {e}")
        return None


# --- Webhook ---
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        if request.is_json:
            data = request.get_json()
        else:
            data = request.form.to_dict()

        if not data or "symbol" not in data or "side" not in data:
            return jsonify({"error": "Invalid payload"}), 400

        symbol = data["symbol"].upper()
        side = data["side"].upper()

        if side == "SELL":
            print(f"🔎 Closing existing position before SELL {symbol}")
            close_position(symbol)
            return jsonify({"status": "SELL processed"})

        elif side == "BUY":
            print(f"🟢 BUY signal for {symbol}")
            result = execute_buy(symbol)
            return jsonify({"status": "BUY executed", "result": result})

        return jsonify({"status": "ignored"})

    except Exception as e:
        print(f"❌ Error in webhook: {e}")
        return jsonify({"error": str(e)}), 500


# --- Run ---
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

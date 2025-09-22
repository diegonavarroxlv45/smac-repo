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

# flush en todos los prints
print = functools.partial(print, flush=True)

app = Flask(__name__)

# --- Helpers generales ---
def sign_params(params: dict, secret: str) -> str:
    """
    Firma parámetros en el orden dado (Python 3.7+ preserva orden de inserción).
    Devuelve hex digest de HMAC-SHA256.
    """
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
    """
    Devuelve dict con:
      - stepSize_str (string, p.ej. "0.00000100")
      - minQty (float)
      - tickSize_str (string)
      - minNotional (float) o 0.0
    """
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
            lot["minNotional"] = float(f.get("minNotional") or f.get("minNotional", 0.0))
    return lot


def floor_to_step_str(value: float, step_str: str) -> str:
    """
    Ajusta 'value' hacia abajo al múltiplo de 'step_str' (usando Decimal) y devuelve string
    con tantos decimales como el step.
    """
    v = Decimal(str(value))
    step = Decimal(str(step_str))
    d = (v // step) * step
    decimals = -step.as_tuple().exponent if step.as_tuple().exponent < 0 else 0
    return format(d.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_DOWN), f".{decimals}f")


# --- Gestión de posiciones ---
def close_position(symbol: str):
    """
    Cierra todas las órdenes abiertas para el símbolo y vende cualquier cantidad disponible.
    """
    try:
        ts = int(time.time() * 1000)
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}

        # Cancelar órdenes abiertas
        params = {"symbol": symbol, "timestamp": ts}
        params["signature"] = sign_params(params, BINANCE_API_SECRET)
        r = requests.delete(f"{BASE_URL}/api/v3/openOrders", headers=headers, params=params, timeout=10)
        if r.status_code not in (200, 400):
            r.raise_for_status()

        # Ver balance
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


# --- Webhook ---
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        # Aceptar tanto JSON como FORM
        if request.is_json:
            data = request.get_json()
        else:
            data = request.form.to_dict()

        if not data or "symbol" not in data or "side" not in data:
            return jsonify({"error": "Invalid payload"}), 400

        symbol = data["symbol"]
        side = data["side"].upper()

        if side == "SELL":
            print(f"🔎 Closing existing position before SELL {symbol}")
            close_position(symbol)
            return jsonify({"status": "SELL processed"})

        elif side == "BUY":
            # TODO: Implementar lógica real de BUY + OCO
            print(f"🟢 BUY signal for {symbol} (logic pendiente de implementar)")
            return jsonify({"status": "BUY processed"})

        return jsonify({"status": "ignored"})

    except Exception as e:
        print(f"❌ Error in webhook: {e}")
        return jsonify({"error": str(e)}), 500


# --- Run ---
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
    

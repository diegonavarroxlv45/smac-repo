# main.py
from flask import Flask, request, jsonify
import hmac
import hashlib
import time
import requests
import os
import math
import functools
from decimal import Decimal, ROUND_DOWN

# Force stdout flush so logs aparecen inmediatamente en Render
print = functools.partial(print, flush=True)

app = Flask(__name__)

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
BASE_URL = "https://api.binance.com"

# -------------------- Auxiliares -------------------- #

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
    # floor to multiple of step
    d = (v // step) * step
    decimals = -step.as_tuple().exponent if step.as_tuple().exponent < 0 else 0
    # format without scientific notation
    return format(d.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_DOWN), f".{decimals}f")


# -------------------- Webhook -------------------- #

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    if not data:
        print("❌ Invalid JSON payload:", request.data)
        return jsonify({"error": "No JSON data received"}), 400

    print("📩 Webhook received:", data)

    try:
        symbol = data["symbol"]                      # e.g. "BTCUSDC"
        side = data["side"].upper()                  # "BUY" or "SELL"
        entry_price = float(data.get("entry_price", 0))
        sl = float(data.get("sl", 0))
        tp = float(data.get("tp", 0))
        position_type = data.get("position_type", "").upper()  # "LONG" or "SHORT"

        # Validaciones básicas
        if position_type not in ["LONG", "SHORT"]:
            msg = f"Invalid position_type: {position_type}"
            print("⚠️", msg)
            return jsonify({"error": msg}), 400

        if side not in ["BUY", "SELL"]:
            msg = f"Invalid side: {side}"
            print("⚠️", msg)
            return jsonify({"error": msg}), 400

        # --- IGNORAR SELL (según lógica decidida) ---
        if side == "SELL":
            print(f"⏭ SELL ignored for {symbol}")
            return jsonify({"status": f"Ignored SELL for {symbol}"}), 200

        # --- Solo procesar BUY ---
        usdc = get_balance("USDC")
        # Usa 4% si así lo tienes (ajusta a 0.02 para 2%)
        two_percent_amount = usdc * 0.04
        if two_percent_amount <= 0:
            msg = f"USDC balance is zero or too low: {usdc}"
            print("⚠️", msg)
            return jsonify({"error": msg}), 400

        lot = get_lot_info(symbol)
        if not lot:
            msg = f"Lot info not found for {symbol}"
            print("⚠️", msg)
            return jsonify({"error": msg}), 400

        stepSize_str = lot["stepSize_str"]
        minQty = lot["minQty"]
        tickSize_str = lot["tickSize_str"]
        minNotional = lot.get("minNotional", 0.0)

        if not stepSize_str or minQty is None or not tickSize_str:
            msg = f"Incomplete filters for {symbol}"
            print("⚠️", msg, lot)
            return jsonify({"error": msg}), 400

        # Precio spot actual (si falla, usamos entry_price)
        try:
            spot_price = get_price(symbol)
        except Exception:
            spot_price = entry_price

        # Cantidad bruta (en base asset) y redondeo a stepSize
        raw_qty = two_percent_amount / spot_price
        qty_str = floor_to_step_str(raw_qty, stepSize_str)
        qty = float(qty_str)

        if qty < minQty:
            msg = f"Quantity {qty} < minQty {minQty} for {symbol} (two_percent={two_percent_amount}, spot={spot_price})"
            print("⚠️", msg)
            return jsonify({"error": msg}), 400

        # Validar minNotional
        notional = qty * spot_price
        if notional < (minNotional or 0.0):
            msg = f"Notional {notional:.8f} < minNotional {minNotional} for {symbol}"
            print("⚠️", msg)
            return jsonify({"error": msg}), 400

        # --- Orden de mercado (BUY) ---
        print(f"🟢 Placing MARKET BUY -> {qty_str} {symbol} (spot={spot_price}, target_usdc={two_percent_amount:.4f})")
        ts = int(time.time() * 1000)
        order_params = {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quantity": qty_str,
            "timestamp": ts
        }
        # firmar y enviar
        order_params["signature"] = sign_params(order_params, BINANCE_API_SECRET)
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        r_order = requests.post(f"{BASE_URL}/api/v3/order", headers=headers, params=order_params, timeout=10)
        order_data = r_order.json()

        if r_order.status_code != 200:
            print("❌ Market order failed:", order_data)
            return jsonify({"error": "Market order failed", "details": order_data}), 400

        executed_qty_str = order_data.get("executedQty", qty_str)
        print("✅ Market order executed:", {"executedQty": executed_qty_str, "order": order_data})

        # --- Ajustar cantidad disponible para OCO ---
        oco_qty = float(executed_qty_str) * 0.999  # dejamos margen por comisión
        oco_qty_str = floor_to_step_str(oco_qty, stepSize_str)

        # --- PREPARAR OCO (ajustes de precisión y colchón) ---
        tp_adj = floor_to_step_str(tp, tickSize_str)
        # colchón en SL para evitar rechazo "stopPrice too close" (reducción leve)
        sl_adj = floor_to_step_str(Decimal(str(sl)) * Decimal("0.999"), tickSize_str)
        # stopLimit algo más conservador
        stop_limit_adj = floor_to_step_str(Decimal(str(sl_adj)) * Decimal("0.999"), tickSize_str)

        print("🔧 Adjusted TP/SL:", {"tp_adj": tp_adj, "sl_adj": sl_adj, "stop_limit_adj": stop_limit_adj})

        # --- INTENTAR COLOCAR OCO (reintentos) ---
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        oco_success = False
        oco_resp_data = None

        for attempt in range(1, 4):  # 3 intentos
            ts = int(time.time() * 1000)
            oco_params = {
                "symbol": symbol,
                "side": "SELL",
                "quantity": oco_qty_str,
                "price": tp_adj,
                "stopPrice": sl_adj,
                "stopLimitPrice": stop_limit_adj,
                "stopLimitTimeInForce": "GTC",
                "timestamp": ts
            }
            oco_params["signature"] = sign_params(oco_params, BINANCE_API_SECRET)
            r_oco = requests.post(f"{BASE_URL}/api/v3/order/oco", headers=headers, params=oco_params, timeout=10)
            oco_resp_data = r_oco.json()

            if r_oco.status_code == 200:
                print(f"✅ OCO placed (attempt {attempt}):", oco_resp_data)
                oco_success = True
                break
            else:
                print(f"⚠️ OCO failed (attempt {attempt}):", oco_resp_data)
                time.sleep(2)  # pequeño delay antes de reintentar

        if not oco_success:
            # No cerramos como error crítico: market order ya ejecutada. Devolvemos advertencia.
            print("⚠️ OCO failed after 3 attempts. Leaving market order in place.")
            return jsonify({
                "warning": "Market order executed but OCO failed after 3 attempts",
                "market_order": order_data,
                "oco_error": oco_resp_data
            }), 200

        # Si todo OK:
        return jsonify({
            "status": "✅ BUY executed and OCO placed",
            "market_order": order_data,
            "oco_order": oco_resp_data
        }), 200

    except Exception as e:
        # imprime repr para tener más info (stack/exception)
        print("❌ EXCEPTION:", repr(e))
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    # Esto solo se usa cuando ejecutas localmente con `python main.py`
    app.run(host="0.0.0.0", port=5000)

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

print = functools.partial(print, flush=True)

app = Flask(__name__)

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

BINANCE_BASE = "https://api.binance.com"
HTTP_TIMEOUT = 10  # segundos
RECV_WINDOW = 5000 # ms


# -------------------- Helpers de firma/HTTP -------------------- #

def sign_params(params: dict, secret: str) -> str:
    """Firma parámetros conforme a Binance (usar exactamente el mismo orden de inserción)."""
    # OJO: Python 3.7+ preserva orden de dict; requests respetará ese orden al serializar.
    query_string = "&".join([f"{k}={v}" for k, v in params.items()])
    signature = hmac.new(secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    return signature


def binance_get(path: str, headers=None, params=None, signed=False):
    url = f"{BINANCE_BASE}{path}"
    if signed:
        if params is None:
            params = {}
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = RECV_WINDOW
        params["signature"] = sign_params({k: v for k, v in params.items() if k != "signature"}, BINANCE_API_SECRET)
    headers = headers or {}
    headers.setdefault("X-MBX-APIKEY", BINANCE_API_KEY)
    return requests.get(url, headers=headers, params=params, timeout=HTTP_TIMEOUT)


def binance_post(path: str, headers=None, params=None, signed=False):
    url = f"{BINANCE_BASE}{path}"
    if signed:
        if params is None:
            params = {}
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = RECV_WINDOW
        params["signature"] = sign_params({k: v for k, v in params.items() if k != "signature"}, BINANCE_API_SECRET)
    headers = headers or {}
    headers.setdefault("X-MBX-APIKEY", BINANCE_API_KEY)
    return requests.post(url, headers=headers, params=params, timeout=HTTP_TIMEOUT)


# -------------------- Info de símbolo y balances -------------------- #

def get_symbol_info(symbol: str):
    """Devuelve info de símbolo (baseAsset, quoteAsset, stepSize, minQty, tickSize, minNotional, ocoAllowed)."""
    r = binance_get("/api/v3/exchangeInfo", params={"symbol": symbol})
    data = r.json()
    if r.status_code != 200:
        raise RuntimeError(f"exchangeInfo error: {data}")
    if "symbols" not in data or not data["symbols"]:
        raise RuntimeError(f"exchangeInfo vacío para {symbol}")

    s = data["symbols"][0]
    info = {
        "baseAsset": s["baseAsset"],
        "quoteAsset": s["quoteAsset"],
        "ocoAllowed": s.get("ocoAllowed", True),
        "stepSize_str": None,
        "minQty": None,
        "tickSize_str": None,
        "minNotional": None
    }
    for f in s["filters"]:
        t = f["filterType"]
        if t == "LOT_SIZE":
            info["stepSize_str"] = f["stepSize"]      # string exacto
            info["minQty"] = float(f["minQty"])
        elif t == "PRICE_FILTER":
            info["tickSize_str"] = f["tickSize"]      # string exacto
        elif t in ("MIN_NOTIONAL", "NOTIONAL"):
            # Binance usa a veces NOTIONAL; tomamos el mínimo aplicable
            info["minNotional"] = float(f.get("minNotional") or f.get("minNotional", 0.0))
    return info


def get_asset_balance(asset: str) -> float:
    """Balance libre de un asset (p.ej. 'BTC')."""
    r = binance_get("/api/v3/account", signed=True)
    data = r.json()
    if r.status_code != 200:
        raise RuntimeError(f"account error: {data}")
    bal = next((b for b in data.get("balances", []) if b["asset"] == asset), None)
    return float(bal["free"]) if bal else 0.0


def get_usdc_balance() -> float:
    return get_asset_balance("USDC")


# -------------------- Redondeos seguros (Decimal) -------------------- #

def floor_to_step_str(value: float, step_str: str) -> str:
    """
    Ajusta 'value' hacia abajo al múltiplo de 'step_str' y devuelve string con
    los decimales apropiados para Binance.
    """
    v = Decimal(str(value))
    step = Decimal(step_str)
    # (v // step) * step hace floor al múltiplo
    d = (v // step) * step
    # Formatear con los decimales del step
    decimals = -step.as_tuple().exponent if step.as_tuple().exponent < 0 else 0
    return format(d, f".{decimals}f")


# -------------------- Webhook -------------------- #

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True)  # Acepta text/plain con JSON embebido
    if not data:
        print("❌ Invalid JSON payload:", request.data)
        return jsonify({"error": "No JSON data received"}), 400

    print("📩 Webhook received:", data)

    try:
        # --- Extraer datos del JSON ---
        symbol = data["symbol"]                  # p.ej. "BTCUSDC"
        side = data["side"].upper()              # "BUY" o "SELL"
        entry_price = float(data["entry_price"])
        stop_loss = float(data["sl"])
        take_profit = float(data["tp"])
        position_type = data["position_type"].upper()  # "LONG" o "SHORT"

        if position_type not in ["LONG", "SHORT"]:
            msg = f"Invalid position_type: {position_type}"
            print("⚠️", msg)
            return jsonify({"error": msg}), 400

        if side not in ["BUY", "SELL"]:
            msg = f"Invalid side: {side}"
            print("⚠️", msg)
            return jsonify({"error": msg}), 400

        # --- Info de símbolo (filtros y activos base/quote) ---
        info = get_symbol_info(symbol)
        base = info["baseAsset"]
        quote = info["quoteAsset"]
        stepSize_str = info["stepSize_str"]
        tickSize_str = info["tickSize_str"]
        minQty = info["minQty"]
        minNotional = info["minNotional"] or 0.0
        ocoAllowed = info["ocoAllowed"]

        if not stepSize_str or not tickSize_str:
            msg = f"Missing filters for {symbol} (stepSize/tickSize)."
            print("⚠️", msg, info)
            return jsonify({"error": msg}), 400

        # --- Saldo y tamaño de posición (2% en USDC) ---
        usdc = get_usdc_balance()
        two_percent = usdc * 0.02
        if two_percent <= 0:
            msg = f"USDC balance is zero or too low: {usdc}"
            print("⚠️", msg)
            return jsonify({"error": msg}), 400

        # Precio spot actual (para validar notional y calcular qty)
        r_price = binance_get("/api/v3/ticker/price", params={"symbol": symbol})
        spot_price = float(r_price.json()["price"]) if r_price.status_code == 200 else entry_price

        # Cantidad bruta (en activo base) y redondeo a step size
        raw_qty = two_percent / spot_price
        qty_str = floor_to_step_str(raw_qty, stepSize_str)
        qty = float(qty_str)

        if qty < minQty:
            msg = f"Quantity {qty} < minQty {minQty} for {symbol}"
            print("⚠️", msg, f"(two_percent={two_percent}, spot={spot_price})")
            return jsonify({"error": msg}), 400

        # Validar MIN_NOTIONAL
        notional = qty * spot_price
        if notional < minNotional:
            msg = f"Notional {notional:.8f} < minNotional {minNotional} for {symbol}"
            print("⚠️", msg)
            return jsonify({"error": msg}), 400

        # Si es SELL en spot, asegúrate de tener balance del activo base
        if side == "SELL":
            base_free = get_asset_balance(base)
            if base_free <= 0:
                msg = f"SELL signal but no {base} balance in spot wallet."
                print("⚠️", msg, f"base_free={base_free}")
                return jsonify({"error": msg}), 400
            # No vendas más de lo que tienes
            if qty > base_free:
                qty = float(floor_to_step_str(base_free, stepSize_str))
                qty_str = floor_to_step_str(qty, stepSize_str)

        # --- Lanzar orden de mercado ---
        print(f"🟢 Market Order -> {side} {qty_str} {symbol} (entry={entry_price})")
        order_params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": qty_str  # usamos cantidad (no quoteOrderQty) para simplificar
        }
        order_resp = binance_post("/api/v3/order", params=order_params, signed=True)
        order_data = order_resp.json()

        if order_resp.status_code != 200:
            print("❌ Market order failed:", order_data)
            return jsonify({"error": f"Market order failed", "details": order_data}), 400

        # Usar executedQty real para el OCO (mejor que la qty calculada)
        executed_qty_str = order_data.get("executedQty", qty_str)

        # --- OCO (SL/TP) ---
        if not ocoAllowed:
            msg = f"OCO not allowed for {symbol} on this market."
            print("⚠️", msg)
            return jsonify({
                "status": "Order executed (no OCO)",
                "market_order": order_data,
                "warning": msg
            }), 200

        # Ajustar precios a tickSize
        tp_str = floor_to_step_str(take_profit, tickSize_str)
        sl_str = floor_to_step_str(stop_loss,  tickSize_str)

        # Validación simple para coherencia (LONG: TP>entry>SL ; SHORT: SL>entry>TP)
        if position_type == "LONG" and not (Decimal(tp_str) > Decimal(str(entry_price)) > Decimal(sl_str)):
            print("⚠️ LONG incoherent levels:", {"entry": entry_price, "sl": sl_str, "tp": tp_str})
        if position_type == "SHORT" and not (Decimal(sl_str) > Decimal(str(entry_price)) > Decimal(tp_str)):
            print("⚠️ SHORT incoherent levels:", {"entry": entry_price, "sl": sl_str, "tp": tp_str})

        oco_side = "SELL" if position_type == "LONG" else "BUY"

        print(f"📉 OCO -> {oco_side} {executed_qty_str} {symbol} | TP={tp_str}, SL={sl_str}")
        oco_params = {
            "symbol": symbol,
            "side": oco_side,
            "quantity": executed_qty_str,
            "price": tp_str,                 # límite de TP
            "stopPrice": sl_str,             # trigger del SL
            "stopLimitPrice": sl_str,        # precio límite del SL
            "stopLimitTimeInForce": "GTC"
        }
        oco_resp = binance_post("/api/v3/order/oco", params=oco_params, signed=True)
        oco_data = oco_resp.json()

        if oco_resp.status_code != 200:
            print("❌ OCO failed:", oco_data)
            return jsonify({
                "error": "OCO failed",
                "market_order": order_data,
                "details": oco_data
            }), 400

        return jsonify({
            "status": "✅ Order executed",
            "market_order": order_data,
            "oco_order": oco_data
        }), 200

    except Exception as e:
        print("❌ ERROR:", repr(e))
        return jsonify({"error": str(e)}), 500

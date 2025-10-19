import os
import time
import hmac
import hashlib
import requests
import functools
from decimal import Decimal, ROUND_DOWN
from flask import Flask, request, jsonify

# ====== SETTINGS ======
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
BASE_URL = "https://api.binance.com"

BUY_PCT = float(os.getenv("BUY_PCT", "0.04"))
MAX_USDC_PER_ORDER = float(os.getenv("MAX_USDC_PER_ORDER", "100"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.97"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "1.06"))
COMMISSION_BUFFER = Decimal(os.getenv("COMMISSION_BUFFER", "0.999"))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes")

print = functools.partial(print, flush=True)
app = Flask(__name__)


# ====== AUXILIAR FUNCTIONS ======
def _now_ms():
    return int(time.time() * 1000)


def sign_params_query(params: dict, secret: str):
    query = "&".join([f"{k}={v}" for k, v in params.items()])
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query, signature


def _request_with_retries(method: str, url: str, **kwargs):
    for i in range(3):
        try:
            resp = requests.request(method, url, timeout=10, **kwargs)
            if resp.status_code == 200:
                return resp.json()
            else:
                print(f"⚠️ Attempt {i+1} failed: {resp.text}")
        except Exception as e:
            print(f"⚠️ Request error: {e}")
        time.sleep(1)
    raise Exception("❌ Request failed after retries")


def send_signed_request(http_method: str, path: str, payload: dict):
    query_string = "&".join([f"{k}={v}" for k, v in payload.items()])
    signature = hmac.new(BINANCE_API_SECRET.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE_URL}{path}?{query_string}&signature={signature}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    return _request_with_retries(http_method, url, headers=headers)


def floor_to_step_str(value, step):
    d = Decimal(str(step))
    return str(Decimal(str(value)).quantize(d, rounding=ROUND_DOWN))


# ====== BALANCE FUNCTIONS ======
def get_balance_margin(asset="USDC") -> float:
    ts = _now_ms()
    params = {"timestamp": ts}
    q, sig = sign_params_query(params, BINANCE_API_SECRET)
    url = f"{BASE_URL}/sapi/v1/margin/account?{q}&signature={sig}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    if DRY_RUN:
        print(f"[DRY_RUN] get_balance_margin({asset}) -> simulated 1000")
        return 1000.0
    data = _request_with_retries("GET", url, headers=headers)
    bal = next((b for b in data.get("userAssets", []) if b["asset"] == asset), None)
    return float(bal["free"]) if bal else 0.0


def get_symbol_lot(symbol):
    data = _request_with_retries("GET", f"{BASE_URL}/api/v3/exchangeInfo")
    for s in data["symbols"]:
        if s["symbol"] == symbol:
            fs = next(f for f in s["filters"] if f["filterType"] == "LOT_SIZE")
            ts = next(f for f in s["filters"] if f["filterType"] == "PRICE_FILTER")
            return {
                "stepSize": float(fs["stepSize"]),
                "stepSize_str": fs["stepSize"],
                "tickSize": float(ts["tickSize"]),
                "tickSize_str": ts["tickSize"],
            }
    raise Exception(f"Symbol not found: {symbol}")


# ====== MAIN FUNCTIONS ======
def execute_long_margin(symbol):
    lot = get_symbol_lot(symbol)
    balance_usdc = get_balance_margin("USDC")
    qty_quote = min(balance_usdc * BUY_PCT, MAX_USDC_PER_ORDER)

    params = {
        "symbol": symbol,
        "side": "BUY",
        "type": "MARKET",
        "quoteOrderQty": floor_to_step_str(qty_quote, lot["tickSize_str"]),
        "timestamp": _now_ms(),
    }

    if DRY_RUN:
        print(f"[DRY_RUN] Margin LONG {symbol}: quoteOrderQty={qty_quote}")
        return

    resp = send_signed_request("POST", "/sapi/v1/margin/order", params)

    if "fills" in resp:
        executed_qty = sum(float(f["qty"]) for f in resp["fills"])
        spent_quote = sum(float(f["price"]) * float(f["qty"]) for f in resp["fills"])
        entry_price = spent_quote / executed_qty
        print(f"✅ Margin BUY executed {symbol}: executedQty={executed_qty}, spent≈{spent_quote}")

        # Place SL & TP
        if executed_qty > 0:
            place_sl_tp_margin(symbol, "BUY", entry_price, executed_qty, lot)


def execute_short_margin(symbol):
    lot = get_symbol_lot(symbol)
    borrowed_qty = 0.0
    balance_base = get_balance_margin(symbol.replace("USDC", ""))

    if balance_base <= 0:
        borrow_params = {"asset": symbol.replace("USDC", ""), "amount": 0.002, "timestamp": _now_ms()}
        send_signed_request("POST", "/sapi/v1/margin/loan", borrow_params)
        borrowed_qty = 0.002
        print(f"📥 Borrowed {borrowed_qty} {symbol.replace('USDC', '')}")

    qty_str = floor_to_step_str(borrowed_qty, lot["stepSize_str"])
    params = {
        "symbol": symbol,
        "side": "SELL",
        "type": "MARKET",
        "quantity": qty_str,
        "timestamp": _now_ms(),
    }

    if DRY_RUN:
        print(f"[DRY_RUN] Margin SHORT {symbol}: qty={qty_str}")
        return

    resp = send_signed_request("POST", "/sapi/v1/margin/order", params)
    entry_price = float(resp.get("price", 0)) or 0.0
    print(f"✅ SHORT opened {symbol} qty={qty_str}")

    # Place SL & TP
    if borrowed_qty > 0:
        place_sl_tp_margin(symbol, "SELL", entry_price, float(qty_str), lot)


# ====== SL/TP FUNCTIONS ======
def place_sl_tp_margin(symbol: str, side: str, entry_price: float, executed_qty: float, lot: dict):
    """
    Coloca órdenes LIMIT de Stop Loss y Take Profit en margin tras una operación de entrada.
    """
    try:
        sl_side = "SELL" if side == "BUY" else "BUY"
        tp_side = sl_side

        if side == "BUY":  # LONG
            sl_price = entry_price * STOP_LOSS_PCT
            tp_price = entry_price * TAKE_PROFIT_PCT
        else:  # SHORT
            sl_price = entry_price / STOP_LOSS_PCT
            tp_price = entry_price / TAKE_PROFIT_PCT

        sl_price_str = floor_to_step_str(sl_price, lot["tickSize_str"])
        tp_price_str = floor_to_step_str(tp_price, lot["tickSize_str"])
        qty_str = floor_to_step_str(executed_qty * float(COMMISSION_BUFFER), lot["stepSize_str"])

        for label, price_str in [("SL", sl_price_str), ("TP", tp_price_str)]:
            params = {
                "symbol": symbol,
                "side": sl_side,
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": qty_str,
                "price": price_str,
                "timestamp": _now_ms(),
            }
            send_signed_request("POST", "/sapi/v1/margin/order", params)
            print(f"📈 {label} order placed for {symbol} at {price_str} ({sl_side})")
        return True
    except Exception as e:
        print(f"⚠️ Could not place SL/TP for {symbol}: {e}")
        return False


# ====== FLASK WEBHOOK ======
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    if not data or "symbol" not in data or "side" not in data:
        return jsonify({"error": "Missing required fields"}), 400

    symbol = data["symbol"]
    side = data["side"].upper()

    print(f"📩 Webhook received: {data}")

    if side == "BUY":
        execute_long_margin(symbol)
    elif side == "SELL":
        execute_short_margin(symbol)
    else:
        return jsonify({"error": "Invalid side"}), 400

    return jsonify({"status": "ok"})


# ====== FLASK EXECUTION ======
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))

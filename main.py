import os
import time
import hmac
import hashlib
import requests
import functools
from decimal import Decimal, ROUND_DOWN
from flask import Flask, request, jsonify

# --- CONFIGURACIÓN ---
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

# ---------------- HELPERS ----------------

def sign_params_query(params: dict, secret: str):
    query = "&".join([f"{k}={params[k]}" for k in params.keys()])
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query, signature

def send_signed_request(method: str, path: str, params: dict):
    query, sig = sign_params_query(params, BINANCE_API_SECRET)
    url = f"{BASE_URL}{path}?{query}&signature={sig}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    if DRY_RUN:
        print(f"[DRY_RUN] {method} {url}")
        return {"dry_run": True}
    if method.upper() == "GET":
        r = requests.get(url, headers=headers, timeout=15)
    elif method.upper() == "POST":
        r = requests.post(url, headers=headers, timeout=15)
    elif method.upper() == "DELETE":
        r = requests.delete(url, headers=headers, timeout=15)
    else:
        raise ValueError("Unsupported method")
    r.raise_for_status()
    return r.json()

def get_price(symbol: str) -> float:
    r = requests.get(f"{BASE_URL}/api/v3/ticker/price", params={"symbol": symbol}, timeout=10)
    r.raise_for_status()
    return float(r.json()["price"])

def get_margin_balance(asset="USDC") -> float:
    ts = int(time.time() * 1000)
    params = {"timestamp": ts}
    q, sig = sign_params_query(params, BINANCE_API_SECRET)
    url = f"{BASE_URL}/sapi/v1/margin/account?{q}&signature={sig}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    if DRY_RUN:
        print(f"[DRY_RUN] get_margin_balance({asset}) -> simulated 1000")
        return 1000.0
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()
    ua = next((x for x in data.get("userAssets", []) if x.get("asset") == asset), None)
    if not ua:
        return 0.0
    return float(ua.get("free", 0.0))

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

def ceil_to_step_str(value: float, step_str: str) -> str:
    v = Decimal(str(value))
    step = Decimal(str(step_str))
    div = (v / step)
    n = div.to_integral_value(rounding=ROUND_DOWN)
    if (div - n) > Decimal("0"):
        n = n + 1
    d = n * step
    decimals = -step.as_tuple().exponent if step.as_tuple().exponent < 0 else 0
    return format(d.quantize(Decimal(1).scaleb(-decimals)), f".{decimals}f")

def detect_base_asset(symbol: str):
    quote_candidates = ["USDC","USDT","BUSD","EUR","USD","BTC","ETH","BNB"]
    for q in quote_candidates:
        if symbol.endswith(q):
            return symbol[:-len(q)], q
    return symbol[:-4], symbol[-4:]

# ---------------- MARGIN OPS ----------------

def margin_borrow(asset: str, amount: float):
    ts = int(time.time() * 1000)
    params = {"asset": asset, "amount": format(amount, "f"), "timestamp": ts}
    try:
        resp = send_signed_request("POST", "/sapi/v1/margin/loan", params)
        print(f"📥 Borrowed {amount} {asset}")
        return resp
    except Exception as e:
        print(f"⚠️ Borrow failed for {asset}: {e}")
        return None

def cancel_margin_open_orders(symbol: str):
    ts = int(time.time() * 1000)
    params = {"symbol": symbol, "timestamp": ts}
    try:
        send_signed_request("DELETE", "/sapi/v1/margin/openOrders", params)
        print(f"🗑 Cancelled openOrders for {symbol}")
    except Exception as e:
        print(f"⚠️ Could not cancel openOrders for {symbol}: {e}")

# --- CLOSE (para cerrar posiciones) ---
def close_position_margin(symbol: str, side="SELL"):
    try:
        cancel_margin_open_orders(symbol)
        base_asset, _ = detect_base_asset(symbol)
        qty = get_margin_balance(base_asset)
        if qty <= 0:
            print(f"ℹ️ No balance to close for {symbol}")
            return None
        lot = get_lot_info(symbol)
        if not lot:
            print(f"⚠️ No lot info for {symbol}, cannot close")
            return None
        qty_str = floor_to_step_str(qty, lot["stepSize_str"])
        if float(qty_str) < lot["minQty"]:
            print(f"⚠️ Close qty {qty_str} < minQty {lot['minQty']}")
            return None
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": qty_str,
            "timestamp": int(time.time() * 1000),
        }
        resp = send_signed_request("POST", "/sapi/v1/margin/order", params)
        print(f"✅ Closed {symbol} ({side}) qty={qty_str}")
        return resp
    except Exception as e:
        print(f"❌ Error closing {symbol}: {e}")
        return None

# --- LONG (BUY) ---
def execute_long_margin(symbol: str, entry_price=None, sl=None, tp=None):
    try:
        print(f"🟢 LONG signal for {symbol}")
        lot = get_lot_info(symbol)
        entry_price = float(entry_price or get_price(symbol))
        usdc_balance = get_margin_balance("USDC")
        if usdc_balance < 1:
            margin_borrow("USDC", 50)
            usdc_balance = get_margin_balance("USDC")

        usdc_to_use = min(usdc_balance * BUY_PCT, MAX_USDC_PER_ORDER)
        quote_qty_str = format(Decimal(str(usdc_to_use)).quantize(Decimal("0.00000001")), "f")
        cancel_margin_open_orders(symbol)
        params_buy = {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quoteOrderQty": quote_qty_str,
            "timestamp": int(time.time() * 1000),
        }
        buy_resp = send_signed_request("POST", "/sapi/v1/margin/order", params_buy)
        print(f"✅ LONG opened {symbol} with {usdc_to_use} USDC")
        return buy_resp
    except Exception as e:
        print(f"❌ Error in execute_long_margin: {e}")
        return {"error": str(e)}

# --- SHORT (SELL) ---
def execute_short_margin(symbol: str, entry_price=None, sl=None, tp=None):
    try:
        print(f"🔴 SHORT signal for {symbol}")
        base_asset, quote = detect_base_asset(symbol)
        lot = get_lot_info(symbol)
        entry_price = float(entry_price or get_price(symbol))
        usdc_balance = get_margin_balance("USDC")
        usdc_to_use = min(usdc_balance * BUY_PCT, MAX_USDC_PER_ORDER)
        qty = usdc_to_use / entry_price
        qty_str = floor_to_step_str(qty, lot["stepSize_str"])
        if float(qty_str) < lot["minQty"]:
            print(f"⚠️ Qty {qty_str} < minQty {lot['minQty']}")
            return None
        margin_borrow(base_asset, float(qty_str))
        cancel_margin_open_orders(symbol)
        params_sell = {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": qty_str,
            "timestamp": int(time.time() * 1000),
        }
        sell_resp = send_signed_request("POST", "/sapi/v1/margin/order", params_sell)
        print(f"✅ SHORT opened {symbol} qty={qty_str}")
        return sell_resp
    except Exception as e:
        print(f"❌ Error in execute_short_margin: {e}")
        return {"error": str(e)}

# ---------------- WEBHOOK ----------------

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        symbol = data.get("symbol", "").upper()
        side = data.get("side", "").upper()
        entry = data.get("entry_price")
        sl = data.get("sl")
        tp = data.get("tp")

        if side == "BUY":
            resp = execute_long_margin(symbol, entry_price=entry, sl=sl, tp=tp)
            return jsonify({"status": "LONG executed", "resp": resp}), 200
        elif side == "SELL":
            resp = execute_short_margin(symbol, entry_price=entry, sl=sl, tp=tp)
            return jsonify({"status": "SHORT executed", "resp": resp}), 200
        elif side == "CLOSE_LONG":
            resp = close_position_margin(symbol, side="SELL")
            return jsonify({"status": "CLOSE_LONG done", "resp": resp}), 200
        elif side == "CLOSE_SHORT":
            resp = close_position_margin(symbol, side="BUY")
            return jsonify({"status": "CLOSE_SHORT done", "resp": resp}), 200
        else:
            return jsonify({"status": "ignored", "side": side}), 200
    except Exception as e:
        print(f"❌ Webhook error: {e}")
        return jsonify({"error": str(e)}), 500

# ---------------- MAIN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

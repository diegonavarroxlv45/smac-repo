import os
import time
import hmac
import hashlib
import requests
import functools
import json
from decimal import Decimal, ROUND_DOWN
from flask import Flask, request, jsonify

# ---------------- CONFIGURACIÓN ----------------
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
BASE_URL = "https://api.binance.com"

# Risk / sizing & behavior
BUY_PCT = float(os.getenv("BUY_PCT", "0.04"))
MAX_USDC_PER_ORDER = float(os.getenv("MAX_USDC_PER_ORDER", "100"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.97"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "1.06"))
COMMISSION_BUFFER = Decimal(os.getenv("COMMISSION_BUFFER", "0.999"))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes")

# logs flush
print = functools.partial(print, flush=True)
app = Flask(__name__)

# ---------------- HELPERS ----------------

def _now_ms():
    return int(time.time() * 1000)

def sign_params_query(params: dict, secret: str) -> (str, str):
    """
    Construye query string en el orden de inserción y devuelve (query_string, signature).
    """
    # Order-preserving dict expected
    query = "&".join([f"{k}={params[k]}" for k in params.keys()])
    sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query, sig

def _request_with_retries(method: str, url: str, headers: dict = None, timeout: int = 15, max_retries: int = 3):
    """
    Petición HTTP con reintento simple (exp backoff).
    """
    delay = 0.5
    for attempt in range(1, max_retries + 1):
        try:
            if method.upper() == "GET":
                r = requests.get(url, headers=headers, timeout=timeout)
            elif method.upper() == "POST":
                r = requests.post(url, headers=headers, timeout=timeout)
            elif method.upper() == "DELETE":
                r = requests.delete(url, headers=headers, timeout=timeout)
            else:
                raise ValueError("Unsupported HTTP method")
            # raise_for_status para pasar al except si 4xx/5xx
            r.raise_for_status()
            # intentar parse json (muchas respuestas son json)
            try:
                return r.json()
            except Exception:
                return r.text
        except requests.HTTPError as e:
            # para 4xx no siempre merece la pena reintentar, pero haremos hasta max_retries
            print(f"⚠️ HTTP error (attempt {attempt}/{max_retries}) for {url}: {e} - response: {getattr(e.response, 'text', None)}")
            if attempt == max_retries:
                raise
            time.sleep(delay)
            delay *= 2
        except Exception as e:
            print(f"⚠️ Request exception (attempt {attempt}/{max_retries}) for {url}: {e}")
            if attempt == max_retries:
                raise
            time.sleep(delay)
            delay *= 2

def send_signed_request(method: str, path: str, params: dict, max_retries: int = 3):
    """
    Envía petición firmada (query + signature) y devuelve json o lanza excepción.
    """
    # Añadir timestamp si no está presente
    if "timestamp" not in params:
        params["timestamp"] = _now_ms()
    q, sig = sign_params_query(params, BINANCE_API_SECRET)
    url = f"{BASE_URL}{path}?{q}&signature={sig}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    if DRY_RUN:
        print(f"[DRY_RUN] {method} {url}")
        return {"dry_run": True}
    return _request_with_retries(method, url, headers=headers, max_retries=max_retries)

def get_price(symbol: str) -> float:
    r = requests.get(f"{BASE_URL}/api/v3/ticker/price", params={"symbol": symbol}, timeout=10)
    r.raise_for_status()
    return float(r.json()["price"])

def get_margin_balance(asset="USDC") -> float:
    """
    Lee saldo 'free' en la cuenta Margin (Cross) usando /sapi/v1/margin/account (userAssets).
    """
    ts = _now_ms()
    params = {"timestamp": ts}
    q, sig = sign_params_query(params, BINANCE_API_SECRET)
    url = f"{BASE_URL}/sapi/v1/margin/account?{q}&signature={sig}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    if DRY_RUN:
        print(f"[DRY_RUN] get_margin_balance({asset}) -> simulated 1000")
        return 1000.0
    data = _request_with_retries("GET", url, headers=headers)
    # En margin la respuesta tiene "userAssets"
    ua = next((x for x in data.get("userAssets", []) if x.get("asset") == asset), None)
    return float(ua.get("free", 0.0)) if ua else 0.0

def get_spot_balance(asset="USDC") -> float:
    """
    Lee saldo 'free' en la cuenta Spot usando /api/v3/account (balances).
    Función incluida por claridad; no se usa directamente en el flujo margin.
    """
    ts = _now_ms()
    params = {"timestamp": ts}
    q, sig = sign_params_query(params, BINANCE_API_SECRET)
    url = f"{BASE_URL}/api/v3/account?{q}&signature={sig}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    if DRY_RUN:
        print(f"[DRY_RUN] get_spot_balance({asset}) -> simulated 1000")
        return 1000.0
    data = _request_with_retries("GET", url, headers=headers)
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

def cancel_margin_open_orders(symbol: str):
    ts = _now_ms()
    params = {"symbol": symbol, "timestamp": ts}
    try:
        resp = send_signed_request("DELETE", "/sapi/v1/margin/openOrders", params)
        print(f"🗑 Cancelled openOrders for {symbol} (resp ok)")
        return resp
    except Exception as e:
        # no bloquear, pero dejar constancia
        print(f"⚠️ Could not cancel openOrders for {symbol}: {e}")
        return None

def margin_borrow(asset: str, amount: float):
    ts = _now_ms()
    params = {"asset": asset, "amount": format(Decimal(str(amount)), "f"), "timestamp": ts}
    try:
        resp = send_signed_request("POST", "/sapi/v1/margin/loan", params)
        print(f"📥 Borrowed {amount} {asset}")
        return resp
    except Exception as e:
        print(f"⚠️ Borrow failed for {asset}: {e}")
        return None

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
        params = {"symbol": symbol, "side": side, "type": "MARKET", "quantity": qty_str, "timestamp": _now_ms()}
        resp = send_signed_request("POST", "/sapi/v1/margin/order", params)
        print(f"✅ Closed {symbol} ({side}) qty={qty_str}")
        return resp
    except Exception as e:
        print(f"❌ Error closing {symbol}: {e}")
        return None

# ---- Buy on margin: try quoteOrderQty then fallback to quantity (rounded) ----
def execute_long_margin(symbol: str, entry_price=None, sl=None, tp=None):
    try:
        print(f"🟢 LONG signal for {symbol}")
        lot = get_lot_info(symbol)
        if not lot:
            print(f"⚠️ No lot info for {symbol}, aborting LONG.")
            return {"error": "no_lot"}
        entry_price = float(entry_price or get_price(symbol))
        usdc_balance = get_margin_balance("USDC")
        if usdc_balance < 1:
            # small borrow to ensure operation (configurable)
            margin_borrow("USDC", 50)
            usdc_balance = get_margin_balance("USDC")

        usdc_to_use = min(usdc_balance * BUY_PCT, MAX_USDC_PER_ORDER)
        quote_qty_str = format(Decimal(str(usdc_to_use)).quantize(Decimal("0.00000001")), "f")
        cancel_margin_open_orders(symbol)

        # Try quoteOrderQty first (preferred)
        params_buy = {"symbol": symbol, "side": "BUY", "type": "MARKET", "quoteOrderQty": quote_qty_str, "timestamp": _now_ms()}
        try:
            buy_resp = send_signed_request("POST", "/sapi/v1/margin/order", params_buy)
            executed_qty = float(buy_resp.get("executedQty", 0) or 0)
            spent_quote = float(buy_resp.get("cummulativeQuoteQty", 0) or 0)
            print(f"✅ Margin BUY executed {symbol}: executedQty={executed_qty}, spent≈{spent_quote}")
            # place SL/TP separate orders if desired (handled elsewhere)
            return {"order": buy_resp}
        except Exception as e_q:
            print(f"⚠️ quoteOrderQty buy failed ({e_q}), attempting quantity-based fallback.")
            # fallback: compute qty from usdc_to_use and entry_price and round to step
            raw_qty = (Decimal(str(usdc_to_use)) / Decimal(str(entry_price)))
            qty_str = floor_to_step_str(float(raw_qty), lot["stepSize_str"])
            if float(qty_str) < lot["minQty"]:
                msg = f"Qty fallback {qty_str} < minQty {lot['minQty']}"
                print("⚠️", msg)
                return {"error": "qty_too_small", "detail": msg}
            params_buy_qty = {"symbol": symbol, "side": "BUY", "type": "MARKET", "quantity": qty_str, "timestamp": _now_ms()}
            buy_resp2 = send_signed_request("POST", "/sapi/v1/margin/order", params_buy_qty)
            print(f"✅ Margin BUY executed fallback {symbol}: executedQty={buy_resp2.get('executedQty')}")
            return {"order": buy_resp2}

    except Exception as e:
        print(f"❌ Error in execute_long_margin: {e}")
        return {"error": str(e)}

def execute_short_margin(symbol: str, entry_price=None, sl=None, tp=None):
    """
    Borrow base asset, then SELL on margin to open a short.
    """
    try:
        print(f"🔴 SHORT signal for {symbol}")
        base_asset, quote = detect_base_asset(symbol)
        lot = get_lot_info(symbol)
        if not lot:
            print(f"⚠️ No lot info for {symbol}, aborting SHORT.")
            return {"error": "no_lot"}
        entry_price = float(entry_price or get_price(symbol))
        usdc_balance = get_margin_balance("USDC")
        usdc_to_use = min(usdc_balance * BUY_PCT, MAX_USDC_PER_ORDER)
        # compute qty to short based on usdc_to_use / price
        qty = float(Decimal(str(usdc_to_use)) / Decimal(str(entry_price)))
        qty_str = floor_to_step_str(qty, lot["stepSize_str"])
        if float(qty_str) < lot["minQty"]:
            msg = f"Qty {qty_str} < minQty {lot['minQty']}"
            print("⚠️", msg)
            return {"error": "qty_too_small", "detail": msg}
        # borrow base asset first
        borrow_resp = margin_borrow(base_asset, float(qty_str))
        if not borrow_resp:
            print(f"⚠️ Borrow failed for {base_asset}; aborting SHORT for {symbol}")
            return {"error": "borrow_failed"}
        cancel_margin_open_orders(symbol)
        params_sell = {"symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": qty_str, "timestamp": _now_ms()}
        sell_resp = send_signed_request("POST", "/sapi/v1/margin/order", params_sell)
        print(f"✅ SHORT opened {symbol} qty={qty_str}")
        return {"order": sell_resp}
    except Exception as e:
        print(f"❌ Error in execute_short_margin: {e}")
        return {"error": str(e)}

# ---------------- WEBHOOK ----------------

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        # Safe JSON parse: silent=True prevents Flask raising BadRequest for non-json
        data = request.get_json(silent=True)
        # Accept form-encoded payload too
        if data is None:
            if request.form:
                data = request.form.to_dict()
                print("ℹ️ Received form-encoded webhook payload; converted to dict.")
            else:
                raw = (request.data or b"").decode(errors="replace")
                print(f"⚠️ Ignored non-JSON or empty webhook payload. Raw body: {raw!r}")
                # respond 200 to avoid aggressive re-delivery from TradingView
                return jsonify({"status": "ignored", "reason": "non-json-or-empty-payload"}), 200

        # Normalize fields
        symbol = (data.get("symbol") or "").strip().upper()
        side = (data.get("side") or "").strip().upper()
        entry = data.get("entry_price") or data.get("entry") or None
        sl = data.get("sl")
        tp = data.get("tp")

        if not symbol or not side:
            print(f"⚠️ Ignored webhook: missing symbol or side. data={data}")
            return jsonify({"status": "ignored", "reason": "missing_symbol_or_side"}), 200

        allowed_sides = {"BUY", "SELL", "CLOSE_LONG", "CLOSE_SHORT", "LONG", "SHORT"}
        if side not in allowed_sides:
            print(f"⚠️ Ignored webhook: unsupported side={side}. data={data}")
            return jsonify({"status": "ignored", "reason": "unsupported_side"}), 200

        # Map aliases
        if side == "LONG":
            side = "BUY"
        elif side == "SHORT":
            side = "SELL"

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
            print(f"⚠️ Unhandled side after mapping: {side}")
            return jsonify({"status": "ignored", "reason": "unhandled"}), 200

    except Exception as e:
        print(f"❌ Webhook error: {e}")
        return jsonify({"error": str(e)}), 500

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))

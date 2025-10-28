import os
import time
import hmac
import hashlib
import requests
import functools
from decimal import Decimal, ROUND_DOWN, ROUND_UP, InvalidOperation
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
                try:
                    return resp.json()
                except Exception:
                    return resp.text
            else:
                print(f"⚠️ Attempt {i+1} failed: {resp.text}")
        except Exception as e:
            print(f"⚠️ Request error: {e}")
        time.sleep(1)
    raise Exception("❌ Request failed after retries")


def send_signed_request(http_method: str, path: str, payload: dict):
    """
    Ensure timestamp, build deterministic query (sorted keys), sign and request.
    Logs failures for easier debugging.
    """
    if "timestamp" not in payload:
        payload["timestamp"] = _now_ms()
    # deterministic order for easier debugging / reproducibility
    items = [f"{k}={payload[k]}" for k in sorted(payload.keys())]
    query_string = "&".join(items)
    signature = hmac.new(BINANCE_API_SECRET.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE_URL}{path}?{query_string}&signature={signature}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    try:
        return _request_with_retries(http_method, url, headers=headers)
    except Exception as e:
        print(f"⚠️ send_signed_request failed for path={path} payload={payload}: {e}")
        raise


def floor_to_step_str(value, step):
    d = Decimal(str(step))
    return str(Decimal(str(value)).quantize(d, rounding=ROUND_DOWN))


def format_price_to_tick(price: float, tick_size_str: str, rounding=ROUND_DOWN) -> str:
    """
    Ajusta `price` al múltiplo de `tick_size_str` y devuelve una string con los decimales correctos.
    Levanta ValueError si la entrada no es válida (será capturado donde se use).
    """
    try:
        d_tick = Decimal(str(tick_size_str))
        p = Decimal(str(price)).quantize(d_tick, rounding=rounding)
    except (InvalidOperation, ValueError, TypeError) as e:
        raise ValueError(f"format_price_to_tick: invalid input price={price} tick={tick_size_str}: {e}")
    decimals = -d_tick.as_tuple().exponent if d_tick.as_tuple().exponent < 0 else 0
    return f"{p:.{decimals}f}"

# ====== BALANCE & MARKET DATA ======
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

# ====== PRE-TRADE CLEANUP ======
def handle_pre_trade_cleanup(symbol: str):
    """Cancels previous SL/TP and repays partially if there is debt."""
    base_asset = symbol.replace("USDC", "")
    print(f"🔄 Cleaning previous environment for {symbol}...")

    # 1️⃣ Cancel pending orders
    try:
        params = {"symbol": symbol, "timestamp": _now_ms()}
        send_signed_request("DELETE", "/sapi/v1/margin/openOrders", params)
        print(f"🧹 Pending orders for {symbol} canceled")
    except Exception as e:
        print(f"⚠️ Couldn't cancel orders for {symbol}: {e}")

    # 2️⃣ Partial repay (min debt between borrowed and free)
    try:
        ts = _now_ms()
        params_acc = {"timestamp": ts}
        q, sig = sign_params_query(params_acc, BINANCE_API_SECRET)
        url = f"{BASE_URL}/sapi/v1/margin/account?{q}&signature={sig}"
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        acc_data = _request_with_retries("GET", url, headers=headers)

        asset_data = next((a for a in acc_data["userAssets"] if a["asset"] == base_asset), None)
        if asset_data:
            borrowed = float(asset_data["borrowed"])
            free = float(asset_data["free"])

            if borrowed > 0:
                repay_amount = min(borrowed, free)
                if repay_amount > 0:
                    repay_params = {
                        "asset": base_asset,
                        "amount": str(repay_amount),
                        "timestamp": _now_ms(),
                    }
                    send_signed_request("POST", "/sapi/v1/margin/repay", repay_params)
                    print(f"💰 Partial repay executed: {repay_amount} {base_asset}")
                else:
                    print(f"ℹ️ No free balance to repay {base_asset}")
            else:
                print(f"✅ No active debt in {base_asset}")
        else:
            print(f"ℹ️ {base_asset} doesn't appear on margin (no prevoius activity)")
    except Exception as e:
        print(f"⚠️ Error trying repay debt in {base_asset}: {e}")

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

        if executed_qty > 0:
            place_sl_tp_margin(symbol, "BUY", entry_price, executed_qty, lot)


def execute_short_margin(symbol):
    lot = get_symbol_lot(symbol)
    balance_usdc = get_balance_margin("USDC")
    entry_price_data = _request_with_retries("GET", f"{BASE_URL}/api/v3/ticker/price?symbol={symbol}")
    entry_price = float(entry_price_data["price"])

    borrow_amount = (balance_usdc * BUY_PCT) / entry_price
    borrow_amount = Decimal(str(borrow_amount)).quantize(Decimal(str(lot["stepSize"])), rounding=ROUND_DOWN)

    borrow_params = {"asset": symbol.replace("USDC", ""), "amount": str(borrow_amount), "timestamp": _now_ms()}
    send_signed_request("POST", "/sapi/v1/margin/loan", borrow_params)
    print(f"📥 Borrowed {borrow_amount} {symbol.replace('USDC', '')}")

    qty_str = floor_to_step_str(borrow_amount, lot["stepSize_str"])
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
    entry_price = float(resp.get("price", entry_price))
    print(f"✅ SHORT opened {symbol} qty={qty_str}")

    if float(qty_str) > 0:
        place_sl_tp_margin(symbol, "SELL", entry_price, float(qty_str), lot)

# ====== SL/TP FUNCTIONS ======
def place_sl_tp_margin(symbol: str, side: str, entry_price: float, executed_qty: float, lot: dict):
    """Places LIMIT orders SL/TP with tickSize rounding and strict validation."""
    try:
        sl_side = "SELL" if side == "BUY" else "BUY"

        if side == "BUY":
            sl_price = entry_price * STOP_LOSS_PCT
            tp_price = entry_price * TAKE_PROFIT_PCT
            sl_round = ROUND_DOWN
            tp_round = ROUND_UP
        else:
            sl_price = entry_price / STOP_LOSS_PCT
            tp_price = entry_price / TAKE_PROFIT_PCT
            sl_round = ROUND_UP
            tp_round = ROUND_DOWN

        # use format_price_to_tick to guarantee correct decimals & multiples
        try:
            sl_price_str = format_price_to_tick(sl_price, lot["tickSize_str"], rounding=sl_round)
            tp_price_str = format_price_to_tick(tp_price, lot["tickSize_str"], rounding=tp_round)
        except Exception as e:
            print(f"⚠️ Price formatting error for {symbol}: {e}")
            return False

        qty_str = floor_to_step_str(executed_qty * float(COMMISSION_BUFFER), lot["stepSize_str"])
        qty_f = None
        try:
            qty_f = float(qty_str)
        except Exception:
            print(f"⚠️ Invalid qty_str computed for {symbol}: {qty_str}")
            return False

        for label, price_str in [("SL", sl_price_str), ("TP", tp_price_str)]:
            # validation: price not empty, parseable, and notional >= minNotional if present
            if not price_str or price_str.strip() == "":
                print(f"⚠️ Skipping {label} for {symbol}: computed price empty or invalid: {price_str!r}")
                continue
            try:
                price_f = float(price_str)
            except Exception:
                print(f"⚠️ Skipping {label} for {symbol}: price not parseable as float: {price_str!r}")
                continue

            notional = price_f * qty_f
            min_notional = lot.get("minNotional", 0.0)
            if notional < min_notional:
                print(f"⚠️ Skipping {label} for {symbol}: notional {notional:.8f} < minNotional {min_notional}")
                continue

            params = {
                "symbol": symbol,
                "side": sl_side,
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": qty_str,
                "price": price_str,
                "timestamp": _now_ms(),
            }

            # debug print for clarity
            print(f"📤 Placing {label} for {symbol}: side={sl_side} qty={qty_str} price={price_str}")
            try:
                send_signed_request("POST", "/sapi/v1/margin/order", params)
                print(f"📈 {label} order placed for {symbol} at {price_str} ({sl_side})")
            except Exception as e:
                print(f"⚠️ Could not place {label} for {symbol}: {e}")

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

    # 🔄 SL/TP CLEANING + PARTIAL REPAY BEFORE NEW OPERATION
    handle_pre_trade_cleanup(symbol)

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

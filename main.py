import os
import time
import hmac
import hashlib
import requests
import functools
from decimal import Decimal, ROUND_DOWN, ROUND_UP
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
    # ensure timestamp present
    if "timestamp" not in payload:
        payload["timestamp"] = _now_ms()
    query_string = "&".join([f"{k}={v}" for k, v in payload.items()])
    signature = hmac.new(BINANCE_API_SECRET.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE_URL}{path}?{query_string}&signature={signature}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    return _request_with_retries(http_method, url, headers=headers)


def floor_to_step_str(value, step_str):
    """
    Redondea hacia abajo al múltiplo de step_str, devuelve string con los decimales del step.
    """
    step = Decimal(str(step_str))
    v = Decimal(str(value))
    n = (v // step) * step
    decimals = -step.as_tuple().exponent if step.as_tuple().exponent < 0 else 0
    q = n.quantize(Decimal(1).scaleb(-decimals))
    return format(q, f".{decimals}f")


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
    """
    Devuelve información útil: stepSize_str, tickSize_str, minQty, minNotional.
    """
    data = _request_with_retries("GET", f"{BASE_URL}/api/v3/exchangeInfo")
    for s in data["symbols"]:
        if s["symbol"] == symbol:
            fs = next((f for f in s["filters"] if f["filterType"] == "LOT_SIZE"), None)
            ts = next((f for f in s["filters"] if f["filterType"] == "PRICE_FILTER"), None)
            mnf = next((f for f in s["filters"] if f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL")), None)
            if not fs or not ts:
                raise Exception(f"Missing LOT_SIZE or PRICE_FILTER for {symbol}")
            minNotional = float(mnf.get("minNotional") or mnf.get("notional") or 0.0) if mnf else 0.0
            return {
                "stepSize_str": fs["stepSize"],
                "stepSize": float(fs["stepSize"]),
                "minQty": float(fs.get("minQty", 0.0)),
                "tickSize_str": ts["tickSize"],
                "tickSize": float(ts["tickSize"]),
                "minNotional": minNotional,
            }
    raise Exception(f"Symbol not found: {symbol}")


# ====== PRICE ADJUST (tickSize) ======
def format_price_to_tick(price: float, tick_size_str: str, rounding=ROUND_DOWN) -> str:
    """
    Ajusta price a múltiplos de tick_size. rounding puede ser ROUND_DOWN o ROUND_UP.
    Devuelve string con decimales correctos.
    """
    d_tick = Decimal(str(tick_size_str))
    p = Decimal(str(price)).quantize(d_tick, rounding=rounding)
    decimals = -d_tick.as_tuple().exponent if d_tick.as_tuple().exponent < 0 else 0
    return f"{p:.{decimals}f}"


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
def execute_long_margin(symbol, webhook_data=None):
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
        return {"dry_run": True}

    resp = send_signed_request("POST", "/sapi/v1/margin/order", params)

    # calcular executedQty y precio efectivo si hay fills
    executed_qty = 0.0
    entry_price = None
    if isinstance(resp, dict) and "fills" in resp:
        executed_qty = sum(float(f["qty"]) for f in resp["fills"])
        spent_quote = sum(float(f["price"]) * float(f["qty"]) for f in resp["fills"])
        entry_price = (spent_quote / executed_qty) if executed_qty else None
    # fallback
    if not entry_price and isinstance(resp, dict):
        try:
            executed_qty = float(resp.get("executedQty", 0) or 0)
            cumm = float(resp.get("cummulativeQuoteQty", 0) or 0)
            if executed_qty:
                entry_price = cumm / executed_qty
        except Exception:
            pass

    print(f"✅ Margin BUY executed {symbol}: executedQty={executed_qty}, spent≈{(entry_price * executed_qty) if (entry_price and executed_qty) else 'unknown'}")

    if executed_qty > 0 and entry_price:
        # use sl/tp from webhook if provided
        sl_from_web = None
        tp_from_web = None
        if webhook_data:
            sl_from_web = webhook_data.get("sl")
            tp_from_web = webhook_data.get("tp")
        place_sl_tp_margin(symbol, "BUY", entry_price, executed_qty, lot, sl_override=sl_from_web, tp_override=tp_from_web)

    return {"order": resp}


def execute_short_margin(symbol, webhook_data=None):
    """
    Calcular qty a pedir prestado en base a usdc disponible, validar minQty/minNotional,
    pedir prestado esa cantidad, y venderla en MARKET.
    """
    lot = get_symbol_lot(symbol)
    balance_usdc = get_balance_margin("USDC")

    # get a price estimate from ticker
    try:
        r = _request_with_retries("GET", f"{BASE_URL}/api/v3/ticker/price", params={"symbol": symbol})
        price_est = float(r.get("price", 0))
    except Exception as e:
        print(f"⚠️ Could not fetch price for {symbol}: {e}")
        return {"error": "price_fetch_failed"}

    if price_est <= 0:
        print("⚠️ Price estimate invalid, aborting short.")
        return {"error": "invalid_price_est"}

    raw_qty = Decimal(str(balance_usdc * BUY_PCT)) / Decimal(str(price_est))
    borrow_amount = float(raw_qty.quantize(Decimal(str(lot["stepSize_str"])), rounding=ROUND_DOWN))

    # validations
    if borrow_amount <= 0 or borrow_amount < lot.get("minQty", 0.0):
        msg = f"Qty {borrow_amount} < minQty {lot.get('minQty')}"
        print("⚠️", msg)
        return {"error": "qty_too_small", "detail": msg}

    if (borrow_amount * price_est) < lot.get("minNotional", 0.0):
        msg = f"Notional {borrow_amount * price_est:.8f} < minNotional {lot.get('minNotional')}"
        print("⚠️", msg)
        return {"error": "notional_too_small", "detail": msg}

    # pedir prestado exactamente borrow_amount
    borrow_params = {"asset": symbol.replace("USDC", ""), "amount": format(Decimal(str(borrow_amount)), "f"), "timestamp": _now_ms()}
    if DRY_RUN:
        print(f"[DRY_RUN] Borrow {borrow_params}")
        borrowed_qty = borrow_amount
    else:
        borrow_resp = send_signed_request("POST", "/sapi/v1/margin/loan", borrow_params)
        borrowed_qty = None
        if isinstance(borrow_resp, dict):
            borrowed_qty = float(borrow_resp.get("amount") or borrow_resp.get("qty") or borrow_amount)
        else:
            borrowed_qty = borrow_amount

    print(f"📥 Borrowed {borrowed_qty} {symbol.replace('USDC','')} (requested {borrow_amount})")

    # cancelar open orders y enviar market sell
    cancel_params = {"symbol": symbol, "timestamp": _now_ms()}
    try:
        send_signed_request("DELETE", "/sapi/v1/margin/openOrders", cancel_params)
    except Exception as e:
        print(f"⚠️ Could not cancel openOrders for {symbol}: {e}")

    qty_str = floor_to_step_str(float(borrowed_qty), lot["stepSize_str"])
    if float(qty_str) < lot.get("minQty", 0.0):
        msg = f"After borrow qty {qty_str} < minQty {lot.get('minQty')}"
        print("⚠️", msg)
        return {"error": "borrowed_qty_too_small", "detail": msg}

    params = {"symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": qty_str, "timestamp": _now_ms()}

    if DRY_RUN:
        print(f"[DRY_RUN] Margin SHORT {symbol}: quantity={qty_str}")
        return {"dry_run": True}

    resp = send_signed_request("POST", "/sapi/v1/margin/order", params)

    executed_qty = 0.0
    entry_price_effective = None
    if isinstance(resp, dict) and "fills" in resp:
        executed_qty = sum(float(f["qty"]) for f in resp["fills"])
        spent_quote = sum(float(f["price"]) * float(f["qty"]) for f in resp["fills"])
        entry_price_effective = (spent_quote / executed_qty) if executed_qty else None
    if not entry_price_effective and isinstance(resp, dict):
        try:
            executed_qty = float(resp.get("executedQty", 0) or 0)
            cumm = float(resp.get("cummulativeQuoteQty", 0) or 0)
            if executed_qty:
                entry_price_effective = cumm / executed_qty
        except Exception:
            pass

    print(f"✅ SHORT opened {symbol} qty={qty_str} (executed={executed_qty})")

    # place SL/TP using webhook sl/tp when provided
    sl_from_web = None
    tp_from_web = None
    if webhook_data:
        sl_from_web = webhook_data.get("sl")
        tp_from_web = webhook_data.get("tp")

    if executed_qty > 0 and entry_price_effective:
        place_sl_tp_margin(symbol, "SELL", entry_price_effective, executed_qty, lot, sl_override=sl_from_web, tp_override=tp_from_web)

    return {"order": resp}


# ====== SL/TP FUNCTIONS ======
def place_sl_tp_margin(symbol: str, side: str, entry_price: float, executed_qty: float, lot: dict, sl_override=None, tp_override=None):
    """
    Coloca SL y TP respetando tickSize y minNotional.
    SL ahora usa STOP_MARKET, mientras que TP sigue siendo LIMIT.
    Usa valores de webhook (sl_override/tp_override) si vienen; si no, calcula con STOP_LOSS_PCT/TAKE_PROFIT_PCT.
    """
    try:
        sl_side = "SELL" if side == "BUY" else "BUY"

        if sl_override is not None:
            sl_price = float(sl_override)
            sl_rounding = ROUND_DOWN if side == "BUY" else ROUND_UP
        else:
            sl_price = entry_price * STOP_LOSS_PCT if side == "BUY" else entry_price / STOP_LOSS_PCT
            sl_rounding = ROUND_DOWN if side == "BUY" else ROUND_UP

        if tp_override is not None:
            tp_price = float(tp_override)
            tp_rounding = ROUND_UP if side == "BUY" else ROUND_DOWN
        else:
            tp_price = entry_price * TAKE_PROFIT_PCT if side == "BUY" else entry_price / TAKE_PROFIT_PCT
            tp_rounding = ROUND_UP if side == "BUY" else ROUND_DOWN

        # Ajust to tickSize
        sl_price_str = format_price_to_tick(sl_price, lot["tickSize_str"], rounding=sl_rounding)
        tp_price_str = format_price_to_tick(tp_price, lot["tickSize_str"], rounding=tp_rounding)

        qty_str = floor_to_step_str(executed_qty * float(COMMISSION_BUFFER), lot["stepSize_str"])
        qty_f = float(qty_str)

        for label, price_str in [("SL", sl_price_str), ("TP", tp_price_str)]:
            try:
                price_f = float(price_str)
            except Exception:
                print(f"⚠️ {label} price malformed for {symbol}: {price_str}, skipping")
                continue

            if price_f <= 0 or price_f < lot["tickSize"]:
                print(f"⚠️ Skipping {label} for {symbol}: price {price_f} < tickSize {lot['tickSize']}")
                continue

            notional = price_f * qty_f
            if notional < lot.get("minNotional", 0.0):
                print(f"⚠️ Skipping {label} for {symbol}: notional {notional:.8f} < minNotional {lot.get('minNotional')}")
                continue

            # ✅ Types of order for SL & TP
            if label == "SL":
                params = {
                    "symbol": symbol,
                    "side": sl_side,
                    "type": "STOP_LOSS",
                    "stopPrice": price_str,
                    "quantity": qty_str,
                    "timestamp": _now_ms(),
                }
            else:  # TP
                params = {
                    "symbol": symbol,
                    "side": sl_side,
                    "type": "LIMIT",
                    "timeInForce": "GTC",
                    "quantity": qty_str,
                    "price": price_str,
                    "timestamp": _now_ms(),
                }

            try:
                send_signed_request("POST", "/sapi/v1/margin/order", params)
                print(f"📈 {label} ({params['type']}) order placed for {symbol} at {price_str} ({sl_side})")
            except Exception as e:
                print(f"⚠️ send_signed_request failed for path=/sapi/v1/margin/order payload={params}: {e}")
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

    # pass webhook data so SL/TP functions can use provided sl/tp
    if side == "BUY":
        resp = execute_long_margin(symbol, webhook_data=data)
    elif side == "SELL":
        resp = execute_short_margin(symbol, webhook_data=data)
    else:
        return jsonify({"error": "Invalid side"}), 400

    return jsonify({"status": "ok", "result": resp}), 200


# ====== FLASK EXECUTION ======
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))

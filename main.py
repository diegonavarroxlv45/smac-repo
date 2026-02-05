import os
import time
import math
import hmac
import hashlib
import requests
import functools
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from flask import Flask, request, jsonify

# ====== SETTINGS ======
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
ADMIN_KEY = os.getenv("ADMIN_KEY")
BASE_URL = "https://api.binance.com"

if not BINANCE_API_KEY and not BINANCE_API_SECRET:
    print("❌ BINANCE_API_KEY and BINANCE_API_SECRET are NOT defined")
    raise RuntimeError("Missing Binance API credentials")

if not BINANCE_API_KEY:
    print("❌ BINANCE_API_KEY is NOT defined")
    raise RuntimeError("Missing BINANCE_API_KEY")

if not BINANCE_API_SECRET:
    print("❌ BINANCE_API_SECRET is NOT defined")
    raise RuntimeError("Missing BINANCE_API_SECRET")

print("🔐 Binance API credentials loaded successfully")

RISK_PCT = float(os.getenv("RISK_PCT", "0.05"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.98"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "1.04"))
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
    if "timestamp" not in payload:
        payload["timestamp"] = _now_ms()
    query_string = "&".join([f"{k}={v}" for k, v in payload.items()])
    signature = hmac.new(BINANCE_API_SECRET.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE_URL}{path}?{query_string}&signature={signature}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    return _request_with_retries(http_method, url, headers=headers)


def floor_to_step_str(value, step_str):
    """
    Rounds down in base of step_str, returns string with step decimals.
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
    Returns useful information: stepSize_str, tickSize_str, minQty, minNotional.
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
    Ajusts price to tick_size. rounding can be ROUND_DOWN o ROUND_UP.
    Returns string with correct decimals.
    """
    d_tick = Decimal(str(tick_size_str))
    p = Decimal(str(price)).quantize(d_tick, rounding=rounding)
    decimals = -d_tick.as_tuple().exponent if d_tick.as_tuple().exponent < 0 else 0
    return f"{p:.{decimals}f}"


# ====== PRE-TRADE CLEANUP ======
def handle_pre_trade_cleanup(symbol: str):
    """
    Cancels previous orders, repays debt safely (buying missing base if needed),
    then sells any residual base asset.
    """
    base_asset = symbol.replace("USDC", "")
    print(f"🔄 Cleaning previous environment for {symbol}...")

    # === 1️⃣ Cancel pending orders ===
    try:
        params = {"symbol": symbol, "timestamp": _now_ms()}
        send_signed_request("DELETE", "/sapi/v1/margin/openOrders", params)
        print(f"🧹 Pending orders for {symbol} canceled")
    except Exception as e:
        print(f"⚠️ Couldn't cancel orders for {symbol}: {e}")

    # === 2️⃣ SAFE REPAY LOGIC ===
    try:
        ts = _now_ms()
        q, sig = sign_params_query({"timestamp": ts}, BINANCE_API_SECRET)
        url = f"{BASE_URL}/sapi/v1/margin/account?{q}&signature={sig}"
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        acc_data = _request_with_retries("GET", url, headers=headers)

        asset_data = next((a for a in acc_data["userAssets"] if a["asset"] == base_asset), None)
        usdc_data  = next((a for a in acc_data["userAssets"] if a["asset"] == "USDC"), None)

        if not asset_data:
            print(f"ℹ️ {base_asset} not present in margin account")
            return

        borrowed   = float(asset_data["borrowed"])
        free_base = float(asset_data["free"])
        free_usdc = float(usdc_data["free"]) if usdc_data else 0.0

        if borrowed <= 0:
            print(f"✅ No active debt in {base_asset}")
        else:
            print(f"💳 Active debt detected: {borrowed} {base_asset}")

            missing = borrowed - free_base

            if missing > 0:
                lot = get_symbol_lot(symbol)

                BUFFER = 1.02
                buy_qty = missing * BUFFER

                qty_str = floor_to_step_str(buy_qty, lot["stepSize_str"])
                qty_f = float(qty_str)

                if qty_f <= 0:
                    raise Exception("Calculated buy qty is zero after stepSize rounding")

                r = _request_with_retries("GET", f"{BASE_URL}/api/v3/ticker/price", params={"symbol": symbol})
                price_est = float(r["price"])

                if qty_f * price_est < lot["minNotional"]:
                    raise Exception("Buy notional below minNotional, aborting repay cleanup")

                needed_usdc = qty_f * price_est
                if needed_usdc > free_usdc:
                    raise Exception(f"Not enough USDC to buy repay asset (need {needed_usdc:.4f}, have {free_usdc:.4f})")

                buy_params = {"symbol": symbol, "side": "BUY", "type": "MARKET", "quantity": qty_str, "timestamp": _now_ms()}
                send_signed_request("POST", "/sapi/v1/margin/order", buy_params)
                print(f"🛒 Bought {qty_str} {base_asset} to reduce debt")

                time.sleep(3)

            # === Refresh balances after buy ===
            ts = _now_ms()
            q, sig = sign_params_query({"timestamp": ts}, BINANCE_API_SECRET)
            url = f"{BASE_URL}/sapi/v1/margin/account?{q}&signature={sig}"
            acc_data = _request_with_retries("GET", url, headers=headers)

            asset_data = next((a for a in acc_data["userAssets"] if a["asset"] == base_asset), None)

            borrowed   = float(asset_data["borrowed"])
            free_base = float(asset_data["free"])

            # === Repay only what is available ===
            repay_amount = min(borrowed, free_base)

            if repay_amount > 0:
                repay_params = {"asset": base_asset, "amount": str(repay_amount), "timestamp": _now_ms()}
                send_signed_request("POST", "/sapi/v1/margin/repay", repay_params)
                print(f"💰 Repay executed: {repay_amount} {base_asset}")

            remaining = borrowed - repay_amount
            if remaining > 0:
                print(f"⚠️ Remaining debt after repay: {remaining:.8f} {base_asset}")
            else:
                print(f"✅ Debt fully cleared for {base_asset}")

    except Exception as e:
        print(f"⚠️ Error during repay in {base_asset}: {e}")

    # === 3️⃣ SELL residual base asset ===
    try:
        lot = get_symbol_lot(symbol)

        ts = _now_ms()
        q, sig = sign_params_query({"timestamp": ts}, BINANCE_API_SECRET)
        url = f"{BASE_URL}/sapi/v1/margin/account?{q}&signature={sig}"
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        acc_data = _request_with_retries("GET", url, headers=headers)

        asset_data = next((a for a in acc_data["userAssets"] if a["asset"] == base_asset), None)
        if not asset_data:
            return

        free = float(asset_data["free"])

        if free <= 0:
            print(f"ℹ️ No residual {base_asset} to sell")
            return

        qty_str = floor_to_step_str(free, lot["stepSize_str"])
        if float(qty_str) <= 0:
            print(f"ℹ️ Residual {base_asset} too small to sell")
            return

        sell_params = {"symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": qty_str, "timestamp": _now_ms()}
        send_signed_request("POST", "/sapi/v1/margin/order", sell_params)
        print(f"🧹 Sold residual {qty_str} {base_asset} to USDC")

    except Exception as e:
        print(f"⚠️ Error selling residual {base_asset}: {e}")


# ====== MAIN FUNCTIONS ======
def execute_long_margin(symbol, webhook_data=None):
    lot = get_symbol_lot(symbol)
    balance_usdc = get_balance_margin("USDC")
    qty_quote = balance_usdc * RISK_PCT

    params = {"symbol": symbol, "side": "BUY", "type": "MARKET", "quoteOrderQty": floor_to_step_str(qty_quote, lot["tickSize_str"]), "timestamp": _now_ms()}

    if DRY_RUN:
        print(f"[DRY_RUN] Margin LONG {symbol}: quoteOrderQty={qty_quote}")
        return {"dry_run": True}

    resp = send_signed_request("POST", "/sapi/v1/margin/order", params)

    executed_qty = 0.0
    entry_price = None
    if isinstance(resp, dict) and "fills" in resp:
        executed_qty = sum(float(f["qty"]) for f in resp["fills"])
        spent_quote = sum(float(f["price"]) * float(f["qty"]) for f in resp["fills"])
        entry_price = (spent_quote / executed_qty) if executed_qty else None
    if not entry_price and isinstance(resp, dict):
        try:
            executed_qty = float(resp.get("executedQty", 0) or 0)
            cumm = float(resp.get("cummulativeQuoteQty", 0) or 0)
            if executed_qty:
                entry_price = cumm / executed_qty
        except Exception:
            pass

    print(f"📈 LONG opened {symbol}: qty={executed_qty} (spent≈{(entry_price * executed_qty) if entry_price else 'unknown'})")

    if executed_qty > 0 and entry_price:
        sl_from_web = None
        tp_from_web = None
        if webhook_data:
            sl_from_web = webhook_data.get("sl")
            tp_from_web = webhook_data.get("tp")
        place_sl_tp_margin(symbol, "BUY", entry_price, executed_qty, lot, sl_override=sl_from_web, tp_override=tp_from_web)

    return {"order": resp}


def execute_short_margin(symbol, webhook_data=None):
    lot = get_symbol_lot(symbol)
    balance_usdc = get_balance_margin("USDC")

    try:
        r = _request_with_retries("GET", f"{BASE_URL}/api/v3/ticker/price", params={"symbol": symbol})
        price_est = float(r.get("price", 0))
    except Exception as e:
        print(f"⚠️ Could not fetch price for {symbol}: {e}")
        return {"error": "price_fetch_failed"}

    if price_est <= 0:
        print("⚠️ Price estimate invalid, aborting short.")
        return {"error": "invalid_price_est"}

    raw_qty = Decimal(str(balance_usdc * RISK_PCT)) / Decimal(str(price_est))
    borrow_amount = float(raw_qty.quantize(Decimal(str(lot["stepSize_str"])), rounding=ROUND_DOWN))

    if borrow_amount <= 0 or borrow_amount < lot.get("minQty", 0.0):
        msg = f"Qty {borrow_amount} < minQty {lot.get('minQty')}"
        print("⚠️", msg)
        return {"error": "qty_too_small", "detail": msg}

    if (borrow_amount * price_est) < lot.get("minNotional", 0.0):
        msg = f"Notional {borrow_amount * price_est:.8f} < minNotional {lot.get('minNotional')}"
        print("⚠️", msg)
        return {"error": "notional_too_small", "detail": msg}

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
    entry_price = None
    if isinstance(resp, dict) and "fills" in resp:
        executed_qty = sum(float(f["qty"]) for f in resp["fills"])
        spent_quote = sum(float(f["price"]) * float(f["qty"]) for f in resp["fills"])
        entry_price = (spent_quote / executed_qty) if executed_qty else None
    if not entry_price and isinstance(resp, dict):
        try:
            executed_qty = float(resp.get("executedQty", 0) or 0)
            cumm = float(resp.get("cummulativeQuoteQty", 0) or 0)
            if executed_qty:
                entry_price = cumm / executed_qty
        except Exception:
            pass

    print(f"📉 SHORT opened {symbol}: qty={executed_qty} (spent≈{(entry_price * executed_qty) if entry_price else 'unknown'})")

    if executed_qty > 0 and entry_price:
        # use sl/tp from webhook if provided
        sl_from_web = None
        tp_from_web = None
        if webhook_data:
            sl_from_web = webhook_data.get("sl")
            tp_from_web = webhook_data.get("tp")
        place_sl_tp_margin(symbol, "SELL", entry_price, executed_qty, lot, sl_override=sl_from_web, tp_override=tp_from_web)

    return {"order": resp}


# ====== SL/TP FUNCTIONS (OCO VERSION) ======
def place_sl_tp_margin(symbol: str, side: str, entry_price: float, executed_qty: float, lot: dict, sl_override=None, tp_override=None):
    """
    Places an OCO (One Cancels the Other) on Binance Margin.
    """
    try:
        oco_side = "SELL" if side == "BUY" else "BUY"

        # === Price calculation ===
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

        # === tickSize adjusting ===
        sl_price_str = format_price_to_tick(sl_price, lot["tickSize_str"], rounding=sl_rounding)
        tp_price_str = format_price_to_tick(tp_price, lot["tickSize_str"], rounding=tp_rounding)
        qty_str = floor_to_step_str(executed_qty * float(COMMISSION_BUFFER), lot["stepSize_str"])
        qty_f = float(qty_str)

        # === Basic validations ===
        for label, price_str in [("SL", sl_price_str), ("TP", tp_price_str)]:
            try:
                price_f = float(price_str)
            except Exception:
                print(f"⚠️ {label} price malformed for {symbol}: {price_str}, skipping")
                return False

            if price_f <= 0 or price_f < lot["tickSize"]:
                print(f"⚠️ Skipping {label} for {symbol}: price {price_f} < tickSize {lot['tickSize']}")
                return False

            notional = price_f * qty_f
            if notional < lot.get("minNotional", 0.0):
                print(f"⚠️ Skipping {label} for {symbol}: notional {notional:.8f} < minNotional {lot.get('minNotional')}")
                return False

        # === stopLimitPrice ===
        stop_limit_raw = float(sl_price_str) * (0.999 if side == "BUY" else 1.001)
        tick = float(lot["tickSize_str"])
        stop_limit_aligned = math.floor(stop_limit_raw / tick) * tick
        stop_limit_price = f"{stop_limit_aligned:.{lot['tickSize_str'].split('.')[-1].find('1')}f}"

        # === Create OCO ===
        params = {"symbol": symbol, "side": oco_side, "quantity": qty_str, "price": tp_price_str, "stopPrice": sl_price_str, "stopLimitPrice": stop_limit_price, "stopLimitTimeInForce": "GTC", "timestamp": _now_ms()}

        try:
            send_signed_request("POST", "/sapi/v1/margin/order/oco", params)
            print(f"📌 OCO order placed for {symbol}: TP={tp_price_str}, SL={sl_price_str}, stopLimit={stop_limit_price} ({oco_side}), qty={qty_str}")
            return True
        except Exception as e:
            print(f"⚠️ send_signed_request failed for /sapi/v1/margin/order/oco payload={params}: {e}")
            print(f"⚠️ Could not place OCO for {symbol}: {e}")
            return False

    except Exception as e:
        print(f"⚠️ Could not place OCO SL/TP for {symbol}: {e}")
        return False


# ====== ADMIN FUNCTIONS ======
def cancel_all_margin_orders():
    print("🧹 Cancelling ALL margin open orders...")

    open_orders = send_signed_request(
        "GET",
        "/sapi/v1/margin/openOrders",
        {}
    )

    if not open_orders:
        print("✅ No margin open orders found")
        return

    symbols = {order["symbol"] for order in open_orders}

    for symbol in symbols:
        try:
            print(f"↪ Cancelling orders for {symbol}")
            send_signed_request(
                "DELETE",
                "/sapi/v1/margin/openOrders",
                {"symbol": symbol}
            )
        except Exception as e:
            print(f"⚠️ Failed cancelling {symbol}: {e}")

def repay_all_margin_debts():
    print("💳 Repaying ALL margin debts...")

    acc = get_margin_account()

    for asset in acc["userAssets"]:
        if asset["asset"] == "USDC":
            continue

        borrowed = float(asset["borrowed"])
        if borrowed <= 0:
            continue

        symbol = asset["asset"] + "USDC"
        print(f"↪ Repaying {borrowed} {asset['asset']}")

        handle_pre_trade_cleanup(symbol)

    print("✅ All debts processed")

def convert_all_assets_to_usdc():
    print("🔁 Converting ALL assets to USDC...")
    account = get_margin_account()

    for asset in account["userAssets"]:
        asset_name = asset["asset"]
        free_qty = Decimal(asset["free"])

        if asset_name == "USDC" or free_qty <= 0:
            continue

        symbol = f"{asset_name}USDC"

        try:
            sell_residual_asset(symbol, free_qty)
        except Exception as e:
            print(f"⚠️ Could not convert {symbol}: {e}")

def get_margin_account():
    print("📊 Fetching margin account info...")
    params = {}
    acc = send_signed_request("GET", "/sapi/v1/margin/account", params)
    return acc

def sell_residual_asset(symbol: str, free_qty: Decimal):
    asset = symbol.replace("USDC", "")
    print(f"💱 Selling residual {free_qty} {asset} → USDC")

    min_qty, step_size = get_symbol_filters(symbol)
    if free_qty < min_qty:
        print(f"⏭️ Skipping {symbol}: {free_qty} < minQty ({min_qty})")
        return

    qty = (free_qty // step_size) * step_size
    qty_str = format(qty, "f")

    payload = {"symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": qty_str}

    try:
        send_signed_request("POST", "/sapi/v1/margin/order", payload)
        print(f"✅ Sold {qty_str} {asset} to USDC")
    except Exception as e:
        print(f"⚠️ Could not sell {symbol}: {e}")


def get_symbol_price(symbol: str) -> float:
    ticker = send_signed_request("GET", "/api/v3/ticker/price", {"symbol": symbol})
    return float(ticker["price"])

_symbol_filters_cache = {}

def get_symbol_filters(symbol):
    if symbol in _symbol_filters_cache:
        return _symbol_filters_cache[symbol]

    info = send_signed_request("GET", "/api/v3/exchangeInfo", {})
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    min_qty = Decimal(f["minQty"])
                    step_size = Decimal(f["stepSize"])
                    _symbol_filters_cache[symbol] = (min_qty, step_size)
                    return min_qty, step_size

    raise Exception(f"Filters not found for {symbol}")


# ====== FLASK WEBHOOK ======
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    if not data:
        return jsonify({"error": "Empty payload"}), 400

    print(f"📩 Webhook received: {data}")

    # 🔐 ADMIN / CONTROL MODE
    if "action" in data:
        action = data["action"].upper()

        if ADMIN_KEY:
            if data.get("admin_key") != os.getenv("ADMIN_KEY"):
                print("🚫 Unauthorized admin action attempt")
                return jsonify({"error": "unauthorized"}), 403

        print(f"🛠️ ADMIN ACTION RECEIVED: {action}")

        if action == "CANCEL_ALL_ORDERS":
            cancel_all_margin_orders()
            return jsonify({"status": "ok", "action": action}), 200

        elif action == "REPAY_ALL_DEBTS":
            repay_all_margin_debts()
            return jsonify({"status": "ok", "action": action}), 200

        elif action == "CONVERT_ALL_TO_USDC":
            convert_all_assets_to_usdc()
            return jsonify({"status": "ok", "action": action}), 200

        else:
            return jsonify({"error": "Unknown action"}), 400

    # 📈 TRADING MODE (TradingView)
    if "symbol" not in data or "side" not in data:
        return jsonify({"error": "Missing trading fields"}), 400

    symbol = data["symbol"]
    side = data["side"].upper()

    handle_pre_trade_cleanup(symbol)

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

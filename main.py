#   ███████╗   ██████╗   ███╗   ██╗  ████████╗
#   ██╔════╝  ██╔════╝   ████╗  ██║  ╚══██╔══╝
#   ███████╗  ██║  ███╗  ██╔██╗ ██║     ██║
#   ╚════██║  ██║   ██║  ██║╚██╗██║     ██║
#   ███████║  ╚██████╔╝  ██║ ╚████║     ██║
#   ╚══════╝   ╚═════╝   ╚═╝  ╚═══╝     ╚═╝

# ====== IMPORTS ======
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
print = functools.partial(print, flush=True)
app = Flask(__name__)


# ====== VARIABLES ======
# --- DEFAULT VARIABLES ---
DEFAULT_RETRIES = 3
DEFAULT_SL_PCT = 2.0
DEFAULT_TP_PCT = 4.0

# --- ENVIRONMENT VARIABLES ---
RETRIES = int(os.getenv("RETRIES", "3"))
SL_PCT = float(os.getenv("SL_PCT", "2"))
TP_PCT = float(os.getenv("TP_PCT", "4"))
MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", "20"))
MAX_RISK_PCT = max(0.1, min(MAX_RISK_PCT, 20))
DEFAULT_RISK_PCT = float(os.getenv("DEFAULT_RISK_PCT", "5"))
DEFAULT_RISK_PCT = max(0.1, min(DEFAULT_RISK_PCT, MAX_RISK_PCT))
COMMISSION = Decimal(os.getenv("COMMISSION", "0.1"))
MIN_BOOT_SECS = int(os.getenv("MIN_BOOT_SECS", "60"))
DEPLOY_GRACE_PERIOD = int(os.getenv("DEPLOY_GRACE_PERIOD", "120"))
PORT = int(os.getenv("PORT", "5000"))

# --- BOOL VARIABLES ---
TESTNET = os.getenv("TESTNET", "false").lower() == "true"
TRADING_ENABLED = os.getenv("TRADING_ENABLED", "true").lower() == "true"
SL_OVERRIDE = os.getenv("SL_OVERRIDE", "true").lower() == "true"
TP_OVERRIDE = os.getenv("TP_OVERRIDE", "true").lower() == "true"

# --- SECRET VARIABLES ---
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
TESTNET_API_KEY = os.getenv("TESTNET_API_KEY")
TESTNET_API_SECRET = os.getenv("TESTNET_API_SECRET")
TRADING_KEY = os.getenv("TRADING_KEY")
ADMIN_KEY = os.getenv("ADMIN_KEY")


# ====== APIS ======
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

else:
    print("🔐 Binance API credentials loaded successfully")

if not TESTNET_API_KEY and not TESTNET_API_SECRET:
    print("❌ TESTNET_API_KEY and TESTNET_API_SECRET are NOT defined")
    raise RuntimeError("Missing Testnet API credentials")

if not TESTNET_API_KEY:
    print("❌ BINANCE_API_KEY is NOT defined")
    raise RuntimeError("Missing TESTNET_API_KEY")

if not TESTNET_API_SECRET:
    print("❌ TESTNET_API_SECRET is NOT defined")
    raise RuntimeError("Missing TESTNET_API_SECRET")

else:
    print("🔐 Testnet API credentials loaded successfully")

if TESTNET:
    BINANCE_API_KEY = os.getenv("TESTNET_API_KEY")
    BINANCE_API_SECRET = os.getenv("TESTNET_API_SECRET")
    BASE_URL = "https://testnet.binance.vision"
else:
    BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
    BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
    BASE_URL = "https://api.binance.com"


# ====== SAFE DEPLOYMENT PATTERN ======
BOOT_TIME = time.time()
BOT_READY = False

# --- BASIC CONNECTIVITY CHECK ---
def _check_binance_connectivity():
    try:
        send_public_request("GET", "/api/v3/time")
        return True
    except Exception as e:
        print(f"❌ Binance connectivity failed: {e}")
        return False

# --- PUBLIC BINANCE REQUEST ---
def send_public_request(http_method: str, path: str, params=None):
    url = f"{BASE_URL}{path}"

    try:
        return _request_with_retries(
            http_method,
            url,
            params=params
        )
    except Exception as e:
        print(f"⚠️ Public request failed {path}: {e}")
        raise

# --- ACCOUNT ACCESS CHECK ---
def _check_account_access():
    try:
        get_balance_margin("USDC")
        return True
    except Exception as e:
        print(f"❌ Account access failed: {e}")
        return False

# --- GLOBAL HEALTH CHECK ---
def health_check():
    print("🩺 Running health check...")

    if not _check_binance_connectivity():
        return False

    if not _check_account_access():
        return False

    print("✅ Health check passed")
    return True

# --- BOT READINESS STATE MACHINE ---
def is_bot_ready():
    global BOT_READY

    if not TRADING_ENABLED:
        print("🛑 Trading manually disabled (TRADING_ENABLED=false)")
        return False

    if BOT_READY:
        return True

    uptime = time.time() - BOOT_TIME

    if uptime < MIN_BOOT_SECS:
        print(f"⏳ Boot protection active ({int(uptime)}s/{MIN_BOOT_SECS}s)")
        return False

    if uptime < DEPLOY_GRACE_PERIOD:
        print(f"🟡 Deploy grace period ({int(uptime)}s/{DEPLOY_GRACE_PERIOD}s)")
        return False

    if not health_check():
        print("⚠️ Bot not healthy yet")
        return False

    BOT_READY = True
    print("🚀 BOT READY — trading ENABLED")

    return True

# --- SAFE EXECUTION GUARD ---
def trading_guard():
    if not is_bot_ready():
        return False, (
            jsonify({
                "status": "booting_or_unhealthy",
                "trading_enabled": TRADING_ENABLED
            }),
            200
        )

    return True, None


# ====== GLOBAL RISK STATE ======
TRADING_BLOCKED = False
MARGIN_MAX_RISK_PCT = MAX_RISK_PCT


# ====== TIME FUNCTION ======
def _now_ms():
    return int(time.time() * 1000)


# ====== SIGNING AND REQUESTING ======
def sign_params_query(params: dict, secret: str):
    query = "&".join([f"{k}={v}" for k, v in params.items()])
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query, signature

def _request_with_retries(method: str, url: str, **kwargs):
    for i in range(RETRIES):
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


# ====== FINAL RISK RESOLUTION ======
def resolve_risk_pct(webhook_data=None):

    risk_pct = DEFAULT_RISK_PCT

    if webhook_data and "risk_pct" in webhook_data:
        try:
            risk_pct = float(webhook_data["risk_pct"])
        except Exception:
            print("⚠️ Invalid risk_pct from webhook")

    risk_pct = min(risk_pct, MARGIN_MAX_RISK_PCT)

    return risk_pct / 100


# ====== BALANCE & MARKET DATA ======
def get_balance_margin(asset="USDC") -> float:
    ts = _now_ms()
    params = {"timestamp": ts}
    q, sig = sign_params_query(params, BINANCE_API_SECRET)
    url = f"{BASE_URL}/sapi/v1/margin/account?{q}&signature={sig}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    data = _request_with_retries("GET", url, headers=headers)
    bal = next((b for b in data.get("userAssets", []) if b["asset"] == asset), None)
    return float(bal["free"]) if bal else 0.0

def get_symbol_lot(symbol):
    data = _request_with_retries("GET", f"{BASE_URL}/api/v3/exchangeInfo")
    for s in data["symbols"]:
        if s["symbol"] == symbol:
            fs = next((f for f in s["filters"] if f["filterType"] == "LOT_SIZE"), None)
            ts = next((f for f in s["filters"] if f["filterType"] == "PRICE_FILTER"), None)
            mnf = next((f for f in s["filters"] if f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL")), None)
            if not fs or not ts:
                raise Exception(f"Missing LOT_SIZE or PRICE_FILTER for {symbol}")
            minNotional = float(mnf.get("minNotional") or mnf.get("notional") or 0.0) if mnf else 0.0
            return {"stepSize_str": fs["stepSize"], "stepSize": float(fs["stepSize"]), "minQty": float(fs.get("minQty", 0.0)), "tickSize_str": ts["tickSize"], "tickSize": float(ts["tickSize"]), "minNotional": minNotional,}
    raise Exception(f"Symbol not found: {symbol}")


# ====== PRICE ADJUST (tickSize) ======
def format_price_to_tick(price: float, tick_size_str: str, rounding=ROUND_DOWN) -> str:
    d_tick = Decimal(str(tick_size_str))
    p = Decimal(str(price)).quantize(d_tick, rounding=rounding)
    decimals = -d_tick.as_tuple().exponent if d_tick.as_tuple().exponent < 0 else 0
    return f"{p:.{decimals}f}"

def floor_to_step_str(value, step_str):
    step = Decimal(str(step_str))
    v = Decimal(str(value))
    n = (v // step) * step
    decimals = -step.as_tuple().exponent if step.as_tuple().exponent < 0 else 0
    q = n.quantize(Decimal(1).scaleb(-decimals))
    return format(q, f".{decimals}f")

def tick_decimals(tick_str: str):
    return len(tick_str.rstrip('0').split('.')[-1])


# ====== CHECK MARGIN LEVEL BEFORE OPERATING ======
def check_margin_level():
    global TRADING_BLOCKED, MARGIN_MAX_RISK_PCT

    try:
        account_info = get_margin_account()
        margin_level = float(account_info["marginLevel"])
        print(f"🧮 Current Margin Level: {margin_level:.2f}")

        # 🚨 CRITICAL — CONTROLLED LIQUIDATION
        if margin_level < 1.16:
            print("🚨 CRITICAL! Margin < 1.16 — EXECUTING CONTROLLED LIQUIDATION")
            TRADING_BLOCKED = True
            clear()
            return False

        # 🔴 EMERGENCY — BLOCK NEW ENTRIES
        elif margin_level < 1.25:
            print("🔴 DANGER! Margin < 1.25 — BLOCKING NEW ENTRIES")
            TRADING_BLOCKED = True
            return True

        # 🟠 DEFENSIVE — LIMIT MAX RISK
        elif margin_level < 2:
            print("🟠 WARNING! Margin < 2 — LIMITING MAX RISK TO 2%")
            MARGIN_MAX_RISK_PCT = 2
            return True

        # 🟢 HEALTHY
        else:
            if TRADING_BLOCKED:
                print("✅ Margin recovered — resuming normal operation")

            TRADING_BLOCKED = False
            MARGIN_MAX_RISK_PCT = MAX_RISK_PCT
            print("✅ Margin level healthy")
            return True

    except Exception as e:
        print(f"⚠️ Could not fetch margin level: {e}")
        return True


# ====== PRE-TRADE CLEANUP ======
def handle_pre_trade_cleanup(symbol: str):
    base_asset = symbol.replace("USDC", "")
    print(f"🔄 Cleaning previous environment for {symbol}...")

    # === 1️⃣ Cancel pending orders ===
    try:
        params = {"symbol": symbol, "timestamp": _now_ms()}
        send_signed_request("DELETE", "/sapi/v1/margin/openOrders", params)
        print(f"🧹 Pending orders for {symbol} canceled")
    except Exception as e:
        print(f"⚠️ Couldn't cancel orders for {symbol}: {e}")

    # === 2️⃣ Repay debt ===
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

    # === 3️⃣ Sell residual balance ===
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
    risk_pct = resolve_risk_pct(webhook_data)
    qty_quote = balance_usdc * risk_pct

    params = {"symbol": symbol, "side": "BUY", "type": "MARKET", "quoteOrderQty": floor_to_step_str(qty_quote, lot["tickSize_str"]), "timestamp": _now_ms()}
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

    risk_pct = resolve_risk_pct(webhook_data)
    raw_qty = Decimal(str(balance_usdc * risk_pct)) / Decimal(str(price_est))
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
        sl_from_web = None
        tp_from_web = None
        if webhook_data:
            sl_from_web = webhook_data.get("sl")
            tp_from_web = webhook_data.get("tp")
        place_sl_tp_margin(symbol, "SELL", entry_price, executed_qty, lot, sl_override=sl_from_web, tp_override=tp_from_web)

    return {"order": resp}


# ====== SL/TP FUNCTIONS ======
def place_sl_tp_margin(symbol: str, side: str, entry_price: float, executed_qty: float, lot: dict, sl_override=None, tp_override=None):
    try:
        COMMISSION_BUFFER = Decimal("1") - (COMMISSION / Decimal("100"))
        oco_side = "SELL" if side == "BUY" else "BUY"

        # === Determine if SL/TP should be used ===
        use_sl = sl_override is not None or (SL_OVERRIDE and SL_PCT is not None)
        use_tp = tp_override is not None or (TP_OVERRIDE and TP_PCT is not None)

        if not use_sl and not use_tp:
            print(f"ℹ️ No SL/TP requested for {symbol}")
            return True

        # === Price calculation ===
        if sl_override is not None:
            sl_price = float(sl_override)
        elif SL_OVERRIDE and SL_PCT is not None:
            sl_price = entry_price * (1 - SL_PCT / 100) if side == "BUY" else entry_price * (1 + SL_PCT / 100)
        else:
            sl_price = None

        if tp_override is not None:
            tp_price = float(tp_override)
        elif TP_OVERRIDE and TP_PCT is not None:
            tp_price = entry_price * (1 + TP_PCT / 100) if side == "BUY" else entry_price * (1 - TP_PCT / 100)
        else:
            tp_price = None

        # === Tick alignment function ===
        def align_price(price: float, tick_str: str, rounding):
            tick = float(tick_str)
            if rounding == ROUND_DOWN:
                return math.floor(price / tick) * tick
            else:
                return math.ceil(price / tick) * tick

        decimals = lot["tickSize_str"].split('.')[-1].find('1')
        if decimals < 0:
            decimals = 8  # fallback

        # === Align SL/TP to tickSize ===
        sl_price_str = None
        tp_price_str = None
        stop_limit_price = None

        if sl_price is not None:
            sl_rounding = ROUND_DOWN if side == "BUY" else ROUND_UP
            sl_price_aligned = align_price(sl_price, lot["tickSize_str"], sl_rounding)
            sl_price_str = f"{sl_price_aligned:.{decimals}f}"

            # stopLimitPrice: slightly inside SL to satisfy Binance
            if side == "BUY":
                stop_limit_aligned = align_price(sl_price_aligned * 0.999, lot["tickSize_str"], ROUND_DOWN)
            else:
                stop_limit_aligned = align_price(sl_price_aligned * 1.001, lot["tickSize_str"], ROUND_UP)
            stop_limit_price = f"{stop_limit_aligned:.{decimals}f}"

        if tp_price is not None:
            tp_rounding = ROUND_UP if side == "BUY" else ROUND_DOWN
            tp_price_aligned = align_price(tp_price, lot["tickSize_str"], tp_rounding)
            tp_price_str = f"{tp_price_aligned:.{decimals}f}"

        # === Quantity alignment ===
        qty_str = floor_to_step_str(executed_qty * float(COMMISSION_BUFFER), lot["stepSize_str"])
        qty_f = float(qty_str)

        # === Decide order type ===
        if sl_price_str and tp_price_str:
            order_type = "OCO"
        elif sl_price_str:
            order_type = "SL_ONLY"
        elif tp_price_str:
            order_type = "TP_ONLY"

        # === Basic validations ===
        for label, price_str in [("SL", sl_price_str), ("TP", tp_price_str)]:
            if price_str is None:
                continue
            price_f = float(price_str)
            if price_f <= 0 or price_f < lot["tickSize"]:
                print(f"⚠️ Skipping {label} for {symbol}: price {price_f} < tickSize {lot['tickSize']}")
                return False
            notional = price_f * qty_f
            if notional < lot.get("minNotional", 0.0):
                print(f"⚠️ Skipping {label} for {symbol}: notional {notional:.8f} < minNotional {lot.get('minNotional')}")
                return False

        # ===== Place OCO =====
        if order_type == "OCO":
            params = {
                "symbol": symbol,
                "side": oco_side,
                "quantity": qty_str,
                "price": tp_price_str,
                "stopPrice": sl_price_str,
                "stopLimitPrice": stop_limit_price,
                "stopLimitTimeInForce": "GTC",
                "timestamp": _now_ms()
            }
            try:
                send_signed_request("POST", "/sapi/v1/margin/order/oco", params)

                direction = 1 if side == "BUY" else -1
                entry_f = float(entry_price)
                tp_f = float(tp_price_str)
                sl_f = float(sl_price_str)
                profit_tp = (tp_f - entry_f) * qty_f * direction
                loss_sl = (sl_f - entry_f) * qty_f * direction
                rr = abs(profit_tp / loss_sl) if loss_sl != 0 else 0

                print(f"📌 OCO placed for {symbol}: TP={tp_price_str}, SL={sl_price_str}, stopLimit={stop_limit_price} ({oco_side}), qty={qty_str}")
                print(f"🟢 TP PnL ≈ {profit_tp:.2f} USDC | 🔴 SL PnL ≈ {loss_sl:.2f} USDC | ⚖️ R:R {rr:.2f}")

                return True
            except Exception as e:
                print(f"⚠️ Failed OCO for {symbol}, payload={params}: {e}")
                return False

        # ===== SL ONLY =====
        if order_type == "SL_ONLY":
            params = {
                "symbol": symbol,
                "side": oco_side,
                "type": "STOP_LOSS_LIMIT",
                "quantity": qty_str,
                "price": stop_limit_price,
                "stopPrice": sl_price_str,
                "timeInForce": "GTC",
                "timestamp": _now_ms()
            }
            send_signed_request("POST", "/sapi/v1/margin/order", params)
            print(f"🛑 SL placed for {symbol}: stop={sl_price_str}, limit={stop_limit_price}, qty={qty_str}")
            return True

        # ===== TP ONLY =====
        if order_type == "TP_ONLY":
            params = {
                "symbol": symbol,
                "side": oco_side,
                "type": "LIMIT",
                "quantity": qty_str,
                "price": tp_price_str,
                "timeInForce": "GTC",
                "timestamp": _now_ms()
            }
            send_signed_request("POST", "/sapi/v1/margin/order", params)
            print(f"🎯 TP placed for {symbol}: price={tp_price_str}, qty={qty_str}")
            return True

    except Exception as e:
        print(f"⚠️ Could not place SL/TP for {symbol}: {e}")
        return False


# ====== MILESTONES ======
MILESTONES_USDC = [500, 1000, 2000, 5000, 10000, 25000, 50000]
REACHED_MILESTONES = set()

def check_milestones(total_balance_usdc: float):
    for milestone in MILESTONES_USDC:
        if total_balance_usdc >= milestone and milestone not in REACHED_MILESTONES:
            REACHED_MILESTONES.add(milestone)

            print(
                f"🎉🎉 CONGRATS! 🎉🎉\n"
                f"💰 You reached {milestone:,.0f} USDC\n"
                f"🚀 Keep it up. Compounding is working.\n"
                f"🔥 Discipline > Luck\n"
            )


# ====== ADMIN FUNCTIONS ======
def clear(symbol=None):
    if symbol:
        print(f"🔁 Converting {symbol} to USDC...")
    else:
        print("🔁 Converting ALL assets to USDC...")

    account = get_margin_account()
    cleared_symbols = []
    failed_symbols = []

    for asset in account["userAssets"]:
        asset_name = asset["asset"]
        free_qty = float(asset["free"])

        if asset_name == "USDC" or free_qty <= 0:
            continue

        asset_symbol = f"{asset_name}USDC"

        if symbol and asset_symbol != symbol:
            continue

        try:
            print(f"↪ Clearing {free_qty} {asset_name}")
            handle_pre_trade_cleanup(asset_symbol)
            cleared_symbols.append(asset_symbol)

        except Exception as e:
            print(f"⚠️ Could not convert {asset_symbol}: {e}")
            failed_symbols.append({"symbol": asset_symbol, "error": str(e)})

    print("✅ CLEAR completed")
    return {"cleared": cleared_symbols, "failed": failed_symbols}

def read():
    print("📊 Reading Cross Margin account snapshot...")

    acc = get_margin_account()

    total_debt = 0.0
    usdc_balance = 0.0
    usdc_borrowed = 0.0

    assets_with_balance = []

    for asset in acc["userAssets"]:

        borrowed = float(asset["borrowed"])
        free = float(asset["free"])
        locked = float(asset["locked"])

        total_debt += borrowed

        total_asset_balance = free + locked

        if total_asset_balance > 0 and asset["asset"] != "USDC":
            assets_with_balance.append({
                "asset": asset["asset"],
                "balance": round(total_asset_balance, 8)
            })

        if asset["asset"] == "USDC":
            usdc_balance = total_asset_balance
            usdc_borrowed = borrowed


    btc_usdc_price = get_btc_usdc_price()
    total_balance_usdc = float(acc["totalNetAssetOfBtc"]) * btc_usdc_price
    margin_level = float(acc["marginLevel"])

    print("────────── 📊 ACCOUNT VARIABLES 📊 ──────────")
    print(f"├─ 🤖 Trading Enabled      : {TRADING_ENABLED}")
    print(f"├─ 🧪 Testnet Mode         : {TESTNET}")
    print(f"├─ 🟥 Stop Loss Override   : {SL_OVERRIDE}")
    print(f"├─ 🟩 Take Profit Override : {TP_OVERRIDE}")
    print(f"├─ 🔴 Stop Loss Value      : {SL_PCT}")
    print(f"├─ 🟢 Take Profit Value    : {TP_PCT}")
    print(f"├─ 🔄 Retries Value        : {RETRIES}")
    print("────────── 📊 ACCOUNT SNAPSHOT 📊 ──────────")
    print(f"├─ 💰 Total Balance (USDC) : {total_balance_usdc:.8f}")
    print(f"├─ 💸 USDC Balance         : {usdc_balance:.8f}")
    print(f"├─ 💳 USDC Borrowed        : {usdc_borrowed:.8f}")
    print(f"├─ 📉 Total Debt           : {total_debt:.8f}")
    print(f"├─ ⚖️ Margin Level         : {margin_level}")
    print(f"├─ 🧾 Assets with balance:")
    for a in assets_with_balance:
        print(f"│   ├─ {a['asset']} : {a['balance']}")

    print("──────────────────────────────────────────────")

    snapshot = {
        "TRADING_ENABLED": TRADING_ENABLED,
        "TESTNET": TESTNET,
        "SL_OVERRIDE": SL_OVERRIDE,
        "TP_OVERRIDE": TP_OVERRIDE,
        "SL": SL_PCT,
        "TP": TP_PCT,
        "RETRIES": RETRIES,
        "totalBalanceUSDC": round(total_balance_usdc, 8),
        "usdcBalance": round(usdc_balance, 8),
        "usdcBorrowed": round(usdc_borrowed, 8),
        "totalDebt": round(total_debt, 8),
        "marginLevel": float(acc["marginLevel"]),
        "assetsWithBalance": assets_with_balance
    }

    check_milestones(total_balance_usdc)

    return snapshot
def borrow(amount: float):
    print(f"📥 ADMIN BORROW requested: {amount} USDC")

    if amount <= 0:
        raise ValueError("Borrow amount must be > 0")

    acc = get_margin_account()
    margin_level = float(acc["marginLevel"])
    print(f"🧮 Current Margin Level: {margin_level:.2f}")

    if margin_level < 2:
        raise Exception("Margin level too low to safely borrow USDC")

    params = {"asset": "USDC", "amount": format(amount, "f"), "timestamp": _now_ms()}

    resp = send_signed_request("POST", "/sapi/v1/margin/loan", params)

    print(f"✅ BORROW completed: {amount} USDC")
    return resp

def repay(amount):
    print(f"💳 ADMIN REPAY requested: {amount}")

    if isinstance(amount, str) and amount.lower() == "all":
        margin_info = get_margin_account()

        borrowed_usdc = Decimal("0")
        for asset in margin_info["userAssets"]:
            if asset["asset"] == "USDC":
                borrowed_usdc = Decimal(asset["borrowed"])
                break

        if borrowed_usdc <= 0:
            print("ℹ️ No USDC debt to repay")
            return {"status": "nothing_to_repay"}

        amount = borrowed_usdc
        print(f"🔁 REPAY ALL → {amount} USDC")

    amount = Decimal(str(amount))
    if amount <= 0:
        raise ValueError("Repay amount must be > 0")

    params = {"asset": "USDC", "amount": format(amount, "f"), "timestamp": _now_ms()}

    resp = send_signed_request("POST", "/sapi/v1/margin/repay", params)

    print(f"✅ REPAY completed: {amount} USDC")
    return resp

def set_trading_state(state):
    global TRADING_ENABLED

    if state == "on":
        TRADING_ENABLED = True
        print("▶️ ADMIN ACTION: Trading RESUMED")

    elif state == "off":
        TRADING_ENABLED = False
        print("⏸️ ADMIN ACTION: Trading PAUSED")

    else:
        return {"status": "error", "msg": "invalid state"}

    return {"status": "ok", "trading_enabled": TRADING_ENABLED}

def set_testnet_state(state):
    global TESTNET

    if state == "on":
        TESTNET = True
        print("🧪 ADMIN ACTION: TESTNET MODE ENABLED")

    elif state == "off":
        TESTNET = False
        print("🌐 ADMIN ACTION: LIVE MODE ENABLED")

    else:
        return {"status": "error", "msg": "invalid state"}

    return {"status": "ok", "testnet": TESTNET}

def set_sl(state=None, value=None):
    global SL_OVERRIDE, SL_PCT

    if state is not None:

        if state == "on":
            SL_OVERRIDE = True
            print("🟢 SL override ENABLED")

        elif state == "off":
            SL_OVERRIDE = False
            print("🔴 SL override DISABLED")

        else:
            return {"status": "error", "msg": "invalid state"}

        return {"status": "ok", "sl_override": SL_OVERRIDE}

    if value is not None:

        try:
            value = float(value)
        except:
            return {"status": "error", "msg": "invalid SL value"}

        SL_PCT = max(0.1, min(value, 50))
        print(f"🛠️ ADMIN ACTION: SL value updated → {SL_PCT}")
        return {"status": "ok", "sl_value": SL_PCT}

    return {"status": "error", "msg": "no state or value provided"}

def set_tp(state=None, value=None):
    global TP_OVERRIDE, TP_PCT

    if state is not None:

        if state == "on":
            TP_OVERRIDE = True
            print("🟢 TP override ENABLED")

        elif state == "off":
            TP_OVERRIDE = False
            print("🔴 TP override DISABLED")

        else:
            return {"status": "error", "msg": "invalid state"}

        return {"status": "ok", "tp_override": TP_OVERRIDE}

    if value is not None:

        try:
            value = float(value)
        except:
            return {"status": "error", "msg": "invalid TP value"}

        TP_PCT = max(0.1, min(value, 50))
        print(f"🛠️ ADMIN ACTION: TP value updated → {TP_PCT}")
        return {"status": "ok", "tp_value": TP_PCT}

    return {"status": "error", "msg": "no state or value provided"}

def set_retries(value=None):
    global RETRIES

    if value is not None:

        try:
            value = int(value)
        except:
            return {"status": "error", "msg": "invalid RETRIES value"}

        RETRIES = max(1, min(value, 5))
        print(f"🛠️ ADMIN ACTION: RETRIES value updated → {RETRIES}")
        return {"status": "ok", "retries_value": RETRIES}

    return {"status": "error", "msg": "no state or value provided"}

def restore():
    global RETRIES, SL_PCT, TP_PCT
    print("🛠️ ADMIN ACTION: RESTORE default trading parameters")
    RETRIES = DEFAULT_RETRIES
    SL_PCT = DEFAULT_SL_PCT
    TP_PCT = DEFAULT_TP_PCT
    print(f"🔄 RETRIES restored → {RETRIES}")
    print(f"🔄 SL_PCT restored → {SL_PCT}")
    print(f"🔄 TP_PCT restored → {TP_PCT}")
    return {"status": "ok", "RETRIES": RETRIES, "SL_PCT": SL_PCT, "TP_PCT": TP_PCT}

def logout(ip):
    destroy_admin_session(ip)
    return {"status": "logged_out"}

def get_btc_usdc_price():
    r = requests.get(f"{BASE_URL}/api/v3/ticker/price", params={"symbol": "BTCUSDC"}, timeout=5)
    return float(r.json()["price"])

def get_margin_account():
    print("📊 Fetching margin account info...")
    params = {}
    acc = send_signed_request("GET", "/sapi/v1/margin/account", params)
    return acc

ADMIN_ACTIONS = {
    "CLEAR": clear,
    "READ": read,
    "BORROW": borrow,
    "REPAY": repay,
    "TRADING": set_trading_state,
    "TESTNET": set_testnet_state,
    "SL": set_sl,
    "TP": set_tp,
    "RETRIES": set_retries,
    "RESTORE": restore,
    "LOGOUT": logout
}


# ====== CENSORING KEYS ======
SENSITIVE_FIELDS = {"admin_key", "trading_key"}

def sanitize_payload(payload: dict) -> dict:
    clean = payload.copy()
    for field in SENSITIVE_FIELDS:
        if field in clean:
            clean[field] = "***REDACTED***"
    return clean


# ====== ADMIN SESSION SYSTEM ======
ADMIN_SESSIONS = {}
ADMIN_SESSION_TIMEOUT = 300

def get_client_ip():
    if request.headers.get("X-Forwarded-For"):
        return request.headers.get("X-Forwarded-For").split(",")[0].strip()
    return request.remote_addr

def admin_session_active(ip):
    now = time.time()

    if ip not in ADMIN_SESSIONS:
        return False

    last_seen = ADMIN_SESSIONS[ip]

    if now - last_seen > ADMIN_SESSION_TIMEOUT:
        print(f"🔒 Admin session expired for {ip}")
        del ADMIN_SESSIONS[ip]
        return False

    ADMIN_SESSIONS[ip] = now
    return True

def create_admin_session(ip):
    ADMIN_SESSIONS[ip] = time.time()
    print(f"🔓 Admin session opened for {ip}")

def destroy_admin_session(ip):
    if ip in ADMIN_SESSIONS:
        del ADMIN_SESSIONS[ip]
        print(f"🔒 Admin session closed for {ip}")


# ====== FLASK WEBHOOK ======
@app.route("/webhook", methods=["POST"])
def webhook():

    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    if not data:
        return jsonify({"error": "Empty payload"}), 400

    print(f"📩 Webhook received: {sanitize_payload(data)}")

    # 🔐 ADMIN LOGIN (without action)
    if data.get("admin_key") == ADMIN_KEY and "action" not in data:
        client_ip = get_client_ip()
        create_admin_session(client_ip)
        return jsonify({"status": "admin_session_started"}), 200

    # 🔐 ADMIN MODE
    if "action" in data:

        action = data["action"].upper()
        state = data.get("state", "").lower()
        client_ip = get_client_ip()

        if data.get("admin_key") == ADMIN_KEY:
            create_admin_session(client_ip)

        if not admin_session_active(client_ip):
            print(f"🚫 Unauthorized admin access from {client_ip}")
            return jsonify({"error": "admin_auth_required"}), 403

        print(f"🛠️ ADMIN ACTION RECEIVED: {action} from {client_ip}")

        handler = ADMIN_ACTIONS.get(action)

        if not handler:
            print("❓ Unknown action")
            return jsonify({"error": "Unknown action"}), 400

        if action == "CLEAR":
            symbol = data.get("symbol")
            result = handler(symbol)
            return jsonify({"status": "ok", "action": action, "result": result}), 200

        elif action == "READ":
            snapshot = handler()
            return jsonify(snapshot), 200

        elif action == "BORROW":
            amount = float(data.get("amount", 0))
            handler(amount)
            return jsonify({"status": "ok", "action": action, "amount": amount}), 200

        elif action == "REPAY":
            amount = data.get("amount", 0)
            if isinstance(amount, str):
                amount = amount.lower()
            handler(amount)
            return jsonify({"status": "ok", "action": action, "amount": amount}), 200

        elif action in ["TRADING", "TESTNET"]:
            result = handler(state)
            return jsonify({"action": action, "state": state, **result}), 200

        elif action in ["SL", "TP"]:
            value = data.get("value")
            result = handler(state=state if state else None, value=value)
            return jsonify({"action": action, "state": state, "value": value, **result}), 200

        elif action == "RETRIES":
            value = data.get("value")
            result = handler(value)
            return jsonify({"action": action, "value": value, **result}), 200

        if action == "RESTORE":
            result = handler()
            return jsonify({"status": "ok", **result}), 200

        elif action == "LOGOUT":
            result = handler(client_ip)
            return jsonify({"action": action, **result}), 200

    # 📈 TRADING MODE (TradingView)
    allowed, response = trading_guard()
    if not allowed:
        return response

    if "symbol" not in data or "side" not in data:
        print("❓ Missing trading fields")
        return jsonify({"error": "Missing trading fields"}), 400

    if TRADING_KEY:
        if data.get("trading_key") != TRADING_KEY:
            print("🚫 Invalid or missing trading_key")
            return jsonify({"status": "blocked", "reason": "invalid trading key"}), 403

    symbol = data["symbol"]
    side = data["side"].upper()

    # 🧮 CHECK MARGIN LEVEL
    if not check_margin_level():
        print("⛔ Trading blocked by margin safety system (critical)")
        return jsonify({"status": "blocked", "reason": "margin critical"}), 200

    if TRADING_BLOCKED:
        print("⛔ Trading blocked by margin safety system")
        return jsonify({"status": "blocked", "reason": "margin protection"}), 200

    handle_pre_trade_cleanup(symbol)

    check_margin_level()

    if TRADING_BLOCKED:
        print("⛔ Trading blocked due to margin protection")
        return jsonify({"status": "blocked_by_margin"}), 200

    if side == "BUY":
        resp = execute_long_margin(symbol, webhook_data=data)
    elif side == "SELL":
        resp = execute_short_margin(symbol, webhook_data=data)
    else:
        return jsonify({"error": "Invalid side"}), 400

    return jsonify({"status": "ok", "result": resp}), 200


@app.route("/health", methods=["GET"])
def health():
    uptime = int(time.time() - BOOT_TIME)
    return jsonify({"bot_ready": BOT_READY, "trading_enabled": TRADING_ENABLED, "uptime_seconds": uptime, "mode": "TESTNET" if TESTNET else "LIVE"})


# ====== FLASK EXECUTION ======
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)


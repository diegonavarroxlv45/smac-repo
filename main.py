#   ███████╗   ██████╗   ███╗   ██╗  ████████╗
#   ██╔════╝  ██╔════╝   ████╗  ██║  ╚══██╔══╝
#   ███████╗  ██║  ███╗  ██╔██╗ ██║     ██║
#   ╚════██║  ██║   ██║  ██║╚██╗██║     ██║
#   ███████║  ╚██████╔╝  ██║ ╚████║     ██║
#   ╚══════╝   ╚═════╝   ╚═╝  ╚═══╝     ╚═╝

# ====== IMPORTS ======
"""Standard library and third-party imports required for the application."""

import os
import io
import time
import math
import hmac
import json
import hashlib
import zipfile
import logging
import requests
import functools
import threading
from collections import deque
from threading import Lock, Thread
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import TimedRotatingFileHandler
from flask import Flask, request, jsonify, redirect, url_for, send_file, render_template_string, Response


# ====== SETTINGS ======
"""Flask app initialization, thread pool executor, and global print flush override."""

print = functools.partial(print, flush=True)
app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=3)


# ====== VARIABLES ======
"""All runtime variables: defaults, booleans, environment-loaded values, static config, trade counters, and secrets."""

# --- DEFAULT VARIABLES ---
DFT_TRADING = True
DFT_SL_OVERRIDE = True
DFT_TP_OVERRIDE = True
DFT_LOG_DEBUG = False
DFT_SL_PCT = 2.0
DFT_TP_PCT = 4.0
DFT_LOGIN_LIMIT  = 5
DFT_LOGIN_RETRY  = 5
DFT_SESSION_TIME = 5

# --- BOOL VARIABLES ---
TRADING = os.getenv("TRADING", "true").lower() == "true"
SL_OVERRIDE = os.getenv("SL_OVERRIDE", "true").lower() == "true"
TP_OVERRIDE = os.getenv("TP_OVERRIDE", "true").lower() == "true"
LOG_DEBUG = os.getenv("LOG_DEBUG", "false").lower() == "true"

# --- STRINGS VARIABLES ---
PLATFORM  = os.getenv("PLATFORM", "Binance")                 # STRING

# --- ENVIRONMENT VARIABLES ---
SL_PCT = float(os.getenv("SL_PCT", "2"))                     # %
TP_PCT = float(os.getenv("TP_PCT", "4"))                     # %
LOGIN_LIMIT  = int(os.getenv("LOGIN_LIMIT", "5"))            # NUMBER
LOGIN_RETRY  = int(os.getenv("LOGIN_RETRY", "5"))            # MINUTES
SESSION_TIME = int(os.getenv("SESSION_TIME", "5"))           # MINUTES

# --- VARIABLE MINS ---
MIN_SL_PCT = 0.1                                             # %
MIN_TP_PCT = 0.1                                             # %
MIN_LOGIN_LIMIT  = 1                                         # NUMBER
MIN_LOGIN_RETRY  = 1                                         # MINUTES
MIN_SESSION_TIME = 1                                         # MINUTES

# --- VARIABLE MAXS ---
MAX_SL_PCT = 50                                              # %
MAX_TP_PCT = 50                                              # %
MAX_LOGIN_LIMIT  = 15                                        # NUMBER
MAX_LOGIN_RETRY  = 15                                        # MINUTES
MAX_SESSION_TIME = 15                                        # MINUTES

# --- SDP PERIODS ---
BOOT_PERIOD = int(os.getenv("BOOT_PERIOD", "1"))             # MINUTES
GRACE_PERIOD = int(os.getenv("GRACE_PERIOD", "2"))           # MINUTES

# --- TRADE COUNTER VARIABLES ---
TRADE_COUNTER = 0                                            # NUMBER
DAILY_LONGS   = 0                                            # NUMBER
DAILY_SHORTS  = 0                                            # NUMBER
TOTAL_LONGS   = 0                                            # NUMBER
TOTAL_SHORTS  = 0                                            # NUMBER
CURRENT_DAY   = datetime.utcnow().date()                     # NUMBER

# --- MARGIN LEVEL THRESHOLDS ---
ML_WARNING = float(os.getenv("ML_WARNING", "2"))             # NUMBER
ML_DANGER = float(os.getenv("ML_DANGER", "1.25"))            # NUMBER
ML_CRITICAL = float(os.getenv("ML_CRITICAL", "1.16"))        # NUMBER
ML_LIQUID = float(os.getenv("ML_LIQUID", "1.1"))             # NUMBER

# --- RISK_PCT VARIABLES ---
MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", "20"))        # %
MAX_RISK_PCT = max(0.1, min(MAX_RISK_PCT, 20))               # %
DFT_RISK_PCT = float(os.getenv("DFT_RISK_PCT", "5"))         # %
DFT_RISK_PCT = max(0.1, min(DFT_RISK_PCT, MAX_RISK_PCT))     # %

# --- SL/TP COMISSION ---
COMMISSION = Decimal(os.getenv("COMMISSION", "0.1"))         # %

# --- HISTORY VARIABLES ---
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "100000"))        # NUMBER

# --- SNAPSHOT VARIABLES ---
MAX_SNAPSHOTS = int(os.getenv("MAX_SNAPSHOTS", "500"))       # NUMBER

# --- SECRET VARIABLES ---
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")               # API
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")         # API
TESTNET_API_KEY = os.getenv("TESTNET_API_KEY")               # API
TESTNET_API_SECRET = os.getenv("TESTNET_API_SECRET")         # API
TRADING_KEY = os.getenv("TRADING_KEY")                       # KEY
ADMIN_KEY = os.getenv("ADMIN_KEY")                           # KEY

# --- PORT VARIABLES ---
PORT = int(os.getenv("PORT", "5000"))                        # NUMBER


# ====== LOGGING ======
"""Logger setup with rotating file handler, console handler, custom ADMIN and DATE log levels."""

# --- LOGGER SETTINGS ---
logger = logging.getLogger("sgnt")
logger.setLevel(logging.INFO)

handler = TimedRotatingFileHandler(
    "sgnt.log",
    when="D",
    interval=90,
    backupCount=4,
    encoding="utf-8"
)

formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(filename)s:%(lineno)d | %(message)s")
handler.suffix = "%Y-%m-%d.log"
handler.setFormatter(formatter)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(handler)
logger.addHandler(console_handler)

# --- ADMIN LABEL ---
ADMIN_LEVEL = 25
logging.addLevelName(ADMIN_LEVEL, "ADMIN")

def admin(self, message, *args, **kwargs):
    if self.isEnabledFor(ADMIN_LEVEL):
        self._log(ADMIN_LEVEL, message, args, **kwargs)

logging.Logger.admin = admin

# --- DATE LABEL ---
DATE_LEVEL = 26
logging.addLevelName(DATE_LEVEL, "DATE")

def date(self, message, *args, **kwargs):
    if self.isEnabledFor(DATE_LEVEL):
        self._log(DATE_LEVEL, message, args, **kwargs)

logging.Logger.date = date


# ====== APIS ======
"""API configuration: selects credentials and base URL, validates that secrets are present."""

# --- ALLOWED PLATFORMS ---
VALID_PLATFORMS = {"Binance", "Binance Testnet"}

if PLATFORM not in VALID_PLATFORMS:
    logger.error(f"❌ Invalid PLATFORM '{PLATFORM}'. Valid options: {VALID_PLATFORMS}")
    raise RuntimeError(f"Invalid PLATFORM: '{PLATFORM}'")

# --- BINANCE / TESTNET CONFIGURATION ---
logger.info(f"🌐 Running in {PLATFORM}")
if PLATFORM == "Binance":
    API_KEY = os.getenv("BINANCE_API_KEY")
    API_SECRET = os.getenv("BINANCE_API_SECRET")
    BASE_URL = "https://api.binance.com"
    ALGORITHM = "sha256"
    QUOTE = "USDC"
    SEPARATOR = ""

elif PLATFORM == "Binance Testnet":
    API_KEY = os.getenv("TESTNET_API_KEY")
    API_SECRET = os.getenv("TESTNET_API_SECRET")
    BASE_URL = "https://testnet.binance.vision"
    ALGORITHM = "sha256"
    QUOTE = "USDC"
    SEPARATOR = ""

if not API_KEY and not API_SECRET:
    logger.error(f"❌ Missing both {PLATFORM} API credentials")
    raise RuntimeError(f"Missing both {PLATFORM} API credentials")

elif not API_KEY:
    logger.error(f"❌ Missing {PLATFORM} API KEY credentials")
    raise RuntimeError(f"Missing {PLATFORM} API KEY credentials")

elif not API_SECRET:
    logger.error(f"❌ Missing {PLATFORM} API SECRET credentials")
    raise RuntimeError(f"Missing {PLATFORM} API SECRET credentials")

else:
    logger.info(f"🔐 {PLATFORM} API credentials loaded successfully")


# ====== TIME FUNCTION ======
"""Returns the current UTC timestamp in milliseconds for API request signing."""

def _now_ms():
    ts = int(time.time() * 1000)

    if LOG_DEBUG:
        logger_admin(f"📋 NOW_MS={ts}")

    return ts


# ====== SAFE DEPLOYMENT PATTERN ======
"""Boot protection and grace period logic, health checks, and bot readiness state machine to prevent trading during unstable deploys."""

# --- SDP SETTINGS ---
BOOT_TIME = time.time()
BOT_READY = False
LAST_HEALTH_CHECK = 0
HEALTH_CHECK_INTERVAL = 10
LAST_HEALTH_STATUS = False

# --- PUBLIC REQUEST ---
def send_public_request(http_method: str, path: str, params=None):
    url = f"{BASE_URL}{path}"

    try:
        return request_with_retries(http_method, url, params=params)
    except Exception as e:
        logger.error(f"⚠️ Public request failed {path}: {e}")
        raise

# --- GLOBAL HEALTH CHECK ---
def health_check():
    if not BOT_READY:
        logger.info("🩺 Running health check...")

    # 📡 CHECK CONNECTIVITY
    try:
        send_public_request("GET", "/api/v3/time")
    except Exception as e:
        logger.error(f"❌ Binance connectivity failed: {e}")
        return False

    # 📡 CHECK ACCOUNT ACCESS
    try:
        get_balance_margin(QUOTE)
    except Exception as e:
        logger.error(f"❌ Account access failed: {e}")
        return False
    return True

# --- CACHED HEALTH CHECK ---
def health_check_cached():
    global LAST_HEALTH_CHECK, LAST_HEALTH_STATUS

    now = time.time()

    if now - LAST_HEALTH_CHECK < HEALTH_CHECK_INTERVAL:
        return LAST_HEALTH_STATUS

    try:
        status = health_check()
    except Exception:
        status = False

    LAST_HEALTH_CHECK = now
    LAST_HEALTH_STATUS = status
    return status

# --- BOT READINESS STATE MACHINE ---
def is_bot_ready():
    global BOT_READY

    # 🛑 TRADING DISABLED LOGGER
    if not TRADING:
        logger.info("🛑 Trading manually disabled (TRADING=false)\n")
        return False

    # ⚠️ HEALTH LOST LOGGER
    if BOT_READY:
        if not health_check_cached():
            logger.error("⚠️ Bot lost health — disabling trading")
            BOT_READY = False
            return False
        return True

    uptime = time.time() - BOOT_TIME

    # ⌛ BOOT LOGGER
    if uptime < (BOOT_PERIOD * 60):
        logger.info(f"⌛ Boot protection active ({int(uptime)}s/{BOOT_PERIOD * 60}s)\n")
        return False

    # ⏳ BOOT LOGGER
    if uptime < (GRACE_PERIOD * 60):
        logger.info(f"⏳ Deploy grace period ({int(uptime)}s/{GRACE_PERIOD * 60}s)\n")
        return False

    # ⚠ STILL NOT HEALTHY LOGGER
    if not health_check_cached():
        logger.error("⚠️ Bot not healthy yet")
        return False

    BOT_READY = True
    logger.info("🚀 BOT READY — trading ENABLED\n")
    return True

# --- SAFE EXECUTION GUARD ---
def trading_guard():
    if not is_bot_ready():
        return False, (
            jsonify({
                "status": "booting_or_unhealthy",
                "trading": TRADING
            }),
            200
        )

    return True, None


# ====== SIGNING AND REQUESTING ======
"""Request signing, retry logic with exponential backoff, and signed/unsigned request dispatchers."""

# --- ALGORITHM VALIDATION ---
def check_algorithm(ALGORITHM):
    if LOG_DEBUG:
        logger_admin(f"📋 Using algorithm: {ALGORITHM}")
    if ALGORITHM not in hashlib.algorithms_guaranteed:
        logger.error(f"⚠ {ALGORITHM} not valid or unavailable or hashlib module")
        raise ValueError(f"error: ALGORITHM '{ALGORITHM}' not valid.")
    else:
        return getattr(hashlib, ALGORITHM)

# --- SIGNING ---
def sign_params_query(params: dict, secret: str):
    algo = check_algorithm(ALGORITHM)
    query = "&".join([f"{k}={v}" for k, v in params.items()])
    signature = hmac.new(secret.encode(), query.encode(), algo).hexdigest()
    return query, signature

# --- STATUS CODES ---
RETRYABLE_STATUS = {408, 409, 418, 423, 425, 429, 500, 502, 503, 504}
FATAL_STATUS = {400, 401, 403, 404, 405, 412, 422}

BACKOFFS = {
    418: 60,
    429: 5,
    503: 5,
    502: 3,
    504: 3,
}

# --- SESSION REQUEST ---
SESSION = requests.Session()

# --- REQUESTING ---
def request_with_retries(method: str, url: str, **kwargs):
    for i in range(3):
        try:
            resp = SESSION.request(method, url, timeout=10, **kwargs)
            status = resp.status_code

            try:
                data = resp.json()
            except Exception:
                data = resp.text

            if (isinstance(data, dict) and data.get("code") == -1021):
                return data

            if LOG_DEBUG:
                logger_admin(f"📋 REQUEST attempt={i+1}, method={method}, url={url[:150]}")

            # ⚠ ERROR CODES
            if status == 400 and isinstance(data, dict) and "code" in data and data["code"] < 0:
                return data

            # ⚠ ERROR LOGS
            body_preview = resp.text[:300]
            reason = resp.reason or ""
            log_msg = (
                f"⚠ Error {status} {reason} | {method} {url} | "
                f"attempt={i+1} | params={kwargs.get('params')} | body={body_preview}")

            if status >= 400:
                logger.error(log_msg)

            # ⚠ FATAL ERRORS
            if status in FATAL_STATUS:
                logger.error(f"🚫 Fatal | {log_msg}")
                raise Exception(f"Non-retryable error {status}")

            # ⚠ RETRAYABLE ERRORS
            if status in RETRYABLE_STATUS:
                retry_after = resp.headers.get("Retry-After")
                sleep_time = int(retry_after) if retry_after else BACKOFFS.get(status, min(2 ** i, 10))
                logger.error(f"🔁 Retryable → sleeping {sleep_time}s | {log_msg}")
                time.sleep(sleep_time)
                continue

            # ✅ 200 OK
            if status == 200:
                if i > 0:
                    logger.info(f"✅ Request recovered on retry {i+1}")
                return data

            logger.error(f"⚠️ Attempt {i+1} failed: {data}")

        except requests.exceptions.ConnectionError as e:
            logger.error(f"⚠️ ConnectionError attempt {i+1}: {e}")
        except requests.exceptions.Timeout as e:
            logger.error(f"⚠️ Timeout attempt {i+1}: {e}")
        except requests.exceptions.SSLError as e:
            logger.error(f"⚠️ SSLError attempt {i+1}: {e}")
        except Exception as e:
            logger.error(f"⚠️ Unknown error attempt {i+1}: {type(e).__name__}: {e}")

        time.sleep(1)

    raise Exception("❌ Request failed after retries")

# --- SEND REQUESTS ---
def send_signed_request(http_method: str, path: str, payload: dict):
    headers = {"X-MBX-APIKEY": API_KEY}

    for attempt in range(3):

        try:
            params = payload.copy()

            params["timestamp"] = _now_ms()

            algo = check_algorithm(ALGORITHM)

            query_string = "&".join(
                [f"{k}={v}" for k, v in params.items()]
            )

            signature = hmac.new(
                API_SECRET.encode(),
                query_string.encode(),
                algo
            ).hexdigest()

            url = (
                f"{BASE_URL}{path}"
                f"?{query_string}"
                f"&signature={signature}"
            )

            if LOG_DEBUG:
                logger_admin(f"📋 SIGNED attempt={attempt+1}, {http_method} {path}, timestamp={params['timestamp']}")

            return request_with_retries(http_method, url, headers=headers)

        except Exception:

            if attempt == 2:
                raise

            time.sleep(1)

# --- CHECK ERROR CODES ---
def check_error(resp, symbol, task):
    if LOG_DEBUG:
        logger_admin(f"📋 {task} response for {symbol}: {resp}")

    if isinstance(resp, dict) and resp.get("code", 0) < 0:
        logger.error(f"⚠️ {task} skipped for {symbol}: {resp}")
        return {"error": f"{task.lower()}_issue", "code": resp.get("code")}
    return None


# ====== SYMBOL BUILDING ======
"""Returns symbol and asset structure based on the given platform quote and platform separator"""

# --- BUILD SYMBOL ---
def build_symbol(base_asset):
    return f"{base_asset}{SEPARATOR}{QUOTE}"

# --- GET BALANCE ASSET ---
def get_base_asset(symbol):
    suffix = f"{SEPARATOR}{QUOTE}"

    if not symbol.endswith(suffix):
        raise ValueError(f"{symbol} doesn't end with {suffix}")

    return symbol[:-len(suffix)]


# ====== BALANCE & MARKET DATA ======
"""Fetches free margin balance for a given asset and retrieves symbol lot size, tick size, and notional constraints from exchange info."""

# --- BALANCE FETCHING ---
def get_balance_margin(asset=QUOTE) -> float:
    q, sig = sign_params_query({"timestamp": _now_ms()}, API_SECRET)
    url = f"{BASE_URL}/sapi/v1/margin/account?{q}&signature={sig}"
    headers = {"X-MBX-APIKEY": API_KEY}
    data = request_with_retries("GET", url, headers=headers)
    bal = next((b for b in data.get("userAssets", []) if b["asset"] == asset), None)
    return float(bal["free"]) if bal else 0.0

# --- MARKET DATA FETCHING ---
def get_symbol_lot(symbol):
    if EXCHANGE_INFO is None:
        logger.error("⚠ Exchange info is none -> Reloading...")
        load_exchange_info()

    data = EXCHANGE_INFO
    for s in data["symbols"]:
        if s["symbol"] == symbol:
            fs = next((f for f in s["filters"] if f["filterType"] == "LOT_SIZE"), None)
            ts = next((f for f in s["filters"] if f["filterType"] == "PRICE_FILTER"), None)
            mnf = next((f for f in s["filters"] if f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL")), None)

            if not fs or not ts:
                raise Exception(f"❌ Missing LOT_SIZE or PRICE_FILTER for {symbol}")

            minNotional = float(mnf.get("minNotional") or mnf.get("notional") or 0.0) if mnf else 0.0
            return {
                "stepSize_str": fs["stepSize"],
                "stepSize": float(fs["stepSize"]),
                "minQty": float(fs.get("minQty", 0.0)),
                "tickSize_str": ts["tickSize"],
                "tickSize": float(ts["tickSize"]),
                "minNotional": minNotional,
            }

    raise Exception(f"❌ Symbol not found: {symbol}")

# --- MARGIN ACCOUNT FETCHING ---
def get_margin_account():
    acc = send_signed_request("GET", "/sapi/v1/margin/account", {})
    return acc


# ====== PRICE ADJUST (tickSize) ======
"""Utility functions for rounding prices and quantities to Binance-compliant tick sizes and step sizes."""

# --- TICK SIZE ---
def format_price_to_tick(price: float, tick_size_str: str, rounding=ROUND_DOWN) -> str:
    d_tick = Decimal(str(tick_size_str))
    p = Decimal(str(price)).quantize(d_tick, rounding=rounding)
    decimals = -d_tick.as_tuple().exponent if d_tick.as_tuple().exponent < 0 else 0
    return f"{p:.{decimals}f}"

# --- STEP SIZE
def floor_to_step_str(value, step_str):
    step = Decimal(str(step_str))
    v = Decimal(str(value))
    n = (v // step) * step
    decimals = -step.as_tuple().exponent if step.as_tuple().exponent < 0 else 0
    q = n.quantize(Decimal(1).scaleb(-decimals))
    return format(q, f".{decimals}f")

# --- TICK DECIMALS ---
def tick_decimals(tick_str: str):
    return len(tick_str.rstrip('0').split('.')[-1])


# ====== CHECK MARGIN LEVEL BEFORE OPERATING ======
"""Pre-trade margin safety check: blocks or limits trading based on margin level thresholds, triggers controlled liquidation if critical."""

# --- PRE MARGIN STATUS ---
TRADING_BLOCKED = False
MARGIN_MAX_RISK_PCT = MAX_RISK_PCT

# --- CHECK MARGIN LEVEL ---
def check_margin_level():
    global TRADING_BLOCKED, MARGIN_MAX_RISK_PCT

    try:
        account_info = get_margin_account()
        margin_level = float(account_info["marginLevel"])

        # ☠ FORCED LIQUIDATION
        if margin_level <= ML_LIQUID:
            logger.warning("☠ Your account got liquidated")
            TRADING_BLOCKED = True
            clear()
            return False

        # 🟥 CRITICAL — CONTROLLED LIQUIDATION
        if margin_level < ML_CRITICAL:
            logger.warning(f"🟥 CRITICAL! ML: {margin_level:.2f} — EXECUTING CONTROLLED LIQUIDATION")
            TRADING_BLOCKED = True
            clear()
            return False

        # 🟧 DANGER — BLOCK NEW ENTRIES
        elif margin_level < ML_DANGER:
            logger.warning(f"🟧 DANGER! ML: {margin_level:.2f} — BLOCKING NEW ENTRIES")
            TRADING_BLOCKED = True
            return True

        # 🟨 WARNING — LIMIT MAX RISK
        elif margin_level < ML_WARNING:
            logger.warning(f"🟨 WARNING! ML: {margin_level:.2f} — LIMITING MAX RISK TO 2%")
            MARGIN_MAX_RISK_PCT = 2
            return True

        # 🟩 HEALTHY
        else:
            if TRADING_BLOCKED:
                logger.info("🟩 Margin recovered — resuming normal operation")

            TRADING_BLOCKED = False
            MARGIN_MAX_RISK_PCT = MAX_RISK_PCT
            logger.info(f"🟩 Margin level healthy! ML: {margin_level:.2f}")
            return True

    except Exception as e:
        logger.error(f"⚠️ Could not fetch margin level: {e}")
        if any(x in str(e) for x in ["Request failed after retries", "ConnectTimeout", "ConnectionError"]):
            logger.error("⚠️ Network error — blocking trading as precaution")
            return False
        return True


# ====== FINAL RISK RESOLUTION ======
"""Resolves the effective risk percentage for a trade, applying webhook overrides and margin-based caps."""

def resolve_risk_pct(webhook_data=None):
    # 💯 USING DEFAULT RISK_PCT
    risk_pct = DFT_RISK_PCT

    # 💯 USING RISK_PCT FROM PAYLOAD
    if webhook_data and "risk_pct" in webhook_data:
        try:
            risk_pct = float(webhook_data["risk_pct"])
        except Exception:
            logger.error("⚠️ Invalid risk_pct from webhook")

    risk_pct = min(risk_pct, MARGIN_MAX_RISK_PCT)
    return risk_pct / 100


# ====== PRE-TRADE CLEANUP ======
"""Before each trade: cancels open orders, repays outstanding debt, and sells residual asset balance back to quote."""

# --- CANCEL ORDERS FROM PREVIOUS POSITIONS ---
def cancel(symbol: str):
    base_asset = get_base_asset(symbol)

    try:
        # 🧹 ORDER CANCEL PARAMS
        params = {
            "symbol": symbol,
            "timestamp": _now_ms()
        }

        # 🧹 ORDER CANCEL RESP
        resp = send_signed_request("DELETE", "/sapi/v1/margin/openOrders", params)

        # 🧹 NO ORDERS RESP
        if isinstance(resp, dict) and resp.get("code", 0) == -2011:
            logger.info(f"ℹ️ No open orders to cancel for {symbol}")
            return

        err = check_error(resp, symbol, "Cancel")
        if err:
            return err

        logger.info(f"🧹 Pending orders for {symbol} canceled")

    except Exception as e:
        logger.error(f"⚠️ Couldn't cancel orders for {symbol}: {e}")

# --- CANCEL ALL ORDERS FOR CLEAR ---
def cancel_all():
    try:
        q, sig = sign_params_query({"timestamp": _now_ms()}, API_SECRET)
        url = f"{BASE_URL}/sapi/v1/margin/openOrders?{q}&signature={sig}"
        headers = {"X-MBX-APIKEY": API_KEY}
        open_orders = request_with_retries("GET", url, headers=headers)

        if not open_orders:
            logger.info("ℹ️ No open orders found")
            return

        symbols = set(order["symbol"] for order in open_orders)

        for symbol in symbols:
            cancel(symbol)

    except Exception as e:
        logger.error(f"⚠️ Couldn't cancel all orders: {e}")

# --- GENERAL CLEANUP FROM PREVIOUS POSITIONS ---
def cleanup(symbol: str):
    base_asset = get_base_asset(symbol)

    try:
        lot = get_symbol_lot(symbol)
        q, sig = sign_params_query({"timestamp": _now_ms()}, API_SECRET)
        url = f"{BASE_URL}/sapi/v1/margin/account?{q}&signature={sig}"
        headers = {"X-MBX-APIKEY": API_KEY}
        acc_data = request_with_retries("GET", url, headers=headers)
        if not isinstance(acc_data, dict) or "userAssets" not in acc_data:
            logger.error(f"⚠️ Cleanup aborted for {base_asset}: invalid account response: {acc_data}")
            return
        asset_data = next((a for a in acc_data["userAssets"] if a["asset"] == base_asset), None)
        quote_data  = next((a for a in acc_data["userAssets"] if a["asset"] == QUOTE), None)

        if not asset_data:
            logger.info(f"ℹ️ {base_asset} not present in margin account")
            return

        borrowed  = float(asset_data["borrowed"])
        free_base = float(asset_data["free"])
        free_quote = float(quote_data["free"]) if quote_data else 0.0
        r = request_with_retries("GET", f"{BASE_URL}/api/v3/ticker/price", params={"symbol": symbol})
        price_est = float(r["price"])

        # --- Top up calculation ---
        missing_for_debt = max(0.0, borrowed - free_base)
        free_after_repay = free_base - borrowed + missing_for_debt
        extra_for_notional = 0.0

        if borrowed > 0 or free_base > 0:
            notional_after_repay = free_after_repay * price_est
            if notional_after_repay < lot["minNotional"] and free_after_repay >= 0:
                extra_for_notional = max(0.0, (lot["minNotional"] / price_est) - free_after_repay)

        buy_raw = (missing_for_debt + extra_for_notional) * 1.1

        if buy_raw > 0:
            buy_qty_str = floor_to_step_str(buy_raw, lot["stepSize_str"])
            buy_qty_f = float(buy_qty_str)

            if buy_qty_f > 0:
                buy_cost = buy_qty_f * price_est

                if buy_cost > free_quote:
                    logger.info(f"ℹ️ Not enough {QUOTE} for cleanup buy (need {buy_cost:.4f}, have {free_quote:.4f}) — skipping buy")
                elif buy_cost < lot["minNotional"]:
                    logger.info(f"ℹ️ Cleanup buy below minNotional ({buy_cost:.4f} < {lot['minNotional']}) — skipping buy")
                else:
                    # 🛒 TOP UP BUY PARAMS
                    params = {
                        "symbol": symbol,
                        "side": "BUY",
                        "type": "MARKET",
                        "quantity": buy_qty_str,
                        "timestamp": _now_ms()
                    }

                    # 🛒 TOP UP BUY RESP
                    resp = send_signed_request("POST", "/sapi/v1/margin/order", params)
                    err = check_error(resp, symbol, "Top Up Buy")
                    if err:
                        return err

                    logger.info(f"🛒 Cleanup buy: {buy_qty_str} {base_asset}")

                    # --- Refresh after buy ---
                    time.sleep(2)
                    for _ in range(3):
                        q, sig = sign_params_query({"timestamp": _now_ms()}, API_SECRET)
                        url = f"{BASE_URL}/sapi/v1/margin/account?{q}&signature={sig}"
                        acc_data = request_with_retries("GET", url, headers=headers)
                        asset_data = next((a for a in acc_data["userAssets"] if a["asset"] == base_asset), None)
                        borrowed  = float(asset_data["borrowed"])
                        free_base = float(asset_data["free"])
                        if free_base > 0:
                            break

        # --- Repay debt ---
        if borrowed == 0:
            logger.info(f"ℹ️ No active debt in {base_asset}")
        elif borrowed > 0:
            repay_amount = min(borrowed, free_base)
            if repay_amount > 0:
                # 💰 REPAY PARAMS
                params = {
                    "asset": base_asset,
                    "amount": str(repay_amount),
                    "timestamp": _now_ms()
                }

                # 💰 REPAY RESP
                resp = send_signed_request("POST", "/sapi/v1/margin/repay", params)
                err = check_error(resp, symbol, "Repay")
                if err:
                    return err

                logger.info(f"💰 Repay executed: {repay_amount} {base_asset}")
                remaining = borrowed - repay_amount

                if remaining > 0:
                    logger.info(f"⚠️ Remaining debt after repay: {remaining:.8f} {base_asset}")
                else:
                    logger.info(f"ℹ️ Debt fully cleared for {base_asset}")

                # --- Refresh after repay ---
                time.sleep(2)
                for _ in range(3):
                    q, sig = sign_params_query({"timestamp": _now_ms()}, API_SECRET)
                    url = f"{BASE_URL}/sapi/v1/margin/account?{q}&signature={sig}"
                    acc_data = request_with_retries("GET", url, headers=headers)
                    asset_data = next((a for a in acc_data["userAssets"] if a["asset"] == base_asset), None)
                    free_base = float(asset_data["free"])
                    if free_base > 0:
                        break

        # --- Sell residual ---
        step = Decimal(str(lot["stepSize_str"]))
        free_dec = Decimal(str(free_base))

        qty_floor_str = floor_to_step_str(free_base, lot["stepSize_str"])
        qty_floor_f = float(qty_floor_str)
        notional_floor = qty_floor_f * price_est

        if notional_floor >= lot["minNotional"]:
            qty_str = qty_floor_str
            qty_f = qty_floor_f
        else:
            qty_ceil = (free_dec / step).to_integral_value(rounding=ROUND_UP) * step
            qty_ceil_f = float(qty_ceil)
            if qty_ceil_f > free_base * 1.001:
                logger.info(f"ℹ️ Residual {base_asset} below minNotional — skipping sell")
                return
            qty_str = format(qty_ceil, "f")
            qty_f = qty_ceil_f

        if qty_f <= 0:
            logger.info(f"ℹ️ No residual {base_asset} to sell")
            return

        notional = qty_f * price_est
        if notional < lot["minNotional"]:
            logger.info(f"ℹ️ Residual {base_asset} below minNotional ({notional:.4f} < {lot['minNotional']}) — skipping sell")
            return

        time.sleep(2)

        # 🧹 RESIDUAL SELL PARAMS
        params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": qty_str,
            "timestamp": _now_ms()
        }

        # 🧹 RESIDUAL SELL RESP
        resp = send_signed_request("POST", "/sapi/v1/margin/order", params)
        err = check_error(resp, symbol, "Residual Sell")
        if err:
            return err

        logger.info(f"🧹 Sold residual {qty_str} {base_asset} to {QUOTE}")

    except Exception as e:
        logger.error(f"⚠️ Cleanup error for {base_asset}: {e}")


# ====== MAIN FUNCTIONS ======
"""Core trade execution: margin long (buy with quote quantity), margin short (borrow and sell), post-trade handling, and SL/TP placement."""

# --- MARGIN LONG ---
def execute_long_margin(symbol, strategy, webhook_data=None):
    lot = get_symbol_lot(symbol)
    balance_quote = get_balance_margin(QUOTE)
    risk_pct = resolve_risk_pct(webhook_data)
    qty_quote = balance_quote * risk_pct

    # 📈 MARGIN LONG PARAMS
    params = {
        "symbol": symbol,
        "side": "BUY",
        "type": "MARKET",
        "quoteOrderQty": format(qty_quote, "f"),
        "timestamp": _now_ms()
    }

    # 📈 MARGIN LONG RESP
    resp = send_signed_request("POST", "/sapi/v1/margin/order", params)
    err = check_error(resp, symbol, "Long")
    if err:
        return err

    trade_id = next_trade_id("Long")
    post_trade(symbol, strategy, "Long", resp, lot, webhook_data, trade_id)
    return {"order": resp, "trade_id": trade_id}

# --- MARGIN SHORT ---
def execute_short_margin(symbol, strategy, webhook_data=None):
    lot = get_symbol_lot(symbol)
    balance_quote = get_balance_margin(QUOTE)

    try:
        r = request_with_retries("GET", f"{BASE_URL}/api/v3/ticker/price", params={"symbol": symbol})
        price_est = float(r.get("price", 0))
    except Exception as e:
        logger.error(f"⚠️ Could not fetch price for {symbol}: {e}")
        return {"error": "price_fetch_failed"}

    if price_est <= 0:
        logger.error(f"⚠️ Invalid price detected: {price_est}")
        raise Exception ("❌ Invalid price")

    risk_pct = resolve_risk_pct(webhook_data)
    raw_qty = Decimal(str(balance_quote * risk_pct)) / Decimal(str(price_est))

    qty_str = borrowing(raw_qty, lot, price_est, symbol)

    if not qty_str:
        logger.error(f"⚠ Couldn't borrow {base_asset}, aborting short")
        return {"error": "borrow_failed"}

    # 📉 MARGIN SHORT PARAMS
    params = {
        "symbol": symbol,
        "side": "SELL",
        "type": "MARKET",
        "quantity": qty_str,
        "timestamp": _now_ms()
    }

    # 📉 MARGIN SHORT RESP
    resp = send_signed_request("POST", "/sapi/v1/margin/order", params)
    err = check_error(resp, symbol, "Short")
    if err:
        return err

    trade_id = next_trade_id("Short")
    post_trade(symbol, strategy, "Short", resp, lot, webhook_data, trade_id)
    return {"order": resp, "trade_id": trade_id}

# --- BORROWING (FOR SHORT) ---
def borrowing(raw_qty, lot, price_est, symbol):
    asset = get_base_asset(symbol)
    borrow_amount = float(raw_qty.quantize(Decimal(str(lot["stepSize_str"])), rounding=ROUND_DOWN))

    if borrow_amount <= 0 or borrow_amount < lot.get("minQty", 0.0):
        raise Exception(f"Qty {borrow_amount} < minQty")

    if (borrow_amount * price_est) < lot.get("minNotional", 0.0):
        raise Exception("Notional too small")

    # 📥 BORROW PARAMS
    params = {
        "asset": asset,
        "amount": format(Decimal(str(borrow_amount)), "f"),
        "timestamp": _now_ms()
    }

    # 📥 BORROW RESP
    resp = send_signed_request("POST", "/sapi/v1/margin/loan", params)
    err = check_error(resp, symbol, "Borrow")
    if err:
        return err

    time.sleep(0.3)

    borrowed_qty = float(
        resp.get("amount") or
        resp.get("qty") or
        borrow_amount
    )

    logger.info(f"📥 Borrowed {borrowed_qty} {asset}")
    qty_str = floor_to_step_str(borrowed_qty, lot["stepSize_str"])

    if float(qty_str) < lot.get("minQty", 0.0):
        raise Exception("Borrowed qty too small")

    return qty_str

# --- EXECUTION INFO ---
def extract_execution_info(resp):
    executed_qty = 0.0
    entry = None

    if isinstance(resp, dict) and "fills" in resp:
        executed_qty = sum(float(f["qty"]) for f in resp["fills"])
        spent_quote = sum(float(f["price"]) * float(f["qty"]) for f in resp["fills"])
        entry = (spent_quote / executed_qty) if executed_qty else None
        spent_qty = float(entry * executed_qty) if entry is not None else 'unknown'

    if not entry and isinstance(resp, dict):
        try:
            executed_qty = float(resp.get("executedQty", 0) or 0)
            cumm = float(resp.get("cummulativeQuoteQty", 0) or 0)
            entry = cumm / executed_qty if executed_qty else entry
        except Exception:
            pass

    return executed_qty, entry, spent_qty

# --- POST TRADE ---
def post_trade(symbol, strategy, side, resp, lot, webhook_data, trade_id):
    executed_qty, entry, spent_qty = extract_execution_info(resp)

    if executed_qty == 0:
        logger.error(f"[TRADE {trade_id}] ⚠️ No execution detected")
        return

    side_emoji = "📈" if side == "Long" else "📉"
    logger.info(
        f"[TRADE {trade_id}] {side_emoji} {side} executed {symbol}: "
        f"qty={executed_qty} (spent≈{spent_qty:.5f} {QUOTE})"
    )

    if executed_qty > 0 and entry:
        sl_from_web = None
        tp_from_web = None

        if webhook_data:
            sl_from_web = webhook_data.get("sl")
            tp_from_web = webhook_data.get("tp")

        # 📌 SL/TP PLACING
        success, order_type = place_sl_tp_margin(
            symbol,
            side,
            entry,
            executed_qty,
            lot,
            sl_override=sl_from_web,
            tp_override=tp_from_web,
            trade_id=trade_id
        )

        # 📋 LAST TRADE
        if success:
            update_last_trade(
                symbol,
                side,
                order_type,
                executed_qty,
                spent_qty,
                strategy
            )

    return executed_qty, entry


# ====== TRADE COUNTER ======
"""Thread-safe trade ID generator, daily trade summary logger, and midnight reset watcher."""

# --- TRADE ID ---
def next_trade_id(side):
    global TRADE_COUNTER, DAILY_LONGS, DAILY_SHORTS, TOTAL_LONGS, TOTAL_SHORTS

    # 📊 GENERAL TRADE COUNTER
    with TRADE_LOCK:
        TRADE_COUNTER += 1

    # 📈 TRADE COUNTER LONG
    if side == "Long":
        DAILY_LONGS += 1
        TOTAL_LONGS += 1

    # 📉 TRADE COUNTER SHORT
    elif side == "Short":
        DAILY_SHORTS +=1
        TOTAL_SHORTS +=1

    return TRADE_COUNTER

# --- DAILY SUMMARY ---
def check_daily_summary():
    global DAILY_LONGS, DAILY_SHORTS, CURRENT_DAY

    now_day = datetime.utcnow().date()

    if now_day != CURRENT_DAY:
        total_trades = DAILY_LONGS + DAILY_SHORTS

        if total_trades > 0:
            # 📅 DAY SUMMARY LOGGER
            logger.date(f"📅 Day {CURRENT_DAY} completed!")
            logger.date(f"Trades: {total_trades}")
            logger.date(f"(Longs: {DAILY_LONGS} | Shorts: {DAILY_SHORTS})")
            logger.date("_____________________________________\n")

        DAILY_LONGS = 0
        DAILY_SHORTS = 0
        CURRENT_DAY = now_day

# --- DAILY WATCHER ---
def daily_watcher():
    while True:
        check_daily_summary()
        time.sleep(60)

threading.Thread(target=daily_watcher, daemon=True).start()


# ====== LAST TRADE PAYLOAD ======
"""Showcases a payload with the data of the las succesfully executed in the account."""

# --- LAST TRADE PLACEHOLDER ---
LAST_TRADE = {}

# --- LAST TARDE FUNCTION ---
def update_last_trade(symbol: str, side: str, order_type: str, executed_qty: str, spent_qty: str, strategy: str):
    global LAST_TRADE

    # ⌚ LAST TRADE PAYLOAD
    LAST_TRADE = {
        "tradeId": TRADE_COUNTER,
        "symbol": symbol,
        "side": side,
        "order_type": order_type,
        "executed_qty": executed_qty,
        "spent_qty": spent_qty,
        "strategy": strategy,
        "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    }

    store_trade(LAST_TRADE.copy())


# ====== SL/TP FUNCTIONS ======
"""Places OCO, stop-loss-only, or take-profit-only orders after trade execution, with tick-aligned prices and commission-adjusted quantities."""

# --- SL/TP PLACING ---
def place_sl_tp_margin(symbol: str, side: str, entry: float, executed_qty: float, lot: dict, sl_override=None, tp_override=None, trade_id=None):
    try:
        COMMISSION_BUFFER = Decimal("1") - (COMMISSION / Decimal("100"))
        oco_side = "SELL" if side == "Long" else "BUY"

        # --- Determine if SL/TP should be used ---
        use_sl = sl_override is not None or (SL_OVERRIDE and SL_PCT is not None)
        use_tp = tp_override is not None or (TP_OVERRIDE and TP_PCT is not None)

        # --- If not requested SL/TP ---
        if not use_sl and not use_tp:
            logger.info(f"[TRADE {trade_id}] ℹ️ No SL/TP requested for {symbol}")
            return True

        # --- SL Price calculation ---
        if sl_override is not None:
            sl_price = float(sl_override)
        elif SL_OVERRIDE and SL_PCT is not None:
            sl_price = entry * (1 - SL_PCT / 100) if side == "BUY" else entry * (1 + SL_PCT / 100)
        else:
            sl_price = None

        # --- TP Price calculation ---
        if tp_override is not None:
            tp_price = float(tp_override)
        elif TP_OVERRIDE and TP_PCT is not None:
            tp_price = entry * (1 + TP_PCT / 100) if side == "BUY" else entry * (1 - TP_PCT / 100)
        else:
            tp_price = None

        # --- Tick alignment function ---
        def align_price(price: float, tick_str: str, rounding):
            tick = float(tick_str)

            if rounding == ROUND_DOWN:
                return math.floor(price / tick) * tick
            else:
                return math.ceil(price / tick) * tick

        decimals = lot["tickSize_str"].split('.')[-1].find('1')

        if decimals < 0:
            decimals = 8

        # --- Align SL/TP to tickSize ---
        sl_price_str = None
        tp_price_str = None
        stop_limit_price = None

        if sl_price is not None:
            sl_rounding = ROUND_DOWN if side == "BUY" else ROUND_UP
            sl_price_aligned = align_price(sl_price, lot["tickSize_str"], sl_rounding)
            sl_price_str = f"{sl_price_aligned:.{decimals}f}"

            if side == "BUY":
                stop_limit_aligned = align_price(sl_price_aligned * 1.001, lot["tickSize_str"], ROUND_DOWN)
            else:
                stop_limit_aligned = align_price(sl_price_aligned * 0.999, lot["tickSize_str"], ROUND_UP)

            stop_limit_price = f"{stop_limit_aligned:.{decimals}f}"

        if tp_price is not None:
            tp_rounding = ROUND_UP if side == "BUY" else ROUND_DOWN
            tp_price_aligned = align_price(tp_price, lot["tickSize_str"], tp_rounding)
            tp_price_str = f"{tp_price_aligned:.{decimals}f}"

        # --- Quantity alignment ---
        qty_str = floor_to_step_str(executed_qty * float(COMMISSION_BUFFER), lot["stepSize_str"])
        qty_f = float(qty_str)

        # --- Decide order type ---
        if sl_price_str and tp_price_str:
            order_type = "OCO"
        elif sl_price_str:
            order_type = "SL_ONLY"
        elif tp_price_str:
            order_type = "TP_ONLY"

        # --- Basic validations ---
        for label, price_str in [("SL", sl_price_str), ("TP", tp_price_str)]:
            if price_str is None:
                continue
            price_f = float(price_str)

            if price_f <= 0 or price_f < lot["tickSize"]:
                logger.error(f"⚠️ Skipping {label} for {symbol}: price {price_f} < tickSize {lot['tickSize']}")
                return False

            notional = price_f * qty_f

            if notional < lot.get("minNotional", 0.0):
                logger.error(f"⚠️ Skipping {label} for {symbol}: notional {notional:.8f} < minNotional {lot.get('minNotional')}")
                return False

        # --- Place OCO ---
        if order_type == "OCO":
            try:
                # 📌 OCO PARAMS
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

                # 📌 OCO RESP
                resp = send_signed_request("POST", "/sapi/v1/margin/order/oco", params)
                err = check_error(resp, symbol, "OCO")
                if err:
                    return err

                direction = 1 if side == "BUY" else -1
                entry_f = float(entry)
                tp_f = float(tp_price_str)
                sl_f = float(sl_price_str)
                profit_tp = (tp_f - entry_f) * qty_f * direction
                loss_sl = (sl_f - entry_f) * qty_f * direction
                rr = abs(profit_tp / loss_sl) if loss_sl != 0 else 0
                logger.info(f"[TRADE {trade_id}] 📌 OCO placed for {symbol}: TP={tp_price_str} ({oco_side}), SL={sl_price_str} ({oco_side}), qty={qty_f:.5f}")
                logger.info(f"[TRADE {trade_id}] 🟢 TP PnL ≈ {profit_tp:.2f} {QUOTE} | 🔴 SL PnL ≈ {loss_sl:.2f} {QUOTE} | ⚖️ R:R {rr:.2f}")
                return True, order_type
            except Exception as e:
                logger.error(f"⚠️ Failed OCO for {symbol}, payload={params}: {e}")
                return False

        # --- SL ONLY ---
        if order_type == "SL_ONLY":
            try:
                # 🛑 SL PARAMS
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

                # 🛑 SL RESP
                resp = send_signed_request("POST", "/sapi/v1/margin/order", params)
                err = check_error(resp, symbol, "SL")
                if err:
                    return err

                logger.info(f"[TRADE {trade_id}] 🛑 SL placed for {symbol}: stop={sl_price_str}, limit={stop_limit_price}, qty={qty_f:.5f}")
                return True, order_type
            except Exception as e:
                logger.error(f"⚠️ Could not place SL for {symbol}, payload={params}: {e}")
                return False

        # --- TP ONLY ---
        if order_type == "TP_ONLY":
            try:
                # 🎯 TP PARAMS
                params = {
                    "symbol": symbol,
                    "side": oco_side,
                    "type": "LIMIT",
                    "quantity": qty_str,
                    "price": tp_price_str,
                    "timeInForce": "GTC",
                    "timestamp": _now_ms()
                }

                # 🎯 TP RESP
                resp = send_signed_request("POST", "/sapi/v1/margin/order", params)
                err = check_error(resp, symbol, "TP")
                if err:
                    return err

                logger.info(f"[TRADE {trade_id}] 🎯 TP placed for {symbol}: price={tp_price_str}, qty={qty_f:.5f}")
                return True, order_type
            except Exception as e:
                logger.error(f"⚠️ Could not place TP for {symbol}, payload={params}: {e}")
                return False

    except Exception as e:
        logger.error(f"⚠️ Could not place SL/TP for {symbol}: {e}")
        return False


# ====== SNAPSHOT METRICS ======
"""Periodic account snapshots stored in memory for the metrics dashboard: balance, margin level, debt, and trade activity."""

# --- SNAPSHOT PLACEHOLDERS ---
SNAPSHOT_HISTORY = []
SNAPSHOT_LOCK = Lock()

# --- SNAPSHOT MEMORY STORAGE ---
def store_snapshot(snapshot):
    global SNAPSHOT_HISTORY

    with SNAPSHOT_LOCK:
        # 📸 CLEAN SNAPSHOT
        clean_snapshot = {
            # ⌚ TIME
            "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),

            # 💰 BALANCE
            "totalBalance": snapshot["totalBalance"],
            "quoteBalance": snapshot["quoteBalance"],
            "totalDebt": snapshot["totalDebt"],
            "quoteBorrowed": snapshot["quoteBorrowed"],

            # ⚖ RISK
            "marginLevel": snapshot["marginLevel"],

            # 📈 ACTIVITY
            "longsToday": snapshot["longsToday"],
            "shortsToday": snapshot["shortsToday"],
            "totalLongs": snapshot["totalLongs"],
            "totalShorts": snapshot["totalShorts"],
            "tradeId": snapshot["tradeId"],

            # 🐍 VARIABLES SNAPSHOT
            "variables": snapshot["variables"]
        }

        SNAPSHOT_HISTORY.append(clean_snapshot)

        if len(SNAPSHOT_HISTORY) > MAX_SNAPSHOTS:
            SNAPSHOT_HISTORY.pop(0)

# --- SNAPSHOT FORMATION ---
def build_snapshot():
    acc = get_margin_account()

    if isinstance(acc, dict) and acc.get("code") == -1021:
        logger.error("⚠️ Snapshot got -1021, retrying with fresh timestamp")

        time.sleep(1)
        acc = get_margin_account()

    if not isinstance(acc, dict) or "userAssets" not in acc:
        logger.error(f"⚠️ build_snapshot: unexpected margin account response: {acc}")
        raise Exception(f"Invalid margin account response: {acc}")

    total_debt = 0.0
    quote_balance = 0.0
    quote_borrowed = 0.0
    assets_with_balance = []

    for asset in acc["userAssets"]:
        borrowed = float(asset["borrowed"])
        free = float(asset["free"])
        locked = float(asset["locked"])

        # 💳 DEBT IN QUOTE
        quote_debt = 0.0

        if borrowed > 0:

            if asset["asset"] == QUOTE:
                quote_debt = borrowed

            else:
                try:
                    symbol=build_symbol(asset["asset"])
                    r = request_with_retries("GET", f"{BASE_URL}/api/v3/ticker/price", params={"symbol": symbol})
                    price = float(r["price"])
                    quote_debt = borrowed * price

                except Exception as e:
                    logger.error(f"⚠️ Could not convert debt for {asset['asset']} to {QUOTE}: {e}")

            total_debt += quote_debt

        total_asset_balance = free + locked

        if total_asset_balance > 0 and asset["asset"] != QUOTE:
            assets_with_balance.append({ "asset": asset["asset"], "balance": round(total_asset_balance, 8)})

        if asset["asset"] == {QUOTE}:
            quote_balance = free + locked
            quote_borrowed = borrowed

    btc_usdc_price = 0.0

    try:
        r = request_with_retries("GET", f"{BASE_URL}/api/v3/ticker/price", params={"symbol": build_symbol("BTC")})
        btc_usdc_price = float(r["price"])
    except Exception as e:
        logger.error(f"⚠️ BTC price fetch failed: {e}")

    total_balance = float(acc["totalNetAssetOfBtc"]) * btc_usdc_price

    # 🏷 ACCOUNT SNAPSHOT
    snapshot = {
        # ⌚ TIME
        "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),

        # 💰 BALANCE
        "totalBalance": round(total_balance, 8),
        "quoteBalance": round(quote_balance, 8),
        "totalDebt": round(total_debt, 8),
        "quoteBorrowed": round(quote_borrowed, 8),
        "assetsWithBalance": assets_with_balance,

        # ⚖ RISK
        "marginLevel": float(acc["marginLevel"]),

        # 📈 ACTIVITY
        "longsToday": DAILY_LONGS,
        "shortsToday": DAILY_SHORTS,
        "totalLongs": TOTAL_LONGS,
        "totalShorts": TOTAL_SHORTS,
        "tradeId": TRADE_COUNTER,
        "lastTrade": LAST_TRADE,

        # 🐍 VARIABLES SNAPSHOT
        "variables": {
            # 🎚 BOOL VARS
            "trading": TRADING,
            "sl_override": SL_OVERRIDE,
            "tp_override": TP_OVERRIDE,
            "log_debug": LOG_DEBUG,

            # 🔤 STRING VARS
            "platform": PLATFORM,

            # 🔢 ENV VARS
            "sl_pct": SL_PCT,
            "tp_pct": TP_PCT,
            "max_snapshots": MAX_SNAPSHOTS,
            "login_limit": LOGIN_LIMIT,
            "login_retry": LOGIN_RETRY,
            "session_time": SESSION_TIME,
        }
    }

    return snapshot

# --- SNAPSHOT TIME ---
def snapshot_time():
    now = datetime.utcnow()
    next_midnight = (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
    return (next_midnight - now).total_seconds()

# --- SNAPSHOT WORKER ---
def snapshot_worker():
    while True:
        sleep_seconds = max(1, snapshot_time())
        time.sleep(sleep_seconds)

        try:
            snapshot = build_snapshot()
            store_snapshot(snapshot)
            logger.info("📸 Daily snapshot stored\n")
        except Exception as e:
            logger.error(f"⚠️ Snapshot error: {e}\n")
            time.sleep(300)

# --- SNAPSHOT EXECUTION ---
try:
    Thread(target=snapshot_worker, daemon=True).start()
    logger.info("📷 Snapshot worker started")
except Exception as e:
    logger.error(f"❌ Error starting snapshot worker: {e}")
    raise


# ====== HISTORY ======
"""Stores every trade into a trade history file you can check."""

# --- TRADE HISTORY PLACEHOLDERS ---
TRADE_HISTORY = []
TRADE_HISTORY_LOCK = Lock()

# --- STORE TRADE HISTORY ---
def store_trade(trade):
    global TRADE_HISTORY

    with TRADE_HISTORY_LOCK:
        TRADE_HISTORY.append(trade)

        if len(TRADE_HISTORY) > MAX_HISTORY:
            TRADE_HISTORY.pop(0)


# ====== MILESTONES ======
"""Detects and logs balance milestones (1000, 2000, 5000...) as they are reached for the first time."""

# --- MILESTONES SETTINGS ---
MILESTONES_QUOTE = [1000, 2000, 5000, 10000, 20000, 50000]
REACHED_MILESTONES = set()

# --- CHECK MILESTONES ---
def check_milestones(total_balance: float):
    new_milestones = []

    for milestone in MILESTONES_QUOTE:
        if total_balance >= milestone and milestone not in REACHED_MILESTONES:
            REACHED_MILESTONES.add(milestone)
            new_milestones.append(milestone)

            # 🎉 MILESTONES LOGGER
            logger.info(f"🎉🎉 CONGRATS! 🎉🎉")
            logger.info(f"💰 You reached {milestone:,.0f} {QUOTE}")
            logger.info(f"🚀 Keep it up. Compounding is working") 
            logger.info(f"🔥 Discipline > Luck\n")

    return new_milestones


# ====== CENSORING KEYS ======
"""Redacts sensitive fields (admin_key, trading_key) from logged payloads."""

# --- SENSITIVE FIELDS ---
SENSITIVE_FIELDS = {"admin_key", "key"}

# --- SANITIZE PAYLOAD ---
def sanitize_payload(payload: dict) -> dict:
    clean = payload.copy()
    for field in SENSITIVE_FIELDS:
        if field in clean:
            clean[field] = "*****"
    return clean


# ====== DEPLOY LOADING ======
"""Loads Binance exchange info on startup and logs the deploy timestamp."""

# --- EXCHANGE INFO ---
SYMBOL_INFO_MAP = {}
EXCHANGE_INFO = {}

# --- EXCHANGE INFO LOADING ---
def load_exchange_info(max_attempts=5, delay=5):
    global EXCHANGE_INFO, SYMBOL_INFO_MAP
    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(f"📡 Loading exchange info (attempt {attempt}/{max_attempts})...")
            EXCHANGE_INFO = send_public_request("GET", "/api/v3/exchangeInfo")
            SYMBOL_INFO_MAP = {s["symbol"]: s for s in EXCHANGE_INFO.get("symbols", [])}
            logger.info(f"✅ Exchange info loaded ({len(SYMBOL_INFO_MAP)} symbols available)")
            return True
        except Exception as e:
            logger.error(f"❌ Exchange info attempt {attempt} failed: {e}")
            if attempt < max_attempts:
                logger.info(f"⏳ Retrying in {delay}s...")
                time.sleep(delay)
    logger.error("❌ Could not load exchange info after all attempts — bot will retry on first trade")
    return False

# --- EXCHANGE INFO LOADING ---
try:
    load_exchange_info()
except Exception as e:
    logger.error(f"❌ Error loading exchange info: {e}")
    raise

# --- LOG DEPLOY ---
deploy_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
logger.info(f"🚀 Deployed at {deploy_time}")
logger.info("________________________________________\n")


# ====== ADMIN FUNCTIONS ======
"""Administrative operations: clear positions, borrow/repay, toggle trading parameters, and restore defaults."""

# --- ADMIN CLEAR ---
def clear(symbol=None):
    if symbol:
        logger_admin(f"🔁 ADMIN ACTION: Converting {symbol} to {QUOTE}...")
        cancel(symbol)
    else:
        logger_admin("🔁 ADMIN ACTION: Converting ALL assets to {QUOTE}...")
        cancel_all()

    time.sleep(2)
    account = get_margin_account()
    cleared_symbols = []
    failed_symbols = []

    for asset in account["userAssets"]:
        asset_name = asset["asset"]
        free_qty = float(asset["free"])
        locked_qty = float(asset["locked"])
        borrowed_qty = float(asset["borrowed"])

        if asset_name == QUOTE:
            continue

        asset_symbol=build_symbol(asset_name)

        if symbol and asset_symbol != symbol:
            continue

        if free_qty <= 0 and locked_qty <= 0:
            continue

        try:
            get_symbol_lot(asset_symbol)
        except:
            logger.error(f"⚠️ No {QUOTE} pair for {asset_name}, skipping")
            continue

        try:
            logger.info(f"➡ Clearing {asset_name} (free={free_qty}, locked={locked_qty}, borrowed={borrowed_qty})")
            cleanup(asset_symbol)
            cleared_symbols.append(asset_symbol)
        except Exception as e:
            logger.error(f"⚠️ Could not convert {asset_symbol}: {e}")
            failed_symbols.append({"symbol": asset_symbol, "error": str(e)})

    logger.info("✅ CLEAR completed\n")
    return {"cleared": cleared_symbols, "failed": failed_symbols}

# --- ADMIN BORROW ---
def borrow(amount: float):
    asset = QUOTE
    logger_admin(f"📥 ADMIN ACTION: Borrow requested: {amount} {asset}")

    if amount <= 0:
        raise ValueError("Borrow amount must be > 0")

    acc = get_margin_account()
    margin_level = float(acc["marginLevel"])
    logger_admin(f"🧮 Current ML: {margin_level:.2f}")

    if margin_level < ML_WARNING:
        logger.error(f"⚠️ Margin level too low for leveraging")
        raise Exception("❌ Margin level too low to safely borrow {asset}")

    # 📥 LEVERAGE BORROW PARAMS
    params = {
        "asset": asset,
        "amount": format(amount, "f"),
        "timestamp": _now_ms()
    }

    # 📥 LEVERAGE BORROW RESP
    resp = send_signed_request("POST", "/sapi/v1/margin/loan", params)
    err = check_error(resp, asset, "Leverage Borrow")
    if err:
        return err

    logger_admin(f"✅ BORROW completed: {amount} {asset}\n")
    return resp

# --- ADMIN REPAY ---
def repay(amount):
    asset = QUOTE
    logger_admin(f"💳 ADMIN ACTION: Repay requested: {amount} {asset}")

    if isinstance(amount, str) and amount.lower() == "all":
        margin_info = get_margin_account()

        borrowed_quote = Decimal("0")
        for asset in margin_info["userAssets"]:
            if asset["asset"] == QUOTE:
                borrowed_quote = Decimal(asset["borrowed"])
                break

        if borrowed_quote <= 0:
            logger_admin("ℹ️ No {asset} debt to repay")
            return {"status": "nothing_to_repay"}

        amount = borrowed_quote
        logger_admin(f"🔁 REPAY ALL → {amount} {asset}")

    amount = Decimal(str(amount))

    if amount <= 0:
        raise ValueError("Repay amount must be > 0")

    # 💳 LEVERAGE REPAY PARAMS
    params = {
        "asset": asset,
        "amount": format(amount, "f"),
        "timestamp": _now_ms()
    }

    # 💳 LEVERAGE REPAY RESP
    resp = send_signed_request("POST", "/sapi/v1/margin/repay", params)
    err = check_error(resp, asset, "Leverage Repay")
    if err:
        return err

    logger_admin(f"✅ REPAY completed: {amount} {asset}\n")
    return resp

# --- ADMIN SET VAR ---
def set_var(var_name, value):
    try:
        vn = var_name.strip().lower()

        if vn not in SETTABLE_VARS:
            return {"status": "error", "msg": f"unknown variable: {var_name}"}

        meta = SETTABLE_VARS[vn]
        current_value = globals().get(meta["var"])

        # 🎚 BOOL HANDLING
        if meta["type"] == "bool":
            val = str(value).strip().lower()

            if val in ("on", "true", "1"):
                parsed = True
            elif val in ("off", "false", "0"):
                parsed = False
            else:
                return {"status": "error", "msg": f"invalid bool value for {var_name}"}

            emoji = meta["emoji_on"] if parsed else meta["emoji_off"]

        # 🔤 STRING HANDLING
        elif meta["type"] == str:
            parsed = str(value).strip().lower()

            if "allowed" in meta and parsed not in meta["allowed"]:
                return {
                    "status": "error",
                    "msg": f"invalid value for {var_name}. allowed: {meta['allowed']}"
                }

            emoji = meta.get("var_emoji", "🧠")

        # 🔢 NUMERIC HANDLING
        else:
            try:
                parsed = meta["type"](value)
            except Exception:
                return {"status": "error", "msg": f"invalid value for {var_name}"}

            if "min" in meta and "max" in meta:
                parsed = max(meta["min"], min(parsed, meta["max"]))

            emoji = meta.get("var_emoji", "⚙️")

        # 🔁 APPLY CHANGE
        if current_value == parsed:
            logger_admin(f"{emoji} {meta['var']} already {parsed}\n")
            return {"status": "ok", "var": vn, "value": parsed, "no_change": True}

        globals()[meta["var"]] = parsed
        logger_admin(f"{emoji} ADMIN ACTION: {meta['var']} → {parsed}\n")

        return {"status": "ok", "var": vn, "value": parsed}

    except Exception as e:
        logger.error(f"⚠ Variable setting error: {e}")
        return {"status": "error", "msg": str(e)}

# --- ADMIN RESTORE ---
def restore():
    logger_admin("💣 ADMIN ACTION: RESTORE default trading parameters")

    defaults = {
        "TRADING": DFT_TRADING,
        "SL_OVERRIDE": DFT_SL_OVERRIDE,
        "TP_OVERRIDE": DFT_TP_OVERRIDE,
        "LOG_DEBUG": DFT_LOG_DEBUG,
        "SL_PCT": DFT_SL_PCT,
        "TP_PCT": DFT_TP_PCT,
        "LOGIN_LIMIT": DFT_LOGIN_LIMIT,
        "LOGIN_RETRY": DFT_LOGIN_RETRY,
        "SESSION_TIME": DFT_SESSION_TIME,
    }

    globals().update(defaults)

    for name, value in defaults.items():
        logger_admin(f"🔄 {name} restored → {value}")

    logger.info("✅ RESTORE completed\n")

    result = defaults.copy()
    result["status"] = "ok"

    return result

# --- SETTABLE VARS ---
SETTABLE_VARS = {
    "trading":      {"type": "bool", "var": "TRADING",      "emoji_on": "▶", "emoji_off": "⏸"},
    "sl_override":  {"type": "bool", "var": "SL_OVERRIDE",  "emoji_on": "🟢", "emoji_off": "🔴"},
    "tp_override":  {"type": "bool", "var": "TP_OVERRIDE",  "emoji_on": "🟢", "emoji_off": "🔴"},
    "log_debug":    {"type": "bool", "var": "LOG_DEBUG",    "emoji_on": "📋", "emoji_off": "🔎"},
    "sl_pct":       {"type": float,  "var": "SL_PCT",       "var_emoji": "🔴", "min": MIN_SL_PCT,       "max": MAX_SL_PCT},
    "tp_pct":       {"type": float,  "var": "TP_PCT",       "var_emoji": "🟢", "min": MIN_TP_PCT,       "max": MAX_TP_PCT},
    "login_limit":  {"type": int,    "var": "LOGIN_LIMIT",  "var_emoji": "🛠", "min": MIN_LOGIN_LIMIT,  "max": MAX_LOGIN_LIMIT},
    "login_retry":  {"type": int,    "var": "LOGIN_RETRY",  "var_emoji": "🛠", "min": MIN_LOGIN_RETRY,  "max": MAX_LOGIN_RETRY},
    "session_time": {"type": int,    "var": "SESSION_TIME", "var_emoji": "🛠", "min": MIN_SESSION_TIME, "max": MAX_SESSION_TIME},
}


# ====== ADMIN SYSTEM ======
"""Session-based admin authentication: IP-tracked sessions with timeout, login rate limiting, and unauthorized request handling."""

# --- ADMIN PLACEHOLDERS ---
ADMIN_SESSIONS = {}
ADMIN_SESSIONS_LOCK = threading.Lock()
LOGIN_ATTEMPTS = {}

# --- ADMIN IP IDENTIFICATION ---
def get_ip():
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
        return ip
    return request.remote_addr

# --- ADMIN SESSION OPENING ---
def create_admin_session(ip):
    with ADMIN_SESSIONS_LOCK:
        ADMIN_SESSIONS[ip] = time.time()
        if ip in LOGIN_ATTEMPTS:
            del LOGIN_ATTEMPTS[ip]
    logger_admin(f"🔓 Admin session opened for {ip}\n")

# --- ADMIN SESSION CLOSING ---
def destroy_admin_session(ip):
    with ADMIN_SESSIONS_LOCK:
        if ip in ADMIN_SESSIONS:
            del ADMIN_SESSIONS[ip]
            logger_admin(f"🔐 Admin session closed for {ip}\n")

# --- ADMIN SESSION EXPIRING ---
def is_admin_authenticated():
    ip = get_ip()
    with ADMIN_SESSIONS_LOCK:
        if ip not in ADMIN_SESSIONS:
            return False
        last_activity = ADMIN_SESSIONS[ip]
        if time.time() - last_activity > (SESSION_TIME * 60):
            logger_admin(f"🔒 Admin session expired for {ip}\n")
            del ADMIN_SESSIONS[ip]
            return False
        ADMIN_SESSIONS[ip] = time.time()
    return True

# --- RETURNS WHEN UNAUTHORIZED ---
def handle_unauthorized():
    if "text/html" in request.headers.get("Accept", ""):
        return redirect(url_for("login"))
    else:
        return jsonify({"error": "unauthorized"}), 403

# --- LOGIN ATTEMPTS ---
def is_rate_limited(ip):
    now = time.time()
    attempts = LOGIN_ATTEMPTS.get(ip, [])

    attempts = [t for t in attempts if now - t < (LOGIN_RETRY * 60)]
    attempts.append(now)
    LOGIN_ATTEMPTS[ip] = attempts

    return len(attempts) > LOGIN_LIMIT

# --- ADMIN LOGGING ---
LOG_LOCK = Lock()

def logger_admin(msg):
    with LOG_LOCK:
        logger.admin(msg)


# ====== FLASK WEBHOOK ======
"""Webhook endpoint that receives trading signals, validates them, and dispatches trade execution in a background thread."""

# --- BACKEND ENDPOINTS ---
TRADE_LOCK = threading.RLock()

@app.route("/webhook", methods=["POST"])
def webhook():

    # 📝 DATA EXCTRACTION
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    if not data:
        return jsonify({"error": "Empty payload"}), 400

    # 🩺 HEALTH CHECK
    allowed, response = trading_guard()

    if not allowed:
        return response

    # ❓ RETURN FOR MISSING DATA
    if "symbol" not in data or "side" not in data or "strategy" not in data:
        logger.info(f"📩 JSON received: {sanitize_payload(data)}")
        if "symbol" not in data:
            logger.error("❓ Missing trading fields: symbol")
        if "side" not in data:
            logger.error("❓ Missing trading fields: side")
        if "strategy" not in data:
            logger.error("❓ Missing trading fields: strategy")
        return jsonify({"error": "Missing trading fields\n"}), 400

    # 🚫 RETURN FOR INCORRECT KEY
    if TRADING_KEY:
        if data.get("key") != TRADING_KEY:
            logger.info(f"📩 JSON received: {sanitize_payload(data)}")
            if data.get("key") == "":
                logger.error("🚫 Missing trading_key\n")
            else:
                logger.error("🚫 Invalid trading_key\n")
            return jsonify({"status": "blocked", "reason": "invalid trading key"}), 403

    # ✅ TRADE PROCESSING
    executor.submit(process_trade, data)
    return jsonify({"status": "ok", "result": "accepted"}), 200

def process_trade(data):
    symbol = data["symbol"]
    side = data["side"].upper()
    strategy = data["strategy"]
    start = time.time()

    try:
        with TRADE_LOCK:
            with LOG_LOCK:

                # 📩 JSON RECEIVING
                logger.info(f"📩 JSON received: {sanitize_payload(data)}")

                # ⛔ DANGEROUS MARGIN LEVEL
                if not check_margin_level():
                    logger.error("⛔ Trading blocked (critical margin condition)\n")
                    return

                # ⛔ TRADING BLOCKING
                if TRADING_BLOCKED:
                    logger.error("⛔ Trading blocked by margin safety system\n")
                    return

                # 🧹 PRE-TRADE CLEANUP
                cancel(symbol)
                cleanup(symbol)

                # 💹 MARKET BUY / SELL AND OCO
                if side == "BUY":
                    resp = execute_long_margin(symbol, strategy, webhook_data=data)
                    trade_id = resp.get("trade_id") if resp else "UNKNOWN"
                elif side == "SELL":
                    resp = execute_short_margin(symbol, strategy, webhook_data=data)
                    trade_id = resp.get("trade_id") if resp else "UNKNOWN"
                else:
                    logger.error("⛔ Trading blocked due to invalid side\n")
                    return

                # 📋 PRINT SYMBOL INFO
                if LOG_DEBUG:
                    symbol_info = SYMBOL_INFO_MAP.get(symbol)
                    logger_admin(f"📋 {symbol} info: {symbol_info}")

                # ⏳ LATENCY
                latency = time.time() - start
                logger.info(f"[TRADE {trade_id}] ⏳ Trade execution latency: {latency:.2f}s\n")

    except Exception as e:
        logger.error(f"🔥 CRITICAL TRADE ERROR: {e}\n", exc_info=True)


@app.route("/clear", methods=["GET"])
def admin_clear():
    if not is_admin_authenticated():
        return handle_unauthorized()

    symbol = request.args.get("symbol")
    threading.Thread(target=clear, args=(symbol,)).start()
    return jsonify({"status": "cleared"}), 200


@app.route("/borrow", methods=["GET"])
def admin_borrow():
    if not is_admin_authenticated():
        return handle_unauthorized()

    try:
        amount = float(request.args.get("amount", 0))
    except:
        return jsonify ({"error": "Invalid amount"}), 400

    try:
        threading.Thread(target=borrow, args=(amount,)).start()
        return jsonify({"status": "borrowed"}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/repay", methods=["GET"])
def admin_repay():
    if not is_admin_authenticated():
        return handle_unauthorized()

    amount_param = request.args.get("amount", "0")

    if amount_param.lower() == "all":
        amount = "all"
    else:
        try:
            amount = float(amount_param)
        except:
            return jsonify({"error": "Invalid amount"}), 400

    try:
        threading.Thread(target=repay, args=(amount,)).start()
        return jsonify({"status": "repaid"}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/set", methods=["GET"])
def admin_set():
    if not is_admin_authenticated():
        return handle_unauthorized()

    var_name = request.args.get("var")
    value = request.args.get("value")

    if not var_name or value is None:
        return jsonify({"status": "error", "msg": "missing var or value"}), 400

    try:
        result = set_var(var_name, value)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/restore", methods=["GET"])
def admin_restore():
    if not is_admin_authenticated():
        return handle_unauthorized()

    threading.Thread(target=restore, args=()).start()
    return jsonify({"status": "restored"}), 200


@app.route("/logout", methods=["GET"])
def admin_logout():
    ip = get_ip()

    if not ip:
        return jsonify({"error": "admin_auth_required"}), 403

    destroy_admin_session(ip)
    return jsonify({"status": "logged out"}), 200


@app.route("/health", methods=["GET"])
def health():
    uptime = int(time.time() - BOOT_TIME)
    return jsonify({"bot_ready": BOT_READY, "trading": TRADING, "uptime_seconds": uptime})


@app.route("/snapshot", methods=["GET"])
def admin_snapshot():

    if not is_admin_authenticated():
        return handle_unauthorized()

    try:

        snapshot = build_snapshot()
        milestones = check_milestones(snapshot["totalBalance"])
        snapshot["milestonesReached"] = milestones

        if LOG_DEBUG:
            logger_admin(f"📋 SNAPSHOT_HISTORY SIZE = {len(SNAPSHOT_HISTORY)}")

        return jsonify(snapshot), 200

    except Exception as e:
        logger.error(f"⚠️ Snapshot endpoint error: {e}, exc_info=True")
        return jsonify({"error": str(e)}), 500


@app.template_filter('log_class')
def log_class_filter(line):
    if '| ERROR |' in line:
        return 'log-line log-error'
    if '| WARNING |' in line:
        return 'log-line log-warning'
    if '| ADMIN |' in line:
        return 'log-line log-admin'
    if '| DATE |' in line:
        return 'log-line log-date'
    return 'log-line'


# --- FRONTEND ENDPOINTS ---
@app.route("/")
def index():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta name="description" content="SGNT — Automated margin trading system.">
        <title>SGNT</title>
        <link rel="icon" type="image/png" href="/static/sgnticon.png">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet">
        <style>
            *{box-sizing:border-box;margin:0;padding:0}
            body{background:#0f172a;color:#e2e8f0;font-family:'Inter',sans-serif;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:40px 20px;transition:background 0.2s,color 0.2s;overflow-x:hidden}
            body.light{background:#f8fafc;color:#0f172a}

            .topbar{position:fixed;top:20px;right:20px}
            .theme-toggle{background:none;border:0.5px solid #334155;border-radius:20px;padding:4px 10px;cursor:pointer;font-size:13px;color:#94a3b8;transition:0.15s}
            body.light .theme-toggle{border-color:#cbd5e1;color:#64748b}
            .theme-toggle:hover{background:#1e293b}
            body.light .theme-toggle:hover{background:#e2e8f0}

            .adam-row{display:flex;align-items:center;justify-content:center;width:100%;max-width:1200px;gap:0}
            .adam-pre{font-family:'Courier New',monospace;font-size:11px;line-height:1.35;color:#334155;white-space:pre;flex-shrink:0;transition:color 0.2s}
            body.light .adam-pre{color:#1f2937}
            .adam-center{display:flex;flex-direction:column;align-items:center;justify-content:center;flex:1;min-width:200px;padding:0 16px}
            .logo{width:140px;margin-bottom:20px;opacity:0.95}
            .tagline{font-size:12px;letter-spacing:0.12em;color:#475569;text-transform:uppercase;margin-bottom:24px;font-family:'Courier New',monospace;text-align:center}

            .cards{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;width:100%;margin-bottom:24px}
            .card{background:#1e293b;border:0.5px solid #334155;border-radius:8px;padding:12px;text-align:center}
            body.light .card{background:#ffffff;border-color:#e2e8f0}
            .card-val{font-size:12px;font-family:'Courier New',monospace;color:#94a3b8;margin-bottom:4px}
            body.light .card-val{color:#475569}
            .card-label{font-size:12px;letter-spacing:0.06em;color:#475569;text-transform:uppercase}

            .btn-login{padding:8px 28px;font-size:11px;letter-spacing:0.06em;border:0.5px solid #334155;border-radius:6px;background:transparent;color:#94a3b8;cursor:pointer;text-decoration:none;transition:0.15s;font-family:'Inter',sans-serif;text-transform:uppercase}
            body.light .btn-login{border-color:#cbd5e1;color:#64748b}
            .btn-login:hover{background:#1e293b;color:#f1f5f9;border-color:#64748b}
            body.light .btn-login:hover{background:#e2e8f0;color:#0f172a}
            .footer{position:fixed;bottom:20px;font-size:12px;color:#1e293b;letter-spacing:0.08em;font-family:'Courier New',monospace}
            body.light .footer{color:#94a3b8}
            @media (max-width: 600px) {
                .db { grid-template-columns: 1fr !important; }
                .col-2 { grid-column: span 1 !important; }
                .adam-pre { display: none; }
                .cards { grid-template-columns: 1fr 1fr 1fr; }
                .metric-big { font-size: 18px; }
                .btn { min-height: 36px; padding: 8px 12px; }
                .btn-minmax { min-height: 32px; padding: 6px 8px; }
                .input-row input[type=number], .input-row input[type=text] { min-height: 36px; }
                .toggle { width: 40px; height: 22px; }
                .slider:before { width: 16px; height: 16px; }
                input:checked+.slider:before { transform: translateX(18px); }
            }

        </style>
    </head>
    <body>

        <div class="topbar">
            <button class="theme-toggle" onclick="toggleTheme()" id="theme-btn">🌙</button>
        </div>

        <div class="adam-row">
            <pre class="adam-pre" id="hand-left">
                     ++++++*****              
               ===-===----==+++=+******       
           ==--::-=+*###**=--===-====++**     
==-------------==+**#####%#=----==++=---=*#%%
=====---------==+*###%%%%%%*+=----+###*+++==+*
+++==========+**####%%%%    *+*+--=*%%%%%%%##=
++++====+**#######%%%%          #*===#%%%%%%%%
====+++**########%%%                *++## %%%%%
===+++**####%%%%%                   ###+=%%%%%@
+***####%%%%%                              %###
######%%%%                                     
%%%%%%%                                        
                                               
                                               
                                               </pre>

            <div class="adam-center">
                <img src="/static/sgntlogo.png" id="logo" class="logo" alt="SGNT">
                <div class="tagline">Automated margin trading system</div>
                <div class="cards">
                    <div class="card">
                        <div class="card-val" id="status">—</div>
                        <div class="card-label">Status</div>
                    </div>
                    <div class="card">
                        <div class="card-val" id="platform">—</div>
                        <div class="card-label">Platform</div>
                    </div>
                    <div class="card">
                        <div class="card-val" id="uptime">—</div>
                        <div class="card-label">Uptime</div>
                    </div>
                </div>
                <a href="/login" class="btn-login">Access</a>
            </div>

            <pre class="adam-pre" id="hand-right">
                                          
                                             +====
                                     +====-----==+  
                       ==-===------=--------====+* 
              ==--=---=---=+===++===-----====+++++
#+====*==+=--===+=-===+***+*#***+=======--------=+
 %%%%%%%%%*+++#=-==-=+#%%##********+++==========+*
          ##+--+*#*+*#%%%%##%%####**#####*******##
       *+===+*#**%###%%@%@  %%%%%####%%%%%%%%#%%%%
        #+*#*+#*+***%%@@            @@@%%%%%%%@@@@
        ++##*%*+#%%@               
        *+%*#%**%@                 
        **%*# ==+                  
                                       </pre>
        </div>

        <div class="footer">SGNT · Autonomous trading infrastructure</div>

        <script>
            function toggleTheme() {
                const light = document.body.classList.toggle('light');
                document.getElementById('theme-btn').textContent = light ? '☀️' : '🌙';
                localStorage.setItem('sgnt-theme', light ? 'light' : 'dark');
                document.getElementById('logo').src = light ? '/static/sgntlogo0.png' : '/static/sgntlogo.png';
            }

            if (localStorage.getItem('sgnt-theme') === 'light') {
                document.body.classList.add('light');
                document.getElementById('theme-btn').textContent = '☀️';
                document.getElementById('logo').src = '/static/sgntlogo0.png';
            }

            async function loadHealth() {
                try {
                    const r = await fetch('/health');
                    const d = await r.json();
                    document.getElementById('status').textContent = d.bot_ready ? 'Ready' : 'Booting';
                    document.getElementById('status').style.color = d.bot_ready ? '#4ade80' : '#fb923c';
                    const s = d.uptime_seconds;
                    const h = Math.floor(s / 3600);
                    const m = Math.floor((s % 3600) / 60);
                    document.getElementById('uptime').textContent = h + 'h ' + m + 'm';
                } catch(e) {
                    document.getElementById('status').textContent = 'Offline';
                    document.getElementById('status').style.color = '#f87171';
                }
            }

            loadHealth();
            setInterval(loadHealth, 30000);
        </script>

    </body>
    </html>
    """
    return html


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        ip = get_ip()
        now = time.time()
        attempts = LOGIN_ATTEMPTS.get(ip, [])
        attempts = [t for t in attempts if now - t < (LOGIN_RETRY * 60)]
        LOGIN_ATTEMPTS[ip] = attempts

        if len(attempts) >= LOGIN_LIMIT:
            retry_after = int((LOGIN_RETRY * 60) - (now - attempts[0]))

            if request.is_json:
                return jsonify({"error": "Too many login attempts", "retry_after": retry_after}), 429
            else:
                error = f"Too many attempts. Try again in {retry_after}s."
        else:
            if request.is_json:
                data = request.get_json()
                admin_key = data.get("admin_key")
            else:
                admin_key = request.form.get("admin_key")

            if admin_key == ADMIN_KEY:
                create_admin_session(ip)

                if request.is_json:
                    return jsonify({"status": "logged in"}), 200

                return redirect(url_for("dashboard"))

            else:
                attempts.append(now)
                LOGIN_ATTEMPTS[ip] = attempts

                if request.is_json:
                    return jsonify({"error": "Invalid admin key"}), 401

                error = "Invalid admin key"

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta name="description" content="SGNT — Automated margin trading system.">
        <title>SGNT • Login</title>
        <link rel="icon" type="image/png" href="/static/sgnticon.png">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet">
        <style>
            *{box-sizing:border-box;margin:0;padding:0}
            body{background:#0f172a;color:#e2e8f0;font-family:'Inter',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;transition:background 0.2s,color 0.2s}
            body.light{background:#f8fafc;color:#0f172a}

            .topbar{position:fixed;top:20px;right:20px}
            .theme-toggle{background:none;border:0.5px solid #334155;border-radius:20px;padding:4px 10px;cursor:pointer;font-size:13px;color:#94a3b8;transition:0.15s}
            body.light .theme-toggle{border-color:#cbd5e1;color:#64748b}
            .theme-toggle:hover{background:#1e293b}
            body.light .theme-toggle:hover{background:#e2e8f0}

            .box{width:300px;text-align:center}
            .icon{width:48px;margin:0 auto 20px;display:block}
            .title{font-size:13px;font-weight:500;color:#f1f5f9;margin-bottom:4px;letter-spacing:0.02em}
            body.light .title{color:#0f172a}

            .subtitle{font-size:11px;color:#475569;margin-bottom:28px;font-family:'Courier New',monospace;letter-spacing:0.06em}

            .input-wrap{position:relative;margin-bottom:10px}
            input[type=password]{width:100%;padding:10px 14px;background:#1e293b;border:0.5px solid #334155;border-radius:6px;color:#f1f5f9;font-size:13px;font-family:'Inter',sans-serif;outline:none;transition:border-color 0.15s}
            body.light input[type=password]{background:#ffffff;border-color:#cbd5e1;color:#0f172a}

            input[type=password]:focus{border-color:#64748b}
            input[type=password]::placeholder{color:#475569}

            .btn{width:100%;padding:10px;background:transparent;border:0.5px solid #334155;border-radius:6px;color:#94a3b8;font-size:12px;letter-spacing:0.06em;text-transform:uppercase;cursor:pointer;transition:0.15s;font-family:'Inter',sans-serif;margin-top:4px}
            body.light .btn{border-color:#cbd5e1;color:#64748b}
            .btn:hover{background:#1e293b;color:#f1f5f9;border-color:#64748b}
            body.light .btn:hover{background:#e2e8f0;color:#0f172a}

            .error{font-size:11px;color:#f87171;margin-top:14px;font-family:'Courier New',monospace}

            .back{display:block;margin-top:20px;font-size:11px;color:#334155;text-decoration:none;letter-spacing:0.04em}
            body.light .back{color:#64748b}
            .back:hover{color:#64748b}
        </style>
    </head>
    <body>

        <div class="topbar">
            <button class="theme-toggle" onclick="toggleTheme()" id="theme-btn">🌙</button>
        </div>

        <div class="box">
            <img src="/static/sgnticon.png" class="icon" alt="SGNT" onclick="window.location.href='/'" style="cursor: pointer; width: 150px;">
            <div class="title">SGNT</div>
            <div class="subtitle">Admin access</div>

            <form method="POST">
                <div class="input-wrap">
                    <input type="password" name="admin_key" placeholder="Admin key" required autofocus>
                </div>
                <button type="submit" class="btn">Login</button>
                {% if error %}
                    <div class="error">{{ error }}</div>
                {% endif %}
            </form>

            <a href="/" class="back">← Back</a>
        </div>

        <script>
            function toggleTheme() {
                const light = document.body.classList.toggle('light');
                document.getElementById('theme-btn').textContent = light ? '☀️' : '🌙';
                localStorage.setItem('sgnt-theme', light ? 'light' : 'dark');
            }

            if (localStorage.getItem('sgnt-theme') === 'light') {
                document.body.classList.add('light');
                document.getElementById('theme-btn').textContent = '☀️';
            }
        </script>

    </body>
    </html>
    """
    return render_template_string(html, error=error)


@app.route("/dashboard")
def dashboard():
    if not is_admin_authenticated():
        return handle_unauthorized()

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta name="description" content="SGNT — Automated margin trading system.">
        <title>SGNT • Dashboard</title>
        <link rel="icon" type="image/png" href="/static/sgnticon.png">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet">
        <style>
            <!-- BOX STYLE -->
            *{box-sizing:border-box;margin:0;padding:0}
            body{background:#0f172a;color:#e2e8f0;font-family:'Inter',sans-serif;font-size:14px;transition:background 0.2s,color 0.2s}
            body.light{background:#f8fafc;color:#0f172a}

            <!-- SIDEBAR STYLE -->
            .layout{display:flex;min-height:100vh}
            .sidebar{width:160px;min-width:160px;background:#0a1120;border-right:0.5px solid #1e293b;display:flex;flex-direction:column;padding:16px 0;position:fixed;top:0;left:0;height:100vh;z-index:50}
            body.light .sidebar{background:#f1f5f9;border-right-color:#e2e8f0}
            .main{margin-left:160px;display:flex;flex-direction:column;flex:1;min-height:100vh}

            <!-- TOPBAR STYLE -->
            .topbar{position:fixed;top:0;left:160px;right:0;z-index:40;display:flex;justify-content:space-between;align-items:center;padding:0 20px;height:48px;background:#0f172a;border-bottom:0.5px solid #1e293b}
            body.light .topbar{background:#f8fafc;border-bottom-color:#e2e8f0}
            .content{padding:20px;margin-top:48px}
            .sidebar-logo{display:flex;align-items:center;justify-content:center;padding:0 16px 16px;border-bottom:0.5px solid #1e293b;margin-bottom:8px}
            body.light .sidebar-logo{border-bottom-color:#e2e8f0}
            .nav-item{display:flex;align-items:center;gap:8px;padding:8px 16px;font-size:11px;letter-spacing:0.06em;text-transform:uppercase;color:#475569;text-decoration:none;cursor:pointer;transition:0.15s;border-left:2px solid transparent;font-family:'Courier New',monospace}
            .nav-item:hover{color:#94a3b8;background:rgba(255,255,255,0.03)}
            body.light .nav-item:hover{background:rgba(0,0,0,0.04)}
            .nav-item.active{color:#f1f5f9;border-left-color:#1D9E75;background:rgba(29,158,117,0.08)}
            body.light .nav-item.active{color:#0f172a;border-left-color:#1D9E75;background:rgba(29,158,117,0.1)}
            body.light .nav-item{color:#64748b}

            <!-- TOPBAR LEFT & RIGHT STYLE -->
            .topbar-left{display:flex;align-items:center;gap:10px}
            .topbar-right{display:flex;align-items:center;gap:8px}
            .topbar-title{font-size:13px;font-weight:500;color:#f1f5f9}
            body.light .topbar-title{color:#0f172a}

            <!-- ACTIVE SIGNAL STYLE -->
            .dot{width:8px;height:8px;border-radius:50%;background:#1D9E75;display:inline-block}
            .dot.red{background:#E24B4A}
            .status-text{font-size:12px;color:#94a3b8;font-family:'Courier New',monospace}
            body.light .status-text{color:#64748b}

            <!-- TAG STYLE -->
            .tag{font-size:12px;padding:2px 8px;border-radius:6px;border:0.5px solid;font-family:'Courier New',monospace}
            .tag-live{border-color:#1D9E75;color:#1D9E75}

            <!-- THEME TOGGLE STYLE -->
            .theme-toggle{background:none;border:0.5px solid #334155;border-radius:20px;padding:4px 10px;cursor:pointer;font-size:13px;color:#94a3b8;transition:0.15s}
            body.light .theme-toggle{border-color:#cbd5e1;color:#64748b}
            .theme-toggle:hover{background:#1e293b}
            body.light .theme-toggle:hover{background:#e2e8f0}

            <!-- BUTTON STYLE -->
            .btn{padding:5px 12px;font-size:11px;border:0.5px solid #334155;border-radius:6px;background:#0f172a;color:#94a3b8;cursor:pointer;white-space:nowrap;transition:0.15s}
            body.light .btn{background:#f8fafc;border-color:#cbd5e1;color:#64748b}
            .btn:hover{background:#1e293b;color:#f1f5f9}
            body.light .btn:hover{background:#e2e8f0;color:#0f172a}
            .btn-danger{border-color:#7f1d1d;color:#fca5a5}
            .btn-danger:hover{background:#450a0a}

            .db{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;padding:12px 0}
            .card{background:#1e293b;border:0.5px solid #334155;border-radius:8px;padding:14px 16px}
            body.light .card{background:#ffffff;border-color:#e2e8f0}
            .card-title{font-size:12px;letter-spacing:0.08em;color:#64748b;text-transform:uppercase;margin-bottom:10px;border-bottom:0.5px solid #334155;padding-bottom:6px}
            body.light .card-title{border-bottom-color:#e2e8f0}
            .metric-big{font-size:22px;font-weight:500;color:#f1f5f9;font-family:'Courier New',monospace}
            body.light .metric-big{color:#0f172a}
            .metric-label{font-size:11px;color:#64748b;margin-top:2px}
            .metric-row{display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:0.5px solid #1e293b;font-size:12px}
            body.light .metric-row{border-bottom-color:#e2e8f0}
            .metric-row:last-child{border-bottom:none}
            .metric-row .k{color:#94a3b8}
            body.light .metric-row .k{color:#64748b}
            .metric-row .v{color:#f1f5f9;font-weight:500;font-family:'Courier New',monospace}
            body.light .metric-row .v{color:#0f172a}
            .section-label{font-size:12px;letter-spacing:0.08em;color:#475569;text-transform:uppercase;margin:10px 0 4px;grid-column:1/-1;padding-left:2px}
            .asset-row{display:flex;justify-content:space-between;font-size:12px;padding:4px 0;border-bottom:0.5px solid #1e293b;color:#94a3b8}
            body.light .asset-row{border-bottom-color:#e2e8f0}
            .asset-row:last-child{border-bottom:none}
            .margin-bar-bg{height:4px;background:#334155;border-radius:4px;margin-top:4px;overflow:hidden}
            .margin-bar-fill{height:100%;border-radius:4px;background:#1D9E75;transition:width 0.4s}
            .toast{position:fixed;bottom:16px;right:16px;background:#1e293b;border:0.5px solid #334155;border-radius:8px;padding:8px 14px;font-size:12px;color:#f1f5f9;opacity:0;transition:opacity 0.3s;z-index:100;font-family:'Courier New',monospace}
            .milestone-overlay{position:fixed;inset:0;background:rgba(0,0,0,0.75);display:flex;align-items:center;justify-content:center;z-index:200;opacity:0;pointer-events:none;transition:opacity 0.4s}
            .milestone-overlay.show{opacity:1;pointer-events:all}
            .milestone-box{background:#1e293b;border:0.5px solid #334155;border-radius:12px;padding:32px 40px;text-align:center;max-width:340px}
            body.light .milestone-box{background:#ffffff;border-color:#e2e8f0}
            .milestone-emoji{font-size:40px;margin-bottom:12px}
            .milestone-title{font-size:22px;font-weight:500;color:#f1f5f9;margin-bottom:6px;font-family:'Courier New',monospace}
            body.light .milestone-title{color:#0f172a}
            .milestone-sub{font-size:12px;color:#64748b;margin-bottom:20px;line-height:1.6}
            .milestone-close{padding:8px 24px;font-size:11px;border:0.5px solid #334155;border-radius:6px;background:transparent;color:#94a3b8;cursor:pointer;letter-spacing:0.06em;text-transform:uppercase}
            .milestone-close:hover{background:#334155;color:#f1f5f9}
            .assets-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:8px;margin-top:4px}
            .asset-pill{background:#0f172a;border:0.5px solid #334155;border-radius:6px;padding:8px 12px;font-family:'Courier New',monospace}
            body.light .asset-pill{background:#f8fafc;border-color:#e2e8f0}
            .asset-pill-name{font-size:11px;color:#64748b;margin-bottom:2px}
            .asset-pill-val{font-size:13px;color:#f1f5f9;font-weight:500}
            body.light .asset-pill-val{color:#0f172a}
            .asset-pill-low{opacity:0.4}
            @media(max-width:600px){.sidebar{display:none}.main{margin-left:0}.topbar{left:0}.db{grid-template-columns:1fr}.metric-big{font-size:18px}}
        </style>
    </head>
    <body>

    <div class="milestone-overlay" id="milestone-overlay" onclick="closeMilestone()">
        <div class="milestone-box" onclick="event.stopPropagation()">
            <div class="milestone-emoji">🎉</div>
            <div class="milestone-title" id="milestone-title">—</div>
            <div class="milestone-sub">Keep it up. Compounding is working.<br>Discipline &gt; Luck</div>
            <button class="milestone-close" onclick="closeMilestone()">Continue</button>
        </div>
    </div>

    <div class="layout">
        <nav class="sidebar">
            <!-- SIDEBAR LOGO -->
            <div class="sidebar-logo">
                <img src="/static/sgnticon.png" alt="SGNT" onclick="window.location.href='/'" style="cursor:pointer;width:32px">
            </div>
            <!-- SIDEBAR CONTENT -->
            <a href="/dashboard" class="nav-item active">Dashboard</a>
            <a href="/logs" class="nav-item">Logs</a>
            <a href="/history" class="nav-item">History</a>
            <a href="/metrics" class="nav-item">Metrics</a>
            <a href="/settings" class="nav-item">Settings</a>
        </nav>

        <div class="main">
            <!-- TOPBAR -->
            <div class="topbar">
                <!-- TOPBAR LEFT -->
                <div class="topbar-left">
                    <span class="dot" id="dot"></span>
                    <span class="status-text" id="status-text">Loading...</span>
                    <span class="tag tag-live" id="mode-tag">LIVE</span>
                </div>
                <!-- TOPBAR RIGHT -->
                <div class="topbar-right">
                    <button class="btn" onclick="loadData()">Update</button>
                    <button class="btn btn-danger" onclick="doLogout()">Logout</button>
                    <button class="theme-toggle" onclick="toggleTheme()" id="theme-btn">🌙</button>
                </div>
            </div>

            <!-- CONTENT -->
            <div class="content">
                <div class="db">

                    <div class="section-label">Account</div>

                    <div class="card">
                        <div class="card-title">Total Equity Balance</div>
                        <div class="metric-big" id="total-balance">—</div>
                        <div class="metric-label">USDC</div>
                    </div>
                    <div class="card">
                        <div class="card-title">Free USDC Balance</div>
                        <div class="metric-big" id="quote-balance">—</div>
                        <div class="metric-label">USDC</div>
                    </div>
                    <div class="card">
                        <div class="card-title">Margin Level</div>
                        <div class="metric-big" id="margin-level">—</div>
                        <div class="margin-bar-bg"><div class="margin-bar-fill" id="margin-bar" style="width:0%"></div></div>
                    </div>

                    <div class="card">
                        <div class="card-title">Debt</div>
                        <div class="metric-row"><span class="k">Total</span><span class="v" id="total-debt">—</span></div>
                        <div class="metric-row"><span class="k">Borrowed USDC</span><span class="v" id="quote-borrowed">—</span></div>
                    </div>
                    <div class="card">
                        <div class="card-title">Activity</div>
                        <div class="metric-row"><span class="k">Last Trade ID</span><span class="v" id="last-trade-id">—</span></div>
                        <div class="metric-row"><span class="k">Longs Today</span><span class="v" id="longs-today">—</span></div>
                        <div class="metric-row"><span class="k">Shorts Today</span><span class="v" id="shorts-today">—</span></div>
                        <div class="metric-row"><span class="k">Total Longs</span><span class="v" id="total-longs">—</span></div>
                        <div class="metric-row"><span class="k">Total Shorts</span><span class="v" id="total-shorts">—</span></div>
                    </div>
                    <div class="card">
                        <div class="card-title">Last Trade</div>
                        <div class="metric-row"><span class="k">Symbol</span><span class="v" id="lt-symbol">—</span></div>
                        <div class="metric-row"><span class="k">Side</span><span class="v" id="lt-side">—</span></div>
                        <div class="metric-row"><span class="k">Qty</span><span class="v" id="lt-qty">—</span></div>
                        <div class="metric-row"><span class="k">Spent</span><span class="v" id="lt-spent">—</span></div>
                        <div class="metric-row"><span class="k">Time</span><span class="v" id="lt-time" style="font-size:10px">—</span></div>
                    </div>

                    <div class="section-label">Assets with balance</div>

                    <div class="card col-full" style="grid-column:1/-1">
                        <div id="assets-grid" class="assets-grid">
                            <span style="font-size:12px;color:#475569;font-family:'Courier New',monospace">—</span>
                        </div>
                    </div>

                </div>
            </div>
        </div>
    </div>

    <div class="toast" id="toast"></div>

    <script>
        function toast(msg, ok=true) {
            const t = document.getElementById('toast');
            t.textContent = msg;
            t.style.borderColor = ok ? '#14532d' : '#7f1d1d';
            t.style.opacity = '1';
            setTimeout(() => t.style.opacity = '0', 2500);
        }

        function showMilestone(amount) {
            document.getElementById('milestone-title').textContent = amount.toLocaleString() + ' USDC reached';
            document.getElementById('milestone-overlay').classList.add('show');
        }

        function closeMilestone() {
            document.getElementById('milestone-overlay').classList.remove('show');
        }

        function toggleTheme() {
            const light = document.body.classList.toggle('light');
            document.getElementById('theme-btn').textContent = light ? '☀️' : '🌙';
            localStorage.setItem('sgnt-theme', light ? 'light' : 'dark');
        }

        if (localStorage.getItem('sgnt-theme') === 'light') {
            document.body.classList.add('light');
            document.getElementById('theme-btn').textContent = '☀️';
        }

        async function api(url) {
            try {
                const r = await fetch(url);
                if (r.status === 403) {
                    toast('Session expired', false);
                    setTimeout(() => window.location.href = '/login', 1500);
                    return null;
                }
                return await r.json();
            } catch(e) { toast('Network error', false); return null; }
        }

        async function loadData() {
            const d = await api('/snapshot');
            if (!d) return;

            const v = d.variables || {};
            const fmt = n => n != null ? parseFloat(n).toFixed(2) : '—';
            const fmtI = n => n != null ? parseInt(n) : '—';

            document.getElementById('total-balance').textContent = fmt(d.totalBalance) + ' $';
            document.getElementById('quote-balance').textContent = fmt(d.quoteBalance) + ' $';
            document.getElementById('total-debt').textContent = fmt(d.totalDebt) + ' $';
            document.getElementById('quote-borrowed').textContent = fmt(d.quoteBorrowed) + ' $';

            const ml = parseFloat(d.marginLevel) || 0;
            document.getElementById('margin-level').textContent = ml >= 999 ? '999 (no debt)' : ml.toFixed(2);
            const barPct = ml >= 999 ? 100 : Math.min(100, (ml / 5) * 100);
            document.getElementById('margin-bar').style.width = barPct + '%';
            document.getElementById('margin-bar').style.background = ml < 1.25 ? '#E24B4A' : ml < 2 ? '#BA7517' : '#1D9E75';

            document.getElementById('last-trade-id').textContent = '#' + (d.tradeId || '—');
            document.getElementById('longs-today').textContent = fmtI(d.longsToday);
            document.getElementById('shorts-today').textContent = fmtI(d.shortsToday);
            document.getElementById('total-longs').textContent = fmtI(d.totalLongs);
            document.getElementById('total-shorts').textContent = fmtI(d.totalShorts);

            const lt = d.lastTrade || {};
            document.getElementById('lt-symbol').textContent = lt.symbol || '—';
            document.getElementById('lt-side').textContent = lt.side || '—';
            document.getElementById('lt-side').style.color = lt.side === 'Long' ? '#4ade80' : lt.side === 'Short' ? '#f87171' : '#f1f5f9';
            document.getElementById('lt-qty').textContent = lt.executed_qty != null ? parseFloat(lt.executed_qty).toFixed(5) : '—';
            document.getElementById('lt-spent').textContent = lt.spent_qty != null ? parseFloat(lt.spent_qty).toFixed(2) + ' $' : '—';
            document.getElementById('lt-time').textContent = lt.time || '—';

            const live = !!v.trading;
            document.getElementById('dot').className = 'dot' + (live ? '' : ' red');
            document.getElementById('status-text').textContent = live ? 'Trading active' : 'Trading paused';
            document.getElementById('mode-tag').textContent = v.platform || 'LIVE';

            const milestones = d.milestonesReached || [];
            if (milestones.length > 0) showMilestone(milestones[milestones.length - 1]);

            const assets = d.assetsWithBalance || [];
            const THRESHOLD = 1;
            const above = assets.filter(a => a.balance >= THRESHOLD);
            const below = assets.filter(a => a.balance < THRESHOLD);
            const all = [...above, ...below];
            if (all.length === 0) {
                document.getElementById('assets-grid').innerHTML = '<span style="font-size:12px;color:#475569;font-family:\'Courier New\',monospace">No assets with balance</span>';
            } else {
                document.getElementById('assets-grid').innerHTML = all.map(a => `
                    <div class="asset-pill${a.balance < THRESHOLD ? ' asset-pill-low' : ''}">
                        <div class="asset-pill-name">${a.asset}</div>
                        <div class="asset-pill-val">${a.balance}</div>
                    </div>`).join('');
            }
        }

        async function doLogout() {
            try { await fetch('/logout'); } catch(e) {}
            window.location.href = '/login';
        }

        loadData();
        document.addEventListener('keydown', e => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'r') {
                e.preventDefault();
                loadData();
                toast('Updated data');
            }
        });
    </script>

    </body>
    </html>
    """
    return render_template_string(html)


@app.route("/logs")
def logs():
    if not is_admin_authenticated():
        return handle_unauthorized()

    filename = request.args.get("file")
    level = request.args.get("level")
    download_all = request.args.get("download")

    if download_all == "all":
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, "w") as zf:
            for f in os.listdir("."):
                if f.endswith(".log") and os.path.isfile(f):
                    zf.write(f)
        memory_file.seek(0)
        return send_file(memory_file, as_attachment=True, download_name="sgnt_logs.zip", mimetype="application/zip")

    if filename:
        if not filename.endswith(".log"):
            return {"error": "invalid file type"}, 400
        if not os.path.isfile(filename):
            return {"error": "file not found"}, 404
        display_name = filename
        if filename == "sgnt.log":
            date_str = datetime.utcnow().strftime("%Y-%m-%d")
            display_name = f"sgnt.{date_str}.log"
        if level:
            with open(filename, "r", encoding="utf-8") as f:
                filtered_lines = [line for line in f if level in line]
            download_name = f"{display_name}({level}).log"
            return Response(
                "".join(filtered_lines),
                mimetype="text/plain",
                headers={"Content-Disposition": f"attachment; filename={download_name}"}
            )
        return send_file(filename, as_attachment=True, download_name=display_name, mimetype="text/plain")

    log_files = []
    for f in os.listdir("."):
        if f.endswith(".log") and os.path.isfile(f):
            size_mb = os.path.getsize(f) / (1024 * 1024)
            modified = os.path.getmtime(f)
            display_name = f
            if f == "sgnt.log":
                date_str = datetime.utcnow().strftime("%Y-%m-%d")
                display_name = f"sgnt.{date_str}.log"
            log_files.append({
                "name": f,
                "display": display_name,
                "size": f"{size_mb:.2f} MB",
                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(modified))
            })

    log_files.sort(key=lambda x: x["modified"], reverse=True)

    latest_logs = []
    if log_files:
        latest_file = log_files[0]["name"]
        with open(latest_file, "r", encoding="utf-8") as f:
            latest_logs = list(deque(f, maxlen=250))

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta name="description" content="SGNT — Automated margin trading system.">
        <title>SGNT • Logs</title>
        <link rel="icon" type="image/png" href="/static/sgnticon.png">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet">
        <style>
            <!-- BOX STYLE -->
            *{box-sizing:border-box;margin:0;padding:0}
            body{background:#0f172a;color:#e2e8f0;font-family:'Inter',sans-serif;font-size:14px;transition:background 0.2s,color 0.2s}
            body.light{background:#f8fafc;color:#0f172a}

            <!-- SIDEBAR STYLE -->
            .layout{display:flex;min-height:100vh}
            .sidebar{width:160px;min-width:160px;background:#0a1120;border-right:0.5px solid #1e293b;display:flex;flex-direction:column;padding:16px 0;position:fixed;top:0;left:0;height:100vh;z-index:50}
            body.light .sidebar{background:#f1f5f9;border-right-color:#e2e8f0}
            .main{margin-left:160px;display:flex;flex-direction:column;flex:1;min-height:100vh}

            <!-- TOPBAR STYLE -->
            .topbar{position:fixed;top:0;left:160px;right:0;z-index:40;display:flex;justify-content:space-between;align-items:center;padding:0 20px;height:48px;background:#0f172a;border-bottom:0.5px solid #1e293b}
            body.light .topbar{background:#f8fafc;border-bottom-color:#e2e8f0}
            .content{padding:20px;margin-top:48px}
            .sidebar-logo{display:flex;align-items:center;justify-content:center;padding:0 16px 16px;border-bottom:0.5px solid #1e293b;margin-bottom:8px}
            body.light .sidebar-logo{border-bottom-color:#e2e8f0}
            .nav-item{display:flex;align-items:center;gap:8px;padding:8px 16px;font-size:11px;letter-spacing:0.06em;text-transform:uppercase;color:#475569;text-decoration:none;cursor:pointer;transition:0.15s;border-left:2px solid transparent;font-family:'Courier New',monospace}
            .nav-item:hover{color:#94a3b8;background:rgba(255,255,255,0.03)}
            body.light .nav-item:hover{background:rgba(0,0,0,0.04)}
            .nav-item.active{color:#f1f5f9;border-left-color:#1D9E75;background:rgba(29,158,117,0.08)}
            body.light .nav-item.active{color:#0f172a;border-left-color:#1D9E75;background:rgba(29,158,117,0.1)}
            body.light .nav-item{color:#64748b}

            <!-- TOPBAR LEFT & RIGHT STYLE -->
            .topbar-left{display:flex;align-items:center;gap:10px}
            .topbar-right{display:flex;align-items:center;gap:8px}
            .topbar-title{font-size:13px;font-weight:500;color:#f1f5f9}
            body.light .topbar-title{color:#0f172a}

            <!-- ACTIVE SIGNAL STYLE -->
            .dot{width:8px;height:8px;border-radius:50%;background:#1D9E75;display:inline-block}
            .dot.red{background:#E24B4A}
            .status-text{font-size:12px;color:#94a3b8;font-family:'Courier New',monospace}
            body.light .status-text{color:#64748b}

            <!-- TAG STYLE -->
            .tag{font-size:12px;padding:2px 8px;border-radius:6px;border:0.5px solid;font-family:'Courier New',monospace}
            .tag-live{border-color:#1D9E75;color:#1D9E75}

            <!-- THEME TOGGLE STYLE -->
            .theme-toggle{background:none;border:0.5px solid #334155;border-radius:20px;padding:4px 10px;cursor:pointer;font-size:13px;color:#94a3b8;transition:0.15s}
            body.light .theme-toggle{border-color:#cbd5e1;color:#64748b}
            .theme-toggle:hover{background:#1e293b}
            body.light .theme-toggle:hover{background:#e2e8f0}

            <!-- BUTTON STYLE -->
            .btn{padding:5px 12px;font-size:11px;border:0.5px solid #334155;border-radius:6px;background:#0f172a;color:#94a3b8;cursor:pointer;white-space:nowrap;transition:0.15s;text-decoration:none;display:inline-block}
            body.light .btn{background:#f8fafc;border-color:#cbd5e1;color:#64748b}
            .btn:hover{background:#1e293b;color:#f1f5f9}
            body.light .btn:hover{background:#e2e8f0;color:#0f172a}
            .btn-danger{border-color:#7f1d1d;color:#fca5a5}
            .btn-danger:hover{background:#450a0a}

            .refresh-toggle{display:flex;align-items:center;gap:6px;font-size:11px;color:#64748b;font-family:'Courier New',monospace}
            .toggle{position:relative;width:28px;height:15px;cursor:pointer}
            .toggle input{opacity:0;width:0;height:0}
            .slider{position:absolute;inset:0;background:#334155;border-radius:15px;transition:0.2s}
            .slider:before{content:'';position:absolute;width:10px;height:10px;left:2px;top:2px;background:#94a3b8;border-radius:50%;transition:0.2s}
            input:checked+.slider{background:#1D9E75}
            input:checked+.slider:before{transform:translateX(13px);background:white}
            .btn-group{display:flex;gap:4px;flex-wrap:wrap}
            .filter-btn{padding:3px 10px;font-size:10px;border:0.5px solid;border-radius:4px;cursor:pointer;background:transparent;font-family:'Courier New',monospace;letter-spacing:0.04em}
            .f-info{border-color:#16a34a;color:#4ade80}.f-info:hover{background:#052e16}
            .f-warning{border-color:#b45309;color:#fbbf24}.f-warning:hover{background:#1c0f00}
            .f-error{border-color:#991b1b;color:#fca5a5}.f-error:hover{background:#450a0a}
            .f-admin{border-color:#6d28d9;color:#c4b5fd}.f-admin:hover{background:#1e0a3c}
            .f-date{border-color:#0e7490;color:#67e8f9}.f-date:hover{background:#001f2b}
            .section-label{font-size:10px;letter-spacing:0.08em;color:#475569;text-transform:uppercase;margin:20px 0 8px;padding-left:2px}
            pre{background:#1e293b;border:0.5px solid #334155;border-radius:8px;padding:14px 16px;max-height:400px;overflow-y:auto;font-family:'Courier New',monospace;font-size:11px;line-height:1.7;color:#94a3b8;white-space:pre-wrap;word-break:break-all}
            body.light pre{background:#f1f5f9;border-color:#cbd5e1;color:#475569}
            .log-line{display:block;padding:1px 4px;border-radius:3px;margin:1px 0}
            .log-error{background:rgba(239,68,68,0.12);color:#fca5a5}
            .log-warning{color:#fbbf24}
            .log-admin{color:#c4b5fd}
            .log-date{color:#67e8f9}
            table{width:100%;border-collapse:collapse}
            thead tr{border-bottom:0.5px solid #334155}
            body.light thead tr{border-bottom-color:#e2e8f0}
            th{font-size:10px;letter-spacing:0.06em;color:#475569;text-transform:uppercase;padding:8px 12px;text-align:left;font-weight:400}
            td{padding:10px 12px;font-size:12px;border-bottom:0.5px solid #1e293b;vertical-align:middle}
            body.light td{border-bottom-color:#e2e8f0}
            tr:last-child td{border-bottom:none}
            tr:hover td{background:#1e293b}
            body.light tr:hover td{background:#f1f5f9}
            .td-name{color:#f1f5f9;font-family:'Courier New',monospace}
            body.light .td-name{color:#0f172a}
            .td-muted{color:#64748b}
            @media(max-width:600px){.sidebar{display:none}.main{margin-left:0}.topbar{left:0}}
        </style>
    </head>
    <body>

    <div class="layout">
        <nav class="sidebar">
            <!-- SIDEBAR LOGO -->
            <div class="sidebar-logo">
                <img src="/static/sgnticon.png" alt="SGNT" onclick="window.location.href='/'" style="cursor:pointer;width:32px">
            </div>
            <!-- SIDEBAR CONTENT -->
            <a href="/dashboard" class="nav-item">Dashboard</a>
            <a href="/logs" class="nav-item active">Logs</a>
            <a href="/history" class="nav-item">History</a>
            <a href="/metrics" class="nav-item">Metrics</a>
            <a href="/settings" class="nav-item">Settings</a>
        </nav>

        <div class="main">
            <!-- TOPBAR -->
            <div class="topbar">
                <!-- TOPBAR LEFT -->
                <div class="topbar-left">
                    <span class="dot" id="dot"></span>
                    <span class="status-text" id="status-text">Loading...</span>
                    <span class="tag tag-live" id="mode-tag">LIVE</span>
                </div>
                <!-- TOPBAR RIGHT -->
                <div class="topbar-right">
                    <div class="refresh-toggle">
                        <label class="toggle">
                            <input type="checkbox" id="auto-refresh" onchange="toggleRefresh(this.checked)">
                            <span class="slider"></span>
                        </label>
                        <span>Auto-refresh</span>
                    </div>
                    <a href="/logs?download=all" class="btn btn-blue">Download all</a>
                    <button class="btn btn-danger" onclick="doLogout()">Logout</button>
                    <button class="theme-toggle" onclick="toggleTheme()" id="theme-btn">🌙</button>
                </div>
            </div>

            <!-- CONTENT -->
            <div class="content">
                <div class="section-label">Live preview — last 250 lines</div>
                <pre id="log-preview">{% for line in preview %}<span class="{{ line | log_class }}">{{ line }}</span>{% endfor %}</pre>

                <div class="section-label">Log files</div>
                <table>
                    <thead>
                        <tr>
                            <th>File</th>
                            <th>Size</th>
                            <th>Modified</th>
                            <th>Download</th>
                            <th>Filter</th>
                        </tr>
                    </thead>
                    <tbody>
                    {% for log in logs %}
                    <tr>
                        <td class="td-name">{{ log.display }}</td>
                        <td class="td-muted">{{ log.size }}</td>
                        <td class="td-muted">{{ log.modified }}</td>
                        <td><a href="/logs?file={{ log.name }}" class="btn">Download</a></td>
                        <td>
                            <div class="btn-group">
                                <a href="/logs?file={{ log.name }}&level=INFO"><button class="filter-btn f-info">INFO</button></a>
                                <a href="/logs?file={{ log.name }}&level=WARNING"><button class="filter-btn f-warning">WARN</button></a>
                                <a href="/logs?file={{ log.name }}&level=ERROR"><button class="filter-btn f-error">ERROR</button></a>
                                <a href="/logs?file={{ log.name }}&level=ADMIN"><button class="filter-btn f-admin">ADMIN</button></a>
                                <a href="/logs?file={{ log.name }}&level=DATE"><button class="filter-btn f-date">DATE</button></a>
                            </div>
                        </td>
                    </tr>
                    {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        let refreshInterval = null;

        function toggleRefresh(enabled) {
            if (enabled) refreshInterval = setInterval(fetchLogs, 10000);
            else { clearInterval(refreshInterval); refreshInterval = null; }
        }

        async function fetchLogs() {
            try {
                const r = await fetch('/logs_preview');
                const d = await r.json();
                const pre = document.getElementById('log-preview');
                pre.innerHTML = d.lines.map(line => `<span class="${logClass(line)}">${escHtml(line)}</span>`).join('');
                pre.scrollTop = pre.scrollHeight;
            } catch(e) {}
        }

        function logClass(line) {
            if (line.includes('| ERROR |')) return 'log-line log-error';
            if (line.includes('| WARNING |')) return 'log-line log-warning';
            if (line.includes('| ADMIN |')) return 'log-line log-admin';
            if (line.includes('| DATE |')) return 'log-line log-date';
            return 'log-line';
        }

        function escHtml(str) {
            return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        }

        function toggleTheme() {
            const light = document.body.classList.toggle('light');
            document.getElementById('theme-btn').textContent = light ? '☀️' : '🌙';
            localStorage.setItem('sgnt-theme', light ? 'light' : 'dark');
        }

        if (localStorage.getItem('sgnt-theme') === 'light') {
            document.body.classList.add('light');
            document.getElementById('theme-btn').textContent = '☀️';
        }

        async function doLogout() {
            try { await fetch('/logout'); } catch(e) {}
            window.location.href = '/login';
        }

    </script>

    </body>
    </html>
    """
    return render_template_string(html, logs=log_files, preview=latest_logs)


@app.route("/history", methods=["GET"])
def history():
    if not is_admin_authenticated():
        return handle_unauthorized()

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta name="description" content="SGNT — Automated margin trading system.">
        <title>SGNT • History</title>
        <link rel="icon" type="image/png" href="/static/sgnticon.png">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet">
        <style>

            <!-- BOX STYLE -->
            *{box-sizing:border-box;margin:0;padding:0}
            body{background:#0f172a;color:#e2e8f0;font-family:'Inter',sans-serif;font-size:14px;transition:background 0.2s,color 0.2s}
            body.light{background:#f8fafc;color:#0f172a}

            <!-- SIDEBAR STYLE -->
            .layout{display:flex;min-height:100vh}
            .sidebar{width:160px;min-width:160px;background:#0a1120;border-right:0.5px solid #1e293b;display:flex;flex-direction:column;padding:16px 0;position:fixed;top:0;left:0;height:100vh;z-index:50}
            body.light .sidebar{background:#f1f5f9;border-right-color:#e2e8f0}
            .main{margin-left:160px;display:flex;flex-direction:column;flex:1;min-height:100vh}

            <!-- TOPBAR STYLE -->
            .topbar{position:fixed;top:0;left:160px;right:0;z-index:40;display:flex;justify-content:space-between;align-items:center;padding:0 20px;height:48px;background:#0f172a;border-bottom:0.5px solid #1e293b}
            body.light .topbar{background:#f8fafc;border-bottom-color:#e2e8f0}
            .content{padding:20px;margin-top:48px}
            .sidebar-logo{display:flex;align-items:center;justify-content:center;padding:0 16px 16px;border-bottom:0.5px solid #1e293b;margin-bottom:8px}
            body.light .sidebar-logo{border-bottom-color:#e2e8f0}
            .nav-item{display:flex;align-items:center;gap:8px;padding:8px 16px;font-size:11px;letter-spacing:0.06em;text-transform:uppercase;color:#475569;text-decoration:none;cursor:pointer;transition:0.15s;border-left:2px solid transparent;font-family:'Courier New',monospace}
            .nav-item:hover{color:#94a3b8;background:rgba(255,255,255,0.03)}
            body.light .nav-item:hover{background:rgba(0,0,0,0.04)}
            .nav-item.active{color:#f1f5f9;border-left-color:#1D9E75;background:rgba(29,158,117,0.08)}
            body.light .nav-item.active{color:#0f172a;border-left-color:#1D9E75;background:rgba(29,158,117,0.1)}
            body.light .nav-item{color:#64748b}

            <!-- TOPBAR LEFT & RIGHT STYLE -->
            .topbar-left{display:flex;align-items:center;gap:10px}
            .topbar-right{display:flex;align-items:center;gap:8px}
            .topbar-title{font-size:13px;font-weight:500;color:#f1f5f9}
            body.light .topbar-title{color:#0f172a}

            <!-- ACTIVE SIGNAL STYLE -->
            .dot{width:8px;height:8px;border-radius:50%;background:#1D9E75;display:inline-block}
            .dot.red{background:#E24B4A}
            .status-text{font-size:12px;color:#94a3b8;font-family:'Courier New',monospace}
            body.light .status-text{color:#64748b}

            <!-- TAG STYLE -->
            .tag{font-size:12px;padding:2px 8px;border-radius:6px;border:0.5px solid;font-family:'Courier New',monospace}
            .tag-live{border-color:#1D9E75;color:#1D9E75}

            <!-- THEME TOGGLE STYLE -->
            .theme-toggle{background:none;border:0.5px solid #334155;border-radius:20px;padding:4px 10px;cursor:pointer;font-size:13px;color:#94a3b8;transition:0.15s}
            body.light .theme-toggle{border-color:#cbd5e1;color:#64748b}
            .theme-toggle:hover{background:#1e293b}
            body.light .theme-toggle:hover{background:#e2e8f0}

            <!-- BUTTON STYLE -->
            .btn{padding:5px 12px;font-size:11px;border:0.5px solid #334155;border-radius:6px;background:#0f172a;color:#94a3b8;cursor:pointer;white-space:nowrap;transition:0.15s;text-decoration:none;display:inline-block}
            body.light .btn{background:#f8fafc;border-color:#cbd5e1;color:#64748b}
            .btn:hover{background:#1e293b;color:#f1f5f9}
            body.light .btn:hover{background:#e2e8f0;color:#0f172a}
            .btn-danger{border-color:#7f1d1d;color:#fca5a5}
            .btn-danger:hover{background:#450a0a}

            <!-- OTHERS STYLE -->
            .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px}
            .stat{background:#1e293b;border:0.5px solid #334155;border-radius:8px;padding:10px 14px}
            body.light .stat{background:#ffffff;border-color:#e2e8f0}
            .stat-val{font-size:18px;font-weight:500;color:#f1f5f9;font-family:'Courier New',monospace}
            body.light .stat-val{color:#0f172a}
            .stat-label{font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:0.06em;margin-top:2px}
            .filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;align-items:center}
            .filter-label{font-size:11px;color:#475569;font-family:'Courier New',monospace;letter-spacing:0.04em}
            .filters select,.filters input[type=text]{padding:5px 10px;font-size:11px;background:#1e293b;border:0.5px solid #334155;border-radius:6px;color:#94a3b8;font-family:'Courier New',monospace;outline:none;transition:0.15s}
            body.light .filters select,body.light .filters input[type=text]{background:#ffffff;border-color:#cbd5e1;color:#64748b}
            .filters select:focus,.filters input[type=text]:focus{border-color:#64748b;color:#f1f5f9}
            .table-wrap{background:#1e293b;border:0.5px solid #334155;border-radius:8px;overflow:hidden}
            body.light .table-wrap{background:#ffffff;border-color:#e2e8f0}
            table{width:100%;border-collapse:collapse}
            thead tr{border-bottom:0.5px solid #334155}
            body.light thead tr{border-bottom-color:#e2e8f0}
            th{font-size:10px;letter-spacing:0.06em;color:#475569;text-transform:uppercase;padding:10px 14px;text-align:left;font-weight:400;cursor:pointer;user-select:none;white-space:nowrap}
            th:hover{color:#94a3b8}
            th.active{color:#f1f5f9}
            body.light th.active{color:#0f172a}
            td{padding:10px 14px;font-size:12px;border-bottom:0.5px solid #1e293b;vertical-align:middle;font-family:'Courier New',monospace}
            body.light td{border-bottom-color:#e2e8f0}
            tr:last-child td{border-bottom:none}
            tr:hover td{background:rgba(255,255,255,0.02)}
            body.light tr:hover td{background:#f8fafc}
            .badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:500;letter-spacing:0.04em}
            .badge-long{background:rgba(29,158,117,0.15);color:#4ade80;border:0.5px solid rgba(29,158,117,0.3)}
            .badge-short{background:rgba(226,75,74,0.15);color:#f87171;border:0.5px solid rgba(226,75,74,0.3)}
            .empty{text-align:center;padding:40px;color:#475569;font-size:13px;font-family:'Courier New',monospace}
            .pagination{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-top:0.5px solid #334155;font-size:11px;color:#475569;font-family:'Courier New',monospace}
            body.light .pagination{border-top-color:#e2e8f0}
            .pg-btns{display:flex;gap:6px}
            @media(max-width:600px){.sidebar{display:none}.main{margin-left:0}.topbar{left:0}.stats{grid-template-columns:1fr 1fr}.filters{flex-direction:column;align-items:flex-start}}
        </style>
    </head>
    <body>

    <div class="layout">
        <nav class="sidebar">
            <!-- SIDEBAR LOGO -->
            <div class="sidebar-logo">
                <img src="/static/sgnticon.png" alt="SGNT" onclick="window.location.href='/'" style="cursor:pointer;width:32px">
            </div>
            <!-- SIDEBAR CONTENT -->
            <a href="/dashboard" class="nav-item">Dashboard</a>
            <a href="/logs" class="nav-item">Logs</a>
            <a href="/history" class="nav-item active">History</a>
            <a href="/metrics" class="nav-item">Metrics</a>
            <a href="/settings" class="nav-item">Settings</a>
        </nav>

        <div class="main">
            <!-- TOPBAR -->
            <div class="topbar">
                <!-- TOPBAR LEFT -->
                <div class="topbar-left">
                    <span class="dot" id="dot"></span>
                    <span class="status-text" id="status-text">Loading...</span>
                    <span class="tag tag-live" id="mode-tag">LIVE</span>
                </div>
                <!-- TOPBAR RIGHT -->
                <div class="topbar-right">
                    <span id="count-badge" style="font-size:11px;color:#475569;font-family:'Courier New',monospace"></span>
                    <button class="btn" id="export-btn">Export CSV</button>
                    <button class="btn btn-danger" onclick="doLogout()">Logout</button>
                    <button class="theme-toggle" onclick="toggleTheme()" id="theme-btn">🌙</button>
                </div>
            </div>

            <!-- CONTENT -->
            <div class="content">
                <div class="stats">
                    <div class="stat"><div class="stat-val" id="s-total">—</div><div class="stat-label">Total trades</div></div>
                    <div class="stat"><div class="stat-val" id="s-longs" style="color:#4ade80">—</div><div class="stat-label">Longs</div></div>
                    <div class="stat"><div class="stat-val" id="s-shorts" style="color:#f87171">—</div><div class="stat-label">Shorts</div></div>
                    <div class="stat"><div class="stat-val" id="s-volume">—</div><div class="stat-label">Volume USDC</div></div>
                </div>

                <div class="filters">
                    <span class="filter-label">Filter</span>
                    <input type="text" id="f-symbol" placeholder="Symbol" style="width:110px">
                    <select id="f-side">
                        <option value="">All sides</option>
                        <option value="Long">Long</option>
                        <option value="Short">Short</option>
                    </select>
                    <select id="f-strategy">
                        <option value="">All strategies</option>
                    </select>
                    <span class="filter-label" style="margin-left:8px">Sort</span>
                    <select id="f-sort">
                        <option value="tradeId-desc">ID ↓</option>
                        <option value="tradeId-asc">ID ↑</option>
                        <option value="time-desc">Time ↓</option>
                        <option value="time-asc">Time ↑</option>
                        <option value="spent_qty-desc">Volume ↓</option>
                        <option value="spent_qty-asc">Volume ↑</option>
                        <option value="executed_qty-desc">Qty ↓</option>
                        <option value="executed_qty-asc">Qty ↑</option>
                    </select>
                    <button class="btn" id="clear-btn" style="margin-left:4px">Clear</button>
                </div>

                <div class="table-wrap">
                    <table>
                        <thead>
                            <tr>
                                <th data-col="tradeId">ID <span class="sort-arrow">↕</span></th>
                                <th data-col="time">Time <span class="sort-arrow">↕</span></th>
                                <th data-col="symbol">Symbol <span class="sort-arrow">↕</span></th>
                                <th data-col="side">Side <span class="sort-arrow">↕</span></th>
                                <th data-col="executed_qty">Qty <span class="sort-arrow">↕</span></th>
                                <th data-col="spent_qty">Volume <span class="sort-arrow">↕</span></th>
                                <th data-col="strategy">Strategy <span class="sort-arrow">↕</span></th>
                            </tr>
                        </thead>
                        <tbody id="tbody"></tbody>
                    </table>
                    <div class="pagination">
                        <span id="pg-info"></span>
                        <div class="pg-btns">
                            <button class="btn" id="pg-prev">← Prev</button>
                            <button class="btn" id="pg-next">Next →</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        const RAW = {{ trade_history | tojson }};
        const PAGE_SIZE = 25;
        let page = 1;
        let filtered = [...RAW];

        function toggleTheme() {
            const light = document.body.classList.toggle('light');
            document.getElementById('theme-btn').textContent = light ? '☀️' : '🌙';
            localStorage.setItem('sgnt-theme', light ? 'light' : 'dark');
        }

        if (localStorage.getItem('sgnt-theme') === 'light') {
            document.body.classList.add('light');
            document.getElementById('theme-btn').textContent = '☀️';
        }

        const strats = [...new Set(RAW.map(t => t.strategy).filter(Boolean))];
        const sel = document.getElementById('f-strategy');
        strats.forEach(s => { const o = document.createElement('option'); o.value = s; o.textContent = s; sel.appendChild(o); });

        function applyFilters() {
            const sym = document.getElementById('f-symbol').value.toUpperCase().trim();
            const side = document.getElementById('f-side').value;
            const strat = document.getElementById('f-strategy').value;
            const [col, dir] = document.getElementById('f-sort').value.split('-');
            let data = [...RAW];
            if (sym) data = data.filter(t => t.symbol && t.symbol.includes(sym));
            if (side) data = data.filter(t => t.side === side);
            if (strat) data = data.filter(t => t.strategy === strat);
            data.sort((a, b) => {
                let va = a[col], vb = b[col];
                if (!isNaN(+va) && !isNaN(+vb)) { va = +va; vb = +vb; }
                else { va = String(va || ''); vb = String(vb || ''); }
                return dir === 'asc' ? (va > vb ? 1 : va < vb ? -1 : 0) : (va < vb ? 1 : va > vb ? -1 : 0);
            });
            filtered = data;
            page = 1;
            render();
        }

        function render() {
            const total = filtered.length;
            const start = (page - 1) * PAGE_SIZE;
            const slice = filtered.slice(start, start + PAGE_SIZE);
            const tbody = document.getElementById('tbody');
            if (slice.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7"><div class="empty">No trades found</div></td></tr>';
            } else {
                tbody.innerHTML = slice.map(t => `<tr>
                    <td style="color:#64748b">#${t.tradeId || '—'}</td>
                    <td style="color:#64748b;font-size:11px">${t.time || '—'}</td>
                    <td style="color:#f1f5f9">${t.symbol || '—'}</td>
                    <td><span class="badge badge-${(t.side || '').toLowerCase()}">${t.side || '—'}</span></td>
                    <td>${t.executed_qty != null ? parseFloat(t.executed_qty).toFixed(5) : '—'}</td>
                    <td style="color:#94a3b8">${t.spent_qty != null ? parseFloat(t.spent_qty).toFixed(2) + ' $' : '—'}</td>
                    <td style="color:#64748b">${t.strategy || '—'}</td>
                </tr>`).join('');
            }
            document.getElementById('pg-info').textContent = total === 0 ? '0 results' : `${start + 1}–${Math.min(start + PAGE_SIZE, total)} of ${total}`;
            document.getElementById('pg-prev').style.opacity = page === 1 ? '0.4' : '1';
            document.getElementById('pg-next').style.opacity = page >= Math.ceil(total / PAGE_SIZE) ? '0.4' : '1';
            document.getElementById('count-badge').textContent = `${RAW.length} trades`;
            const longs = RAW.filter(t => t.side === 'Long').length;
            const shorts = RAW.filter(t => t.side === 'Short').length;
            const vol = RAW.reduce((s, t) => s + (parseFloat(t.spent_qty) || 0), 0);
            document.getElementById('s-total').textContent = RAW.length;
            document.getElementById('s-longs').textContent = longs;
            document.getElementById('s-shorts').textContent = shorts;
            document.getElementById('s-volume').textContent = vol.toFixed(2);
            const [activeCol] = document.getElementById('f-sort').value.split('-');
            document.querySelectorAll('th[data-col]').forEach(th => th.classList.toggle('active', th.dataset.col === activeCol));
        }

        ['f-symbol', 'f-side', 'f-strategy', 'f-sort'].forEach(id => {
            document.getElementById(id).addEventListener('input', applyFilters);
            document.getElementById(id).addEventListener('change', applyFilters);
        });

        document.getElementById('clear-btn').addEventListener('click', () => {
            document.getElementById('f-symbol').value = '';
            document.getElementById('f-side').value = '';
            document.getElementById('f-strategy').value = '';
            document.getElementById('f-sort').value = 'tradeId-desc';
            applyFilters();
        });

        document.getElementById('pg-prev').addEventListener('click', () => { if (page > 1) { page--; render(); } });
        document.getElementById('pg-next').addEventListener('click', () => { if (page < Math.ceil(filtered.length / PAGE_SIZE)) { page++; render(); } });

        document.querySelectorAll('th[data-col]').forEach(th => {
            th.addEventListener('click', () => {
                const col = th.dataset.col;
                const sel = document.getElementById('f-sort');
                const [cur, dir] = sel.value.split('-');
                sel.value = col === cur && dir === 'desc' ? col + '-asc' : col + '-desc';
                applyFilters();
            });
        });

        document.getElementById('export-btn').addEventListener('click', () => {
            const headers = ['ID', 'Time', 'Symbol', 'Side', 'Qty', 'Volume', 'Strategy'];
            const rows = filtered.map(t => [t.tradeId, t.time, t.symbol, t.side, t.executed_qty, t.spent_qty, t.strategy]);
            const csv = [headers, ...rows].map(r => r.join(',')).join('\\n');
            const a = document.createElement('a');
            a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
            a.download = 'sgnt_history.csv';
            a.click();
        });

        applyFilters();

    async function doLogout() {
        try { await fetch('/logout'); } catch(e) {}
        window.location.href = '/login';
    }

    </script>

    </body>
    </html>
    """
    return render_template_string(html, trade_history=TRADE_HISTORY)


@app.route("/metrics")
def metrics():
    if not is_admin_authenticated():
        return handle_unauthorized()

    sidebar_html = """
    <div class="layout">
        <nav class="sidebar">
            <!-- SIDEBAR LOGO -->
            <div class="sidebar-logo">
                <img src="/static/sgnticon.png" alt="SGNT" onclick="window.location.href='/'" style="cursor:pointer;width:32px">
            </div>
            <!-- SIDEBAR CONTENT -->
            <a href="/dashboard" class="nav-item">Dashboard</a>
            <a href="/logs" class="nav-item">Logs</a>
            <a href="/history" class="nav-item">History</a>
            <a href="/metrics" class="nav-item active">Metrics</a>
            <a href="/settings" class="nav-item">Settings</a>
        </nav>

        <div class="main">
            <!-- TOPBAR -->
            <div class="topbar">
                <!-- TOPBAR LEFT -->
                <div class="topbar-left">
                    <span class="dot" id="dot"></span>
                    <span class="status-text" id="status-text">Loading...</span>
                    <span class="tag tag-live" id="mode-tag">LIVE</span>
                </div>
                <!-- TOPBAR RIGHT -->
                <div class="topbar-right">
                    <span class="snapshot-count">{count} snapshots</span>
                    <button class="btn btn-danger" onclick="doLogout()">Logout</button>
                    <button class="theme-toggle" onclick="toggleTheme()" id="theme-btn">🌙</button>
                </div>
            </div>

            <!-- CONTENT -->
            <div class="content">
    """.format(count=len(SNAPSHOT_HISTORY))

    shared_css = """
        *{box-sizing:border-box;margin:0;padding:0}
        body{background:#0f172a;color:#e2e8f0;font-family:'Inter',sans-serif;font-size:14px;transition:background 0.2s,color 0.2s}
        body.light{background:#f8fafc;color:#0f172a}
        .layout{display:flex;min-height:100vh}
        .sidebar{width:160px;min-width:160px;background:#0a1120;border-right:0.5px solid #1e293b;display:flex;flex-direction:column;padding:16px 0;position:fixed;top:0;left:0;height:100vh;z-index:50}
        body.light .sidebar{background:#f1f5f9;border-right-color:#e2e8f0}
        .main{margin-left:160px;display:flex;flex-direction:column;flex:1;min-height:100vh}
        .topbar{position:fixed;top:0;left:160px;right:0;z-index:40;display:flex;justify-content:space-between;align-items:center;padding:0 20px;height:48px;background:#0f172a;border-bottom:0.5px solid #1e293b}
        body.light .topbar{background:#f8fafc;border-bottom-color:#e2e8f0}
        .content{padding:20px;margin-top:48px}
        .sidebar-logo{display:flex;align-items:center;justify-content:center;padding:0 16px 16px;border-bottom:0.5px solid #1e293b;margin-bottom:8px}
        body.light .sidebar-logo{border-bottom-color:#e2e8f0}
        .nav-item{display:flex;align-items:center;gap:8px;padding:8px 16px;font-size:11px;letter-spacing:0.06em;text-transform:uppercase;color:#475569;text-decoration:none;cursor:pointer;transition:0.15s;border-left:2px solid transparent;font-family:'Courier New',monospace}
        .nav-item:hover{color:#94a3b8;background:rgba(255,255,255,0.03)}
        body.light .nav-item:hover{background:rgba(0,0,0,0.04)}
        .nav-item.active{color:#f1f5f9;border-left-color:#1D9E75;background:rgba(29,158,117,0.08)}
        body.light .nav-item.active{color:#0f172a;border-left-color:#1D9E75;background:rgba(29,158,117,0.1)}
        body.light .nav-item{color:#64748b}
        .topbar-left{display:flex;align-items:center;gap:10px}
        .topbar-right{display:flex;align-items:center;gap:8px}
        .topbar-title{font-size:13px;font-weight:500;color:#f1f5f9}
        .dot{width:8px;height:8px;border-radius:50%;background:#1D9E75;display:inline-block}
        .dot.red{background:#E24B4A}
        .status-text{font-size:12px;color:#94a3b8;font-family:'Courier New',monospace}
        body.light .status-text{color:#64748b}
        .tag{font-size:12px;padding:2px 8px;border-radius:6px;border:0.5px solid;font-family:'Courier New',monospace}
        .tag-live{border-color:#1D9E75;color:#1D9E75}
        body.light .topbar-title{color:#0f172a}
        .btn{padding:5px 12px;font-size:11px;border:0.5px solid #334155;border-radius:6px;background:#0f172a;color:#94a3b8;cursor:pointer;white-space:nowrap;transition:0.15s}
        body.light .btn{background:#f8fafc;border-color:#cbd5e1;color:#64748b}
        .btn:hover{background:#1e293b;color:#f1f5f9}
        body.light .btn:hover{background:#e2e8f0;color:#0f172a}
        .btn-danger{border-color:#7f1d1d;color:#fca5a5}
        .btn-danger:hover{background:#450a0a}
        .theme-toggle{background:none;border:0.5px solid #334155;border-radius:20px;padding:4px 10px;cursor:pointer;font-size:13px;color:#94a3b8;transition:0.15s}
        body.light .theme-toggle{border-color:#cbd5e1;color:#64748b}
        .theme-toggle:hover{background:#1e293b}
        body.light .theme-toggle:hover{background:#e2e8f0}
        .snapshot-count{font-size:11px;color:#475569;font-family:'Courier New',monospace}
        @media(max-width:600px){.sidebar{display:none}.main{margin-left:0}.topbar{left:0}}
    """

    if not SNAPSHOT_HISTORY:
        return f"""<!DOCTYPE html>
        <html>
        <head>
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>SGNT • Metrics</title>
            <link rel="icon" type="image/png" href="/static/sgnticon.png">
            <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet">
            <style>{shared_css}
                .empty-state{{display:flex;flex-direction:column;align-items:center;justify-content:center;height:calc(100vh - 48px);color:#475569}}
                .empty-state img{{width:80px;opacity:0.2;margin-bottom:16px}}
                .empty-state p{{font-family:'Courier New',monospace;font-size:13px;letter-spacing:0.06em}}
            </style>
        </head>
        <body>
        {sidebar_html}
            <div class="empty-state">
                <img src="/static/sgntlogo.png" alt="SGNT">
                <p>No snapshot data yet.</p>
            </div>
            </div>
        </div>
        </div>
        <script>
            function toggleTheme(){{const light=document.body.classList.toggle('light');document.getElementById('theme-btn').textContent=light?'☀️':'🌙';localStorage.setItem('sgnt-theme',light?'light':'dark');}}
            if(localStorage.getItem('sgnt-theme')==='light'){{document.body.classList.add('light');document.getElementById('theme-btn').textContent='☀️';}}
            async function doLogout(){{try{{await fetch('/logout');}}catch(e){{}}window.location.href='/login';}}
        </script>
        </body></html>"""

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta name="description" content="SGNT — Automated margin trading system.">
        <title>SGNT • Metrics</title>
        <link rel="icon" type="image/png" href="/static/sgnticon.png">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet">
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
        {shared_css}
        .chart-card{{background:#1e293b;border:0.5px solid #334155;border-radius:8px;padding:16px;margin-bottom:12px}}
        body.light .chart-card{{background:#ffffff;border-color:#e2e8f0}}
        .chart-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}}
        .chart-title{{font-size:11px;letter-spacing:0.06em;color:#64748b;text-transform:uppercase}}
        .dl-btn{{padding:3px 10px;font-size:10px;border:0.5px solid #334155;border-radius:4px;background:transparent;color:#64748b;cursor:pointer}}
        body.light .dl-btn{{border-color:#cbd5e1}}
        .dl-btn:hover{{background:#0f172a;color:#94a3b8}}
        body.light .dl-btn:hover{{background:#e2e8f0;color:#0f172a}}
        .btn-danger{{border-color:#7f1d1d;color:#fca5a5}}
        .btn-danger:hover{{background:#450a0a}}
        </style>
    </head>
    <body>

    {sidebar_html}

        <!-- BALANCE CHART CARD -->
        <div class="chart-card">
            <div class="chart-header">
                <span class="chart-title">Balance</span>
                <button class="dl-btn" onclick="downloadChart('balanceChart')">Download</button>
            </div>
            <canvas id="balanceChart"></canvas>
        </div>

        <!-- MARGIN LEVEL CHART CARD -->
        <div class="chart-card">
            <div class="chart-header">
                <span class="chart-title">Margin level</span>
                <button class="dl-btn" onclick="downloadChart('marginChart')">Download</button>
            </div>
            <canvas id="marginChart"></canvas>
        </div>

        <!-- ACTIVITY CHART CARD -->
        <div class="chart-card">
            <div class="chart-header">
                <span class="chart-title">Activity</span>
                <button class="dl-btn" onclick="downloadChart('activityChart')">Download</button>
            </div>
            <canvas id="activityChart"></canvas>
        </div>

    </div></div></div>

    <script>
    const data = {json.dumps(SNAPSHOT_HISTORY)};
    const labels = data.map(d => d.time);

    const isLight = localStorage.getItem('sgnt-theme') === 'light';
    if (isLight) {{
        document.body.classList.add('light');
        document.getElementById('theme-btn').textContent = '☀️';
    }}

    function gridColor() {{
        return document.body.classList.contains('light') ? '#e2e8f0' : '#1e293b';
    }}

    Chart.defaults.color = isLight ? '#475569' : '#64748b';
    Chart.defaults.borderColor = gridColor();

    function dataset(label, key, color) {{
        return {{ label, data: data.map(d => d[key]), borderColor: color, backgroundColor: color + '18', tension: 0.3, pointRadius: 2, pointHoverRadius: 4, fill: false }};
    }}

    function downloadChart(chartId) {{
        const canvas = document.getElementById(chartId);
        const link = document.createElement('a');
        link.download = chartId + '.png';
        link.href = canvas.toDataURL('image/png');
        link.click();
    }}

    function toggleTheme() {{
        const light = document.body.classList.toggle('light');
        document.getElementById('theme-btn').textContent = light ? '☀️' : '🌙';
        localStorage.setItem('sgnt-theme', light ? 'light' : 'dark');
        location.reload();
    }}

    const commonOptions = {{
        responsive: true,
        plugins: {{legend: {{labels: {{font: {{size: 11}}, boxWidth: 12}}}}}},
        scales: {{
            x: {{ticks: {{font: {{size: 10}}, maxTicksLimit: 8}}, grid: {{color: gridColor()}}}},
            y: {{ticks: {{font: {{size: 10}}}}, grid: {{color: gridColor()}}}}
        }}
    }};

    new Chart(document.getElementById('balanceChart'), {{
        type: 'line',
        data: {{labels, datasets: [
            dataset("Total Balance", "totalBalance", "#22d3ee"),
            dataset("USDC Balance", "quoteBalance", "#4ade80"),
            dataset("Debt", "totalDebt", "#f87171"),
            dataset("Borrowed", "quoteBorrowed", "#fb923c")
        ]}},
        options: commonOptions
    }});

    const transform = (v) => Math.log10(v - 1);
    const inverse = (v) => Math.pow(10, v) + 1;

    const backgroundZonesPlugin = {{
        id: 'backgroundZones',
        beforeDraw: (chart) => {{
            const {{ ctx, chartArea, scales: {{ y }} }} = chart;
            if (!chartArea) return;
            const drawZone = (yMin, yMax, color) => {{
                const yTop = y.getPixelForValue(transform(yMax));
                const yBottom = y.getPixelForValue(transform(yMin));
                ctx.save();
                ctx.fillStyle = color;
                ctx.fillRect(chartArea.left, yTop, chartArea.right - chartArea.left, yBottom - yTop);
                ctx.restore();
            }};
            drawZone(1.25, 2, 'rgba(255, 255, 0, 0.2)');
            drawZone(1.16, 1.25, 'rgba(255, 165, 0, 0.25)');
            drawZone(1.1, 1.16, 'rgba(255, 0, 0, 0.25)');
        }}
    }};

    const marginData = data.map((d, i) => {{
        const v = d.marginLevel;
        if (i > 0) {{ const prev = data[i-1].marginLevel; if (Math.abs(v - prev) > 100) return null; }}
        return transform(v);
    }});

    new Chart(document.getElementById('marginChart'), {{
        type: 'line',
        data: {{ labels, datasets: [{{ label: "Margin Level", data: marginData, borderColor: "#a78bfa", backgroundColor: "#a78bfa18", tension: 0.3, pointRadius: 2, pointHoverRadius: 4, fill: false, spanGaps: false }}] }},
        options: {{
            ...commonOptions,
            scales: {{
                x: commonOptions.scales.x,
                y: {{ type: 'linear', min: transform(1.1), max: transform(1000), ticks: {{ font: {{ size: 10 }}, callback: (v) => inverse(v).toFixed(2) }}, grid: {{ color: gridColor() }} }}
            }}
        }},
        plugins: [backgroundZonesPlugin]
    }});

    const activityDatasets = [];
    activityDatasets.push(
        {{type:'bar', label:'Longs Today', data: data.map(d => d.longsToday), backgroundColor:'rgba(74,222,128,0.5)', stack:'daily'}},
        {{type:'bar', label:'Shorts Today', data: data.map(d => d.shortsToday), backgroundColor:'rgba(248,113,113,0.5)', stack:'daily'}}
    );

    activityDatasets.push(
        {{type:'line', label:'Total Longs', data: data.map(d => d.totalLongs), borderColor:'#4ade80', tension:0.3, pointRadius:2, yAxisID:'y1'}},
        {{type:'line', label:'Total Shorts', data: data.map(d => d.totalShorts), borderColor:'#f87171', tension:0.3, pointRadius:2, yAxisID:'y1'}},
        {{type:'line', label:'Trades', data: data.map(d => d.tradeId), borderColor:'#e2e8f0', tension:0.3, pointRadius:2, yAxisID:'y1'}}
    );

    new Chart(document.getElementById('activityChart'), {{
        data: {{ labels, datasets: activityDatasets }},
        options: {{
            responsive: true,
            plugins: {{legend: {{labels: {{font: {{size: 11}}, boxWidth: 12}}}}}},
            scales: {{
                x: {{ticks: {{font: {{size: 10}}, maxTicksLimit: 8}}, grid: {{color: gridColor()}}}},
                y: {{ beginAtZero: true, stacked: true, ticks: {{ font: {{ size: 10 }}, stepSize: 1 }}, grid: {{ color: gridColor() }}, title: {{ display: true, text: 'Daily', font: {{ size: 10 }}, color: '#475569' }} }},
                y1: {{ beginAtZero: true, position: 'right', ticks: {{ font: {{ size: 10 }} }}, grid: {{ drawOnChartArea: false }}, title: {{ display: true, text: 'Total', font: {{ size: 10 }}, color: '#475569' }} }}
            }}
        }}
    }});

        async function doLogout() {{
            try {{ await fetch('/logout'); }} catch(e) {{}}
            window.location.href = '/login';
        }}

    </script>

    </body>
    </html>
    """


@app.route("/settings")
def settings():
    if not is_admin_authenticated():
        return handle_unauthorized()

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta name="description" content="SGNT — Automated margin trading system.">
        <title>SGNT • Settings</title>
        <link rel="icon" type="image/png" href="/static/sgnticon.png">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet">
        <style>
            <!-- BOX STYLE -->
            *{box-sizing:border-box;margin:0;padding:0}
            body{background:#0f172a;color:#e2e8f0;font-family:'Inter',sans-serif;font-size:14px;transition:background 0.2s,color 0.2s}
            body.light{background:#f8fafc;color:#0f172a}

            <!-- SIDEBAR STYLE -->
            .layout{display:flex;min-height:100vh}
            .sidebar{width:160px;min-width:160px;background:#0a1120;border-right:0.5px solid #1e293b;display:flex;flex-direction:column;padding:16px 0;position:fixed;top:0;left:0;height:100vh;z-index:50}
            body.light .sidebar{background:#f1f5f9;border-right-color:#e2e8f0}
            .main{margin-left:160px;display:flex;flex-direction:column;flex:1;min-height:100vh}

            <!-- TOPBAR STYLE -->
            .topbar{position:fixed;top:0;left:160px;right:0;z-index:40;display:flex;justify-content:space-between;align-items:center;padding:0 20px;height:48px;background:#0f172a;border-bottom:0.5px solid #1e293b}
            body.light .topbar{background:#f8fafc;border-bottom-color:#e2e8f0}
            .content{padding:20px;margin-top:48px}
            .sidebar-logo{display:flex;align-items:center;justify-content:center;padding:0 16px 16px;border-bottom:0.5px solid #1e293b;margin-bottom:8px}
            body.light .sidebar-logo{border-bottom-color:#e2e8f0}
            .nav-item{display:flex;align-items:center;gap:8px;padding:8px 16px;font-size:11px;letter-spacing:0.06em;text-transform:uppercase;color:#475569;text-decoration:none;cursor:pointer;transition:0.15s;border-left:2px solid transparent;font-family:'Courier New',monospace}
            .nav-item:hover{color:#94a3b8;background:rgba(255,255,255,0.03)}
            body.light .nav-item:hover{background:rgba(0,0,0,0.04)}
            .nav-item.active{color:#f1f5f9;border-left-color:#1D9E75;background:rgba(29,158,117,0.08)}
            body.light .nav-item.active{color:#0f172a;border-left-color:#1D9E75;background:rgba(29,158,117,0.1)}
            body.light .nav-item{color:#64748b}

            <!-- TOPBAR LEFT & RIGHT STYLE -->
            .topbar-left{display:flex;align-items:center;gap:10px}
            .topbar-right{display:flex;align-items:center;gap:8px}
            .topbar-title{font-size:13px;font-weight:500;color:#f1f5f9}
            body.light .topbar-title{color:#0f172a}

            <!-- ACTIVE SIGNAL STYLE -->
            .dot{width:8px;height:8px;border-radius:50%;background:#1D9E75;display:inline-block}
            .dot.red{background:#E24B4A}
            .status-text{font-size:12px;color:#94a3b8;font-family:'Courier New',monospace}
            body.light .status-text{color:#64748b}

            <!-- TAG STYLE -->
            .tag{font-size:12px;padding:2px 8px;border-radius:6px;border:0.5px solid;font-family:'Courier New',monospace}
            .tag-live{border-color:#1D9E75;color:#1D9E75}

            <!-- THEME TOGGLE STYLE -->
            .theme-toggle{background:none;border:0.5px solid #334155;border-radius:20px;padding:4px 10px;cursor:pointer;font-size:13px;color:#94a3b8;transition:0.15s}
            body.light .theme-toggle{border-color:#cbd5e1;color:#64748b}
            .theme-toggle:hover{background:#1e293b}
            body.light .theme-toggle:hover{background:#e2e8f0}

            <!-- BUTTON STYLE -->
            .btn{padding:5px 12px;font-size:11px;border:0.5px solid #334155;border-radius:6px;background:#0f172a;color:#94a3b8;cursor:pointer;white-space:nowrap;transition:0.15s}
            body.light .btn{background:#f8fafc;border-color:#cbd5e1;color:#64748b}
            .btn:hover{background:#1e293b;color:#f1f5f9}
            body.light .btn:hover{background:#e2e8f0;color:#0f172a}
            .btn-danger{border-color:#7f1d1d;color:#fca5a5}
            .btn-danger:hover{background:#450a0a}

            <!-- OTHERS STYLE -->
            .btn-success{border-color:#14532d;color:#86efac}
            .btn-success:hover{background:#052e16}
            .btn-minmax{padding:3px 7px;font-size:10px;border:0.5px solid #334155;border-radius:4px;background:transparent;color:#475569;cursor:pointer;font-family:'Courier New',monospace;transition:0.15s}
            .btn-minmax:hover{background:#1e293b;color:#94a3b8}
            body.light .btn-minmax{border-color:#cbd5e1;color:#94a3b8}
            body.light .btn-minmax:hover{background:#e2e8f0}
            .db{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;padding:12px 0}
            .card{background:#1e293b;border:0.5px solid #334155;border-radius:8px;padding:14px 16px}
            body.light .card{background:#ffffff;border-color:#e2e8f0}
            .card-title{font-size:12px;letter-spacing:0.08em;color:#64748b;text-transform:uppercase;margin-bottom:10px;border-bottom:0.5px solid #334155;padding-bottom:6px}
            body.light .card-title{border-bottom-color:#e2e8f0}
            .toggle-row{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:0.5px solid #1e293b}
            body.light .toggle-row{border-bottom-color:#e2e8f0}
            .toggle-row:last-child{border-bottom:none}
            .toggle-label{font-size:12px;color:#94a3b8}
            body.light .toggle-label{color:#64748b}
            .toggle{position:relative;width:34px;height:18px;cursor:pointer}
            .toggle input{opacity:0;width:0;height:0}
            .slider{position:absolute;inset:0;background:#334155;border-radius:18px;transition:0.2s}
            .slider:before{content:'';position:absolute;width:12px;height:12px;left:3px;top:3px;background:#94a3b8;border-radius:50%;transition:0.2s}
            input:checked+.slider{background:#1D9E75}
            input:checked+.slider:before{transform:translateX(16px);background:white}
            .input-row{display:flex;gap:4px;margin-top:6px;align-items:center;flex-wrap:wrap}
            .input-row input[type=number],.input-row input[type=text]{flex:1;min-width:60px;padding:5px 8px;font-size:12px;background:#0f172a;border:0.5px solid #334155;border-radius:6px;color:#f1f5f9;font-family:'Courier New',monospace}
            body.light .input-row input[type=number],body.light .input-row input[type=text]{background:#f8fafc;border-color:#cbd5e1;color:#0f172a}
            .section-label{font-size:12px;letter-spacing:0.08em;color:#475569;text-transform:uppercase;margin:10px 0 4px;grid-column:1/-1;padding-left:2px}
            .toast{position:fixed;bottom:16px;right:16px;background:#1e293b;border:0.5px solid #334155;border-radius:8px;padding:8px 14px;font-size:12px;color:#f1f5f9;opacity:0;transition:opacity 0.3s;z-index:100;font-family:'Courier New',monospace}
            @media(max-width:600px){.sidebar{display:none}.main{margin-left:0}.topbar{left:0}.db{grid-template-columns:1fr}}
        </style>
    </head>
    <body>

    <div class="layout">
        <nav class="sidebar">
            <!-- SIDEBAR LOGO -->
            <div class="sidebar-logo">
                <img src="/static/sgnticon.png" alt="SGNT" onclick="window.location.href='/'" style="cursor:pointer;width:32px">
            </div>
            <!-- SIDEBAR CONTENT -->
            <a href="/dashboard" class="nav-item">Dashboard</a>
            <a href="/logs" class="nav-item">Logs</a>
            <a href="/history" class="nav-item">History</a>
            <a href="/metrics" class="nav-item">Metrics</a>
            <a href="/settings" class="nav-item active">Settings</a>
        </nav>

        <div class="main">
            <!-- TOPBAR -->
            <div class="topbar">
                <!-- TOPBAR LEFT -->
                <div class="topbar-left">
                    <span class="dot" id="dot"></span>
                    <span class="status-text" id="status-text">Loading...</span>
                    <span class="tag tag-live" id="mode-tag">LIVE</span>
                </div>
                <!-- TOPBAR RIGHT -->
                <div class="topbar-right">
                    <button class="btn btn-danger" onclick="doRestore()">Restore defaults</button>
                    <button class="btn btn-danger" onclick="doLogout()">Logout</button>
                    <button class="theme-toggle" onclick="toggleTheme()" id="theme-btn">🌙</button>
                </div>
            </div>

            <!-- CONTENT -->
            <div class="content">
                <div class="db">

                    <div class="section-label">Trading</div>

                    <div class="card">
                        <div class="card-title">Control</div>
                        <div class="toggle-row"><span class="toggle-label">Trading</span><label class="toggle"><input type="checkbox" id="tog-trading" onchange="setVar('trading',this.checked)"><span class="slider"></span></label></div>
                        <div class="toggle-row"><span class="toggle-label">SL Override</span><label class="toggle"><input type="checkbox" id="tog-sl" onchange="setVar('sl_override',this.checked)"><span class="slider"></span></label></div>
                        <div class="toggle-row"><span class="toggle-label">TP Override</span><label class="toggle"><input type="checkbox" id="tog-tp" onchange="setVar('tp_override',this.checked)"><span class="slider"></span></label></div>
                        <div class="toggle-row"><span class="toggle-label">Log Debug</span><label class="toggle"><input type="checkbox" id="tog-debug" onchange="setVar('log_debug',this.checked)"><span class="slider"></span></label></div>
                    </div>

                    <div class="card">
                        <div class="card-title">Parameters</div>
                        <div style="font-size:11px;color:#64748b;margin-bottom:4px">SL % <span id="sl-val" style="color:#f1f5f9">—</span></div>
                        <div class="input-row">
                            <input type="number" id="sl-input" placeholder="{{ MIN_SL_PCT }}–{{ MAX_SL_PCT }}" step="0.1" min="{{ MIN_SL_PCT }}" max="{{ MAX_SL_PCT }}">
                            <button class="btn-minmax" onclick="setInputVal('sl-input',{{ MIN_SL_PCT }})">min</button>
                            <button class="btn-minmax" onclick="setInputVal('sl-input',{{ MAX_SL_PCT }})">max</button>
                            <button class="btn" onclick="setVar('sl_pct', document.getElementById('sl-input').value)">Set</button>
                        </div>
                        <div style="font-size:11px;color:#64748b;margin:8px 0 4px">TP % <span id="tp-val" style="color:#f1f5f9">—</span></div>
                        <div class="input-row">
                            <input type="number" id="tp-input" placeholder="{{ MIN_TP_PCT }}–{{ MAX_TP_PCT }}" step="0.1" min="{{ MIN_TP_PCT }}" max="{{ MAX_TP_PCT }}">
                            <button class="btn-minmax" onclick="setInputVal('tp-input',{{ MIN_TP_PCT }})">min</button>
                            <button class="btn-minmax" onclick="setInputVal('tp-input',{{ MAX_TP_PCT }})">max</button>
                            <button class="btn" onclick="setVar('tp_pct', document.getElementById('tp-input').value)">Set</button>
                        </div>
                    </div>

                    <div class="card">
                        <div class="card-title">Platform</div>
                        <div style="font-size:11px;color:#64748b;margin-bottom:6px">Active platform</div>
                        <div style="font-size:13px;color:#f1f5f9;font-family:'Courier New',monospace" id="platform-val">—</div>
                    </div>

                    <div class="section-label">Admin</div>

                    <div class="card">
                        <div class="card-title">Operations</div>
                        <div style="font-size:11px;color:#94a3b8;margin:8px 0 4px">Borrow USDC</div>
                        <div class="input-row">
                            <input type="number" id="borrow-amt" placeholder="USDC quantity">
                            <button class="btn btn-success" onclick="doBorrow()">Borrow</button>
                        </div>
                        <div style="font-size:11px;color:#94a3b8;margin:8px 0 4px">Repay USDC</div>
                        <div class="input-row">
                            <input type="text" id="repay-amt" placeholder="USDC quantity (or 'all')">
                            <button class="btn btn-success" onclick="doRepay()">Repay</button>
                            <button class="btn" onclick="document.getElementById('repay-amt').value='all'">All</button>
                        </div>
                        <div style="font-size:11px;color:#94a3b8;margin:8px 0 4px">Clear</div>
                        <div class="input-row">
                            <input type="text" id="clear-sym" placeholder="Symbol (empty = all)">
                            <button class="btn btn-danger" onclick="doClear()">Clear</button>
                        </div>
                    </div>

                    <div class="card">
                        <div class="card-title">Admin Session</div>
                        <div style="font-size:11px;color:#64748b;margin-bottom:4px">Session Time <span id="session-val" style="color:#f1f5f9">—</span> min</div>
                        <div class="input-row">
                            <input type="number" id="session-input" placeholder="{{ MIN_SESSION_TIME }}–{{ MAX_SESSION_TIME }}" min="{{ MIN_SESSION_TIME }}" max="{{ MAX_SESSION_TIME }}">
                            <button class="btn-minmax" onclick="setInputVal('session-input',{{ MIN_SESSION_TIME }})">min</button>
                            <button class="btn-minmax" onclick="setInputVal('session-input',{{ MAX_SESSION_TIME }})">max</button>
                            <button class="btn" onclick="setVar('session_time',document.getElementById('session-input').value)">Set</button>
                        </div>
                        <div style="font-size:11px;color:#64748b;margin:8px 0 4px">Login Limit <span id="login-limit-val" style="color:#f1f5f9">—</span></div>
                        <div class="input-row">
                            <input type="number" id="login-limit-input" placeholder="{{ MIN_LOGIN_LIMIT }}–{{ MAX_LOGIN_LIMIT }}" min="{{ MIN_LOGIN_LIMIT }}" max="{{ MAX_LOGIN_LIMIT }}">
                            <button class="btn-minmax" onclick="setInputVal('login-limit-input',{{ MIN_LOGIN_LIMIT }})">min</button>
                            <button class="btn-minmax" onclick="setInputVal('login-limit-input',{{ MAX_LOGIN_LIMIT }})">max</button>
                            <button class="btn" onclick="setVar('login_limit',document.getElementById('login-limit-input').value)">Set</button>
                        </div>
                        <div style="font-size:11px;color:#64748b;margin:8px 0 4px">Login Retry <span id="login-retry-val" style="color:#f1f5f9">—</span> min</div>
                        <div class="input-row">
                            <input type="number" id="login-retry-input" placeholder="{{ MIN_LOGIN_RETRY }}–{{ MAX_LOGIN_RETRY }}" min="{{ MIN_LOGIN_RETRY }}" max="{{ MAX_LOGIN_RETRY }}">
                            <button class="btn-minmax" onclick="setInputVal('login-retry-input',{{ MIN_LOGIN_RETRY }})">min</button>
                            <button class="btn-minmax" onclick="setInputVal('login-retry-input',{{ MAX_LOGIN_RETRY }})">max</button>
                            <button class="btn" onclick="setVar('login_retry',document.getElementById('login-retry-input').value)">Set</button>
                        </div>
                    </div>

                </div>
            </div>
        </div>
    </div>

    <div class="toast" id="toast"></div>

    <script>
        function toast(msg, ok=true) {
            const t = document.getElementById('toast');
            t.textContent = msg;
            t.style.borderColor = ok ? '#14532d' : '#7f1d1d';
            t.style.opacity = '1';
            setTimeout(() => t.style.opacity = '0', 2500);
        }

        function setInputVal(id, val) {
            document.getElementById(id).value = val;
        }

        function toggleTheme() {
            const light = document.body.classList.toggle('light');
            document.getElementById('theme-btn').textContent = light ? '☀️' : '🌙';
            localStorage.setItem('sgnt-theme', light ? 'light' : 'dark');
        }

        if (localStorage.getItem('sgnt-theme') === 'light') {
            document.body.classList.add('light');
            document.getElementById('theme-btn').textContent = '☀️';
        }

        async function api(url) {
            try {
                const r = await fetch(url);
                if (r.status === 403) {
                    toast('Session expired', false);
                    setTimeout(() => window.location.href = '/login', 1500);
                    return null;
                }
                return await r.json();
            } catch(e) { toast('Network error', false); return null; }
        }

        async function loadData() {
            const d = await api('/snapshot');
            if (!d) return;
            const v = d.variables || {};
            document.getElementById('tog-trading').checked = !!v.trading;
            document.getElementById('tog-sl').checked = !!v.sl_override;
            document.getElementById('tog-tp').checked = !!v.tp_override;
            document.getElementById('tog-debug').checked = !!v.log_debug;
            document.getElementById('sl-val').textContent = v.sl_pct != null ? v.sl_pct + '%' : '—';
            document.getElementById('tp-val').textContent = v.tp_pct != null ? v.tp_pct + '%' : '—';
            document.getElementById('session-val').textContent = v.session_time != null ? v.session_time : '—';
            document.getElementById('login-limit-val').textContent = v.login_limit != null ? v.login_limit : '—';
            document.getElementById('login-retry-val').textContent = v.login_retry != null ? v.login_retry : '—';
            document.getElementById('platform-val').textContent = v.platform || '—';
        }

        async function setVar(varName, val) {
            if (val === undefined || val === null || val === '') { toast('Insert value', false); return; }
            let parsedVal = val;
            if (typeof val === 'boolean') parsedVal = val ? 'true' : 'false';
            const d = await api(`/set?var=${varName}&value=${encodeURIComponent(parsedVal)}`);
            if (d && d.status === 'ok') toast(`${varName} updated`);
            else toast(d?.msg || 'Error', false);
            await loadData();
        }

        async function doBorrow() {
            const amt = document.getElementById('borrow-amt').value;
            if (!amt) { toast('Insert quantity', false); return; }
            const d = await api(`/borrow?amount=${amt}`);
            if (d) toast(`Borrow ${amt} USDC OK`);
        }

        async function doRepay() {
            const amt = document.getElementById('repay-amt').value;
            if (!amt) { toast('Insert quantity or "all"', false); return; }
            const d = await api(`/repay?amount=${amt}`);
            if (d) toast(`Repay ${amt} OK`);
        }

        async function doClear() {
            const sym = document.getElementById('clear-sym').value.trim();
            const url = sym ? `/clear?symbol=${sym}` : '/clear';
            const d = await api(url);
            if (d) toast(sym ? `Clear ${sym} OK` : 'Clear all OK');
        }

        async function doRestore() {
            const d = await api('/restore');
            if (d) toast('Restore completed');
            await loadData();
        }

        async function doLogout() {
            try { await fetch('/logout'); } catch(e) {}
            window.location.href = '/login';
        }

        loadData();
    </script>

    </body>
    </html>
    """
    return render_template_string(html,
    MIN_SL_PCT=MIN_SL_PCT,
    MAX_SL_PCT=MAX_SL_PCT,
    MIN_TP_PCT=MIN_TP_PCT,
    MAX_TP_PCT=MAX_TP_PCT,
    MIN_LOGIN_LIMIT=MIN_LOGIN_LIMIT,
    MAX_LOGIN_LIMIT=MAX_LOGIN_LIMIT,
    MIN_LOGIN_RETRY=MIN_LOGIN_RETRY,
    MAX_LOGIN_RETRY=MAX_LOGIN_RETRY,
    MIN_SESSION_TIME=MIN_SESSION_TIME,
    MAX_SESSION_TIME=MAX_SESSION_TIME,
)


# ====== FLASK EXECUTION ======
"""Starts the Flask development server on the configured port."""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)

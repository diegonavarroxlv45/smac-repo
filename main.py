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
from datetime import datetime
from threading import Lock, Thread
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
DFT_TESTNET = False
DFT_SL_OVERRIDE = True
DFT_TP_OVERRIDE = True
DFT_SL_PCT = 2.0
DFT_TP_PCT = 4.0
DFT_RETRIES = 3
DFT_LOG_VIEW = 50
DFT_LOGIN_LIMIT = 5
DFT_LOGIN_RETRY = 5
DFT_SESSION_TIME = 5

# --- BOOL VARIABLES ---
TRADING = os.getenv("TRADING", "true").lower() == "true"
TESTNET = os.getenv("TESTNET", "false").lower() == "true"
SL_OVERRIDE = os.getenv("SL_OVERRIDE", "true").lower() == "true"
TP_OVERRIDE = os.getenv("TP_OVERRIDE", "true").lower() == "true"

# --- ENVIRONMENT VARIABLES ---
SL_PCT = float(os.getenv("SL_PCT", "2"))                     # %
TP_PCT = float(os.getenv("TP_PCT", "4"))                     # %
RETRIES = int(os.getenv("RETRIES", "3"))                     # NUMBER
LOG_VIEW = int(os.getenv("LOG_VIEW", "50"))                  # NUMBER
LOGIN_LIMIT = int(os.getenv("LOGIN_LIMIT", "5"))             # NUMBER
LOGIN_RETRY = int(os.getenv("LOGIN_RETRY", "5"))             # MINUTES
SESSION_TIME = int(os.getenv("SESSION_TIME", "5"))           # MINUTES

# --- VARIABLE LIMITS ---
MIN_SL_PCT = 0.1                                             # %
MIN_TP_PCT = 0.1                                             # %
MIN_RETRIES = 1                                              # NUMBER
MIN_LOG_VIEW = 0                                             # NUMBER
MIN_LOGIN_LIMIT = 1                                          # NUMBER
MIN_LOGIN_RETRY = 1                                          # MINUTES
MIN_SESSION_TIME = 1                                         # MINUTES
MAX_SL_PCT = 50                                              # %
MAX_TP_PCT = 50                                              # %
MAX_RETRIES = 5                                              # NUMBER
MAX_LOG_VIEW = 100                                           # NUMBER
MAX_LOGIN_LIMIT = 15                                         # NUMBER
MAX_LOGIN_RETRY = 15                                         # MINUTES
MAX_SESSION_TIME = 15                                        # MINUTES

# --- SDP PERIODS ---
BOOT_PERIOD = int(os.getenv("BOOT_PERIOD", "1"))             # MINUTES
GRACE_PERIOD = int(os.getenv("GRACE_PERIOD", "2"))           # MINUTES

# --- TRADE COUNTER VARIABLES ---
TRADE_COUNTER = 0                                            # NUMBER
DAILY_LONGS = 0                                              # NUMBER
DAILY_SHORTS = 0                                             # NUMBER
TOTAL_LONGS = 0                                              # NUMBER
TOTAL_SHORTS = 0                                             # NUMBER
CURRENT_DAY = datetime.utcnow().date()                       # NUMBER

# --- MARGIN LEVEL THRESHOLDS ---
ML_WARNING = float(os.getenv("ML_WARNING", "2"))             # NUMBER
ML_DANGER = float(os.getenv("ML_DANGER", "1.25"))            # NUMBER
ML_CRITICAL = float(os.getenv("ML_CRITICAL", 1.16"))         # NUMBER
ML_LIQUIDATION = float(os.getenv("ML_LIQUIDATION", "1.1"))   # NUMBER

# --- RISK_PCT VARIABLES ---
MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", "20"))        # %
MAX_RISK_PCT = max(0.1, min(MAX_RISK_PCT, 20))               # %
DFT_RISK_PCT = float(os.getenv("DFT_RISK_PCT", "5"))         # %
DFT_RISK_PCT = max(0.1, min(DFT_RISK_PCT, MAX_RISK_PCT))     # %

# --- SL/TP COMISSION ---
COMMISSION = Decimal(os.getenv("COMMISSION", "0.1"))         # %

# --- SNAPSHOT VARIABLES ---
SNAPSHOT_INTERVAL = int(os.getenv("SNAPSHOT_INTERVAL", "1")) # DAYS
MAX_SNAPSHOTS = int(os.getenv("MAX_SNAPSHOTS", "500"))       # NUMBER

# --- SECRET VARIABLES ---
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")               # API
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")         # API
TESTNET_API_KEY = os.getenv("TESTNET_API_KEY")               # API
TESTNET_API_SECRET = os.getenv("TESTNET_API_SECRET")         # API
TRADING_KEY = os.getenv("TRADING_KEY")                       # KEY
ADMIN_KEY = os.getenv("ADMIN_KEY")                           # KEY

# --- PORT VARIABLES ---
PORT = int(os.getenv("PORT", "5000"))


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

formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(filename)s:%(lineno)d | %(message)s",
)

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
"""Binance API configuration: selects live or testnet credentials and base URL, validates that secrets are present."""

# --- BINANCE / TESTNET CONFIGURATION ---
if TESTNET:
    # 🧪 TESTNET CONFIG
    BINANCE_API_KEY = os.getenv("TESTNET_API_KEY")
    BINANCE_API_SECRET = os.getenv("TESTNET_API_SECRET")
    BASE_URL = "https://testnet.binance.vision"

    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        logger.error("❌ Missing TESTNET API credentials")
        raise RuntimeError("Missing TESTNET API credentials")

    logger.info("🧪 Running in TESTNET mode")
    logger.info("🔐 Testnet API credentials loaded successfully")

else:
    # 🌐 BINANCE CONFIG
    BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
    BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
    BASE_URL = "https://api.binance.com"

    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        logger.error("❌ Missing BINANCE API credentials")
        raise RuntimeError("Missing BINANCE API credentials")

    logger.info("🌐 Running in LIVE mode")
    logger.info("🔐 Binance API credentials loaded successfully")


# ====== SAFE DEPLOYMENT PATTERN ======
"""Boot protection and grace period logic, health checks, and bot readiness state machine to prevent trading during unstable deploys."""

# --- SDP SETTINGS ---
BOOT_TIME = time.time()
BOT_READY = False
LAST_HEALTH_CHECK = 0
HEALTH_CHECK_INTERVAL = 10
LAST_HEALTH_STATUS = False

# --- PUBLIC BINANCE REQUEST ---
def send_public_request(http_method: str, path: str, params=None):
    url = f"{BASE_URL}{path}"

    try:
        return _request_with_retries(http_method, url, params=params)

    except Exception as e:
        logger.error(f"⚠️ Public request failed {path}: {e}")
        raise

# --- GLOBAL HEALTH CHECK ---
def health_check():
    if not BOT_READY:
        logger.info("🩺 Running health check...")

    # 📡 CHECK BINANCE CONNECTIVITY
    try:
        send_public_request("GET", "/api/v3/time")

    except Exception as e:
        logger.error(f"❌ Binance connectivity failed: {e}")
        return False

    # 📡 CHECK ACCOUNT ACCESS
    try:
        get_balance_margin("USDC")

    except Exception as e:
        logger.error(f"❌ Account access failed: {e}")
        return False

    logger.info("✅ Health check passed")
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

    if not TRADING:
        logger.info("🛑 Trading manually disabled (TRADING=false)")
        return False

    if BOT_READY:
        if not health_check_cached():
            logger.error("⚠️ Bot lost health — disabling trading")
            BOT_READY = False
            return False
        return True

    uptime = time.time() - BOOT_TIME

    if uptime < (BOOT_PERIOD * 60):
        logger.info(f"⌛ Boot protection active ({int(uptime)}s/{BOOT_PERIOD * 60}s)")
        return False

    if uptime < (GRACE_PERIOD * 60):
        logger.info(f"⏳ Deploy grace period ({int(uptime)}s/{GRACE_PERIOD * 60}s)")
        return False

    if not health_check_cached():
        logger.error("⚠️ Bot not healthy yet")
        return False

    BOT_READY = True
    logger.info("🚀 BOT READY — trading ENABLED")
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


# ====== TIME FUNCTION ======
"""Returns the current UTC timestamp in milliseconds for Binance API request signing."""

def _now_ms():
    return int(time.time() * 1000)


# ====== SIGNING AND REQUESTING ======
"""HMAC-SHA256 request signing, retry logic with exponential backoff, and signed/unsigned request dispatchers."""

# --- SIGNING ---
def sign_params_query(params: dict, secret: str):
    query = "&".join([f"{k}={v}" for k, v in params.items()])
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query, signature

# --- REQUESTING ---
def _request_with_retries(method: str, url: str, **kwargs):
    for i in range(RETRIES):
        try:
            resp = requests.request(method, url, timeout=10, **kwargs)

            # ⚠ ERROR 429
            if resp.status_code == 429:
                logger.error(f"🚫 429 RATE LIMIT hit (attempt {i+1}) → sleeping 3s")
                time.sleep(3)
                continue

            try:
                data = resp.json()

            except Exception:
                data = resp.text

            if isinstance(data, dict) and "code" in data:
                # ⚠ ERROR -3045
                if data["code"] == -3045:
                    return data

                # ⚠ ERROR -2011
                if data["code"] == -2011:
                    return data

                # ⚠ ERROR -2010
                if data["code"] == -2010:
                    msg = data.get("msg", "").lower()
                    if "insufficient balance" in msg:
                         return data

                # ⚠ ERROR -1013
                if data["code"] == -1013:
                    return data

            # ✅ 200 OK
            if resp.status_code == 200:
                return data

            logger.error(f"⚠️ Attempt {i+1} failed: {data}")

        except Exception as e:
            logger.error(f"⚠️ Request error: {e}")

        time.sleep(1)

    raise Exception("❌ Request failed after retries")

# --- SEND REQUESTS ---
def send_signed_request(http_method: str, path: str, payload: dict):
    if "timestamp" not in payload:
        payload["timestamp"] = _now_ms()

    query_string = "&".join([f"{k}={v}" for k, v in payload.items()])
    signature = hmac.new(BINANCE_API_SECRET.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE_URL}{path}?{query_string}&signature={signature}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    return _request_with_retries(http_method, url, headers=headers)


# ====== BALANCE & MARKET DATA ======
"""Fetches free margin balance for a given asset and retrieves symbol lot size, tick size, and notional constraints from exchange info."""

# --- BALANCE FETCHING ---
def get_balance_margin(asset="USDC") -> float:
    ts = _now_ms()
    params = {"timestamp": ts}
    q, sig = sign_params_query(params, BINANCE_API_SECRET)
    url = f"{BASE_URL}/sapi/v1/margin/account?{q}&signature={sig}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    data = _request_with_retries("GET", url, headers=headers)
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
    params = {}
    acc = send_signed_request("GET", "/sapi/v1/margin/account", params)
    return acc


# ====== TRADE COUNTER ======
"""Thread-safe trade ID generator, daily trade summary logger, and midnight reset watcher."""

# --- TRADE ID ---
def next_trade_id():
    global TRADE_COUNTER

    with TRADE_LOCK:
        TRADE_COUNTER += 1
        return TRADE_COUNTER

# --- DAILY SUMMARY ---
def check_daily_summary():
    global DAILY_LONGS, DAILY_SHORTS, CURRENT_DAY

    now_day = datetime.utcnow().date()

    if now_day != CURRENT_DAY:
        total_trades = DAILY_LONGS + DAILY_SHORTS

        if total_trades > 0:
            logger.date(
                f"📅 Day {CURRENT_DAY} completed! "
                f"Trades: {total_trades} "
                f"(Longs: {DAILY_LONGS} | Shorts: {DAILY_SHORTS})"
            )
            logger.date(f"________________________________________")

        DAILY_LONGS = 0
        DAILY_SHORTS = 0
        CURRENT_DAY = now_day

# --- DAILY WATCHER ---
def daily_watcher():
    while True:
        check_daily_summary()
        time.sleep(60)

threading.Thread(target=daily_watcher, daemon=True).start()


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
        if margin_level <= ML_LIQUIDATION:
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

    # 💯 RISK_PCT LIMITS
    risk_pct = min(risk_pct, MARGIN_MAX_RISK_PCT)
    return risk_pct / 100


# ====== PRE-TRADE CLEANUP ======
"""Before each trade: cancels open orders, repays outstanding debt, and sells residual asset balance back to USDC."""

# --- PRE-TRADE CLEANUP ---
def handle_pre_trade_cleanup(symbol: str):
    base_asset = symbol.replace("USDC", "")

    try:
        cancel(symbol)
        refund(symbol)
        residual(symbol

    except Exception as e
        logger.error(f"⚠️ Couldn't handle pre-trade cleanup for {symbol}: {e}")

# --- CANCEL ORDERS FROM PREVIOUS POSITIONS ---
def cancel(symbol: str):
    base_asset = symbol.replace("USDC", "")

    try:
        # 🧹 ORDER CANCEL PARAMS
        params = {
            "symbol": symbol,
            "timestamp": _now_ms()
        }

        resp = send_signed_request("DELETE", "/sapi/v1/margin/openOrders", params)

        if isinstance(resp, dict) and resp.get("code") == -2011:
            logger.info(f"ℹ️ No open orders to cancel for {symbol}")
            return

        logger.info(f"🧹 Pending orders for {symbol} canceled")

    except Exception as e:
        logger.error(f"⚠️ Couldn't cancel orders for {symbol}: {e}")

# --- CANCEL ALL ORDERS FOR CLEAR ---
def cancel_all():
    try:
        params = {"timestamp": _now_ms()}
        q, sig = sign_params_query(params, BINANCE_API_SECRET)

        url = f"{BASE_URL}/sapi/v1/margin/openOrders?{q}&signature={sig}"
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        open_orders = _request_with_retries("GET", url, headers=headers)

        if not open_orders:
            logger.info("ℹ️ No open orders found")
            return

        symbols = set(order["symbol"] for order in open_orders)

        for symbol in symbols:
            cancel(symbol)

    except Exception as e:
        logger.error(f"⚠️ Couldn't cancel all orders: {e}")

# --- REFUND DEBT FROM PREVIOUS POSITIONS ---
def refund(symbol: str):
    base_asset = symbol.replace("USDC", "")

    try:
        ts = _now_ms()
        q, sig = sign_params_query({"timestamp": ts}, BINANCE_API_SECRET)
        url = f"{BASE_URL}/sapi/v1/margin/account?{q}&signature={sig}"
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        acc_data = _request_with_retries("GET", url, headers=headers)

        asset_data = next((a for a in acc_data["userAssets"] if a["asset"] == base_asset), None)
        usdc_data  = next((a for a in acc_data["userAssets"] if a["asset"] == "USDC"), None)

        if not asset_data:
            logger.error(f"ℹ️ {base_asset} not present in margin account")
            return

        borrowed   = float(asset_data["borrowed"])
        free_base = float(asset_data["free"])
        free_usdc = float(usdc_data["free"]) if usdc_data else 0.0

        if borrowed <= 0:
            logger.info(f"ℹ️ No active debt in {base_asset}")
        else:
            logger.info(f"💳 Active debt detected: {borrowed} {base_asset}")

            missing = borrowed - free_base

            if missing > 0:
                lot = get_symbol_lot(symbol)

                buy_qty = missing * 1.02

                qty_str = floor_to_step_str(buy_qty, lot["stepSize_str"])
                qty_f = float(qty_str)

                if qty_f <= 0:
                    raise Exception("❌ Calculated buy qty is zero after stepSize rounding")

                r = _request_with_retries("GET", f"{BASE_URL}/api/v3/ticker/price", params={"symbol": symbol})
                price_est = float(r["price"])

                if qty_f * price_est < lot["minNotional"]:
                    raise Exception("❌ Buy notional below minNotional, aborting repay cleanup")

                needed_usdc = qty_f * price_est

                if needed_usdc > free_usdc:
                    raise Exception(f"❌ Not enough USDC to buy repay asset (need {needed_usdc:.4f}, have {free_usdc:.4f})")

                # 🛒 DEBT TOP UP PARAMS
                buy_params = {
                    "symbol": symbol,
                    "side": "BUY",
                    "type": "MARKET",
                    "quantity": qty_str,
                    "timestamp": _now_ms()
                }

                send_signed_request("POST", "/sapi/v1/margin/order", buy_params)
                logger.info(f"🛒 Bought {qty_str} {base_asset} to reduce debt")

                time.sleep(3)

            # --- Refresh balances after buy ---
            ts = _now_ms()
            q, sig = sign_params_query({"timestamp": ts}, BINANCE_API_SECRET)
            url = f"{BASE_URL}/sapi/v1/margin/account?{q}&signature={sig}"
            acc_data = _request_with_retries("GET", url, headers=headers)

            asset_data = next((a for a in acc_data["userAssets"] if a["asset"] == base_asset), None)

            borrowed   = float(asset_data["borrowed"])
            free_base = float(asset_data["free"])

            # --- Repay only what is available ---
            repay_amount = min(borrowed, free_base)

            if repay_amount > 0:
                # 💰 REPAY PARAMS
                repay_params = {
                    "asset": base_asset,
                    "amount": str(repay_amount),
                    "timestamp": _now_ms()
                }

                send_signed_request("POST", "/sapi/v1/margin/repay", repay_params)
                logger.info(f"💰 Repay executed: {repay_amount} {base_asset}")

            remaining = borrowed - repay_amount

            if remaining > 0:
                logger.info(f"⚠️ Remaining debt after repay: {remaining:.8f} {base_asset}")
            else:
                logger.info(f"ℹ️ Debt fully cleared for {base_asset}")

    except Exception as e:
        logger.error(f"⚠️ Error during repay in {base_asset}: {e}")

# --- SELL RESIDUAL FROM PREVIOUS POSITIONS ---
def residual(symbol: str):
    base_asset = symbol.replace("USDC", "")

    try:
        lot = get_symbol_lot(symbol)

        ts = _now_ms()
        q, sig = sign_params_query({"timestamp": ts}, BINANCE_API_SECRET)
        url = f"{BASE_URL}/sapi/v1/margin/account?{q}&signature={sig}"
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        acc_data = _request_with_retries("GET", url, headers=headers)

        asset_data = next((a for a in acc_data["userAssets"] if a["asset"] == base_asset), None)
        usdc_data = next((a for a in acc_data["userAssets"] if a["asset"] == "USDC"), None)

        if not asset_data:
            return

        free = float(asset_data["free"])
        free_usdc = float(usdc_data["free"]) if usdc_data else 0.0

        if free <= 0:
            logger.info(f"ℹ️ No residual {base_asset} to sell")
            return

        r = _request_with_retries("GET", f"{BASE_URL}/api/v3/ticker/price", params={"symbol": symbol})
        price_est = float(r["price"])

        qty_str = floor_to_step_str(free, lot["stepSize_str"])
        qty_f = float(qty_str)

        if qty_f <= 0:
            logger.info(f"ℹ️ Residual {base_asset} too small to sell")
            return

        notional = qty_f * price_est

        if notional < lot["minNotional"]:
            needed_qty = (lot["minNotional"] / price_est) - qty_f
            needed_qty_buffered = needed_qty * 1.02
            buy_qty_str = floor_to_step_str(needed_qty_buffered, lot["stepSize_str"])
            buy_qty_f = float(buy_qty_str)

            if buy_qty_f <= 0:
                logger.info(f"ℹ️ Cannot top up {base_asset} to reach minNotional — skipping")
                return

            top_up_cost = buy_qty_f * price_est
            if top_up_cost > free_usdc:
                logger.info(f"ℹ️ Not enough USDC to top up {base_asset} (need {top_up_cost:.4f}, have {free_usdc:.4f}) — skipping")
                return

            # 🛒 RESIDUAL TOP UP PARAMS
            buy_params = {
                "symbol": symbol,
                "side": "BUY",
                "type": "MARKET",
                "quantity": buy_qty_str,
                "timestamp": _now_ms()
            }
            send_signed_request("POST", "/sapi/v1/margin/order", buy_params)
            logger.info(f"🛒 Topped up {buy_qty_str} {base_asset} to reach minNotional")

            time.sleep(1)

            ts = _now_ms()
            q, sig = sign_params_query({"timestamp": ts}, BINANCE_API_SECRET)
            url = f"{BASE_URL}/sapi/v1/margin/account?{q}&signature={sig}"
            acc_data = _request_with_retries("GET", url, headers=headers)
            asset_data = next((a for a in acc_data["userAssets"] if a["asset"] == base_asset), None)
            free = float(asset_data["free"])
            qty_str = floor_to_step_str(free, lot["stepSize_str"])
            qty_f = float(qty_str)

            if qty_f <= 0:
                logger.info(f"ℹ️ Residual {base_asset} too small to sell after top up")
                return

        # 🧹 SELL RESIDUAL PARAMS
        sell_params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": qty_str,
            "timestamp": _now_ms()
        }

        resp = send_signed_request("POST", "/sapi/v1/margin/order", sell_params)

        if isinstance(resp, dict):
            if resp.get("code") == -2010:
                logger.info(f"ℹ️ Insufficient balance to sell residual {base_asset}, skipping")
                return

            if resp.get("code") == -1013:
                logger.error(f"⚠️ NOTIONAL error while selling {base_asset} — qty too small or below minNotional")
                return

        logger.info(f"🧹 Sold residual {qty_str:.5f} {base_asset} to USDC")

    except Exception as e:
        logger.error(f"⚠️ Error selling residual {base_asset}: {e}")


# ====== MAIN FUNCTIONS ======
"""Core trade execution: margin long (buy with quote quantity), margin short (borrow and sell), post-trade handling, and SL/TP placement."""

# --- MARGIN LONG ---
def execute_long_margin(symbol, webhook_data=None):
    lot = get_symbol_lot(symbol)
    balance_usdc = get_balance_margin("USDC")
    risk_pct = resolve_risk_pct(webhook_data)
    qty_quote = balance_usdc * risk_pct

    # 📈 MARGIN LONG PARAMS
    params = {
        "symbol": symbol,
        "side": "BUY",
        "type": "MARKET",
        "quoteOrderQty": format(qty_quote, "f"),
        "timestamp": _now_ms()
    }

    resp = send_signed_request("POST", "/sapi/v1/margin/order", params)

    if isinstance(resp, dict) and resp.get("code") == -1013:
        logger.error(f"⚠️ Long skipped for {symbol}: notional too small")
        return {"error": "notional_too_small"}

    executed_qty, entry_price = extract_execution_info(resp)

    # 📈 TRADE COUNTER LONG
    trade_id = next_trade_id()
    global DAILY_LONGS, TOTAL_LONGS
    DAILY_LONGS += 1
    TOTAL_LONGS += 1

    side = "BUY"
    handle_post_trade(symbol, side, resp, lot, webhook_data, trade_id)
    return {"order": resp, "trade_id": trade_id}

# --- MARGIN SHORT ---
def execute_short_margin(symbol, webhook_data=None):
    lot = get_symbol_lot(symbol)
    balance_usdc = get_balance_margin("USDC")

    try:
        r = _request_with_retries("GET", f"{BASE_URL}/api/v3/ticker/price", params={"symbol": symbol})
        price_est = float(r.get("price", 0))

    except Exception as e:
        logger.error(f"⚠️ Could not fetch price for {symbol}: {e}")
        return {"error": "price_fetch_failed"}

    if price_est <= 0:
        logger.error(f"⚠️ Invalid price detected: {price_est}")
        raise Exception ("❌ Invalid price")

    risk_pct = resolve_risk_pct(webhook_data)
    raw_qty = Decimal(str(balance_usdc * risk_pct)) / Decimal(str(price_est))

    try:
        qty_str = borrowing(raw_qty, lot, price_est, symbol)

    except Exception as e:
        logger.error(f"❌ Borrow failed: {e}")
        return {"error": "borrow_failed"}

    # 📉 MARGIN SHORT PARAMS
    params = {
        "symbol": symbol,
        "side": "SELL",
        "type": "MARKET",
        "quantity": qty_str,
        "timestamp": _now_ms()
    }

    resp = send_signed_request("POST", "/sapi/v1/margin/order", params)

    if isinstance(resp, dict) and resp.get("code") == -1013:
        logger.error(f"⚠️ Short skipped for {symbol}: notional too small")
        return {"error": "notional_too_small"}

    executed_qty, entry_price = extract_execution_info(resp)

    # 📉 TRADE COUNTER SHORT
    trade_id = next_trade_id()
    global DAILY_SHORTS, TOTAL_SHORTS
    DAILY_SHORTS += 1
    TOTAL_SHORTS += 1

    side = "SELL"
    handle_post_trade(symbol, side, resp, lot, webhook_data, trade_id)
    return {"order": resp, "trade_id": trade_id}

# --- BORROWING (FOR SHORT) ---
def borrowing(raw_qty, lot, price_est, symbol):
    borrow_amount = float(raw_qty.quantize(Decimal(str(lot["stepSize_str"])), rounding=ROUND_DOWN))

    if borrow_amount <= 0 or borrow_amount < lot.get("minQty", 0.0):
        raise Exception(f"Qty {borrow_amount} < minQty")

    if (borrow_amount * price_est) < lot.get("minNotional", 0.0):
        raise Exception("Notional too small")

    # 📥 BORROW PARAMS
    borrow_params = {
        "asset": symbol.replace("USDC", ""),
        "amount": format(Decimal(str(borrow_amount)), "f"),
        "timestamp": _now_ms()
    }

    borrow_resp = send_signed_request("POST", "/sapi/v1/margin/loan", borrow_params)
    time.sleep(0.3)

    borrowed_qty = float(
        borrow_resp.get("amount") or
        borrow_resp.get("qty") or
        borrow_amount
    )

    logger.info(f"📥 Borrowed {borrowed_qty} {symbol.replace('USDC','')}")
    qty_str = floor_to_step_str(borrowed_qty, lot["stepSize_str"])

    if float(qty_str) < lot.get("minQty", 0.0):
        raise Exception("Borrowed qty too small")

    return qty_str

# --- EXECUTION INFO ---
def extract_execution_info(resp):
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

    return executed_qty, entry_price

# --- POST TRADE ---
def handle_post_trade(symbol, side, resp, lot, webhook_data, trade_id):
    executed_qty, entry_price = extract_execution_info(resp)

    if executed_qty == 0:
        logger.error(f"[TRADE {trade_id}] ⚠️ No execution detected")

    emoji = "📈" if side == "BUY" else "📉"
    logger.info(f"[TRADE {trade_id}] {emoji} {side} executed {symbol}: qty={executed_qty} (spent≈{(entry_price * executed_qty) if entry_price is not None else 'unknown'} USDC)")

    if executed_qty > 0 and entry_price:
        sl_from_web = None
        tp_from_web = None

        if webhook_data:
            sl_from_web = webhook_data.get("sl")
            tp_from_web = webhook_data.get("tp")

        # 📌 SL/TP PLACING
        place_sl_tp_margin(
            symbol,
            side,
            entry_price,
            executed_qty,
            lot,
            sl_override=sl_from_web,
            tp_override=tp_from_web,
            trade_id=trade_id
            )

    return executed_qty, entry_price

# ====== SL/TP FUNCTIONS ======
"""Places OCO, stop-loss-only, or take-profit-only orders after trade execution, with tick-aligned prices and commission-adjusted quantities."""

# --- SL/TP PLACING ---
def place_sl_tp_margin(symbol: str, side: str, entry_price: float, executed_qty: float, lot: dict, sl_override=None, tp_override=None, trade_id=None):
    try:
        COMMISSION_BUFFER = Decimal("1") - (COMMISSION / Decimal("100"))
        oco_side = "SELL" if side == "BUY" else "BUY"

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
            sl_price = entry_price * (1 - SL_PCT / 100) if side == "BUY" else entry_price * (1 + SL_PCT / 100)
        else:
            sl_price = None

        # --- TP Price calculation ---
        if tp_override is not None:
            tp_price = float(tp_override)
        elif TP_OVERRIDE and TP_PCT is not None:
            tp_price = entry_price * (1 + TP_PCT / 100) if side == "BUY" else entry_price * (1 - TP_PCT / 100)
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
                stop_limit_aligned = align_price(sl_price_aligned * 0.999, lot["tickSize_str"], ROUND_DOWN)
            else:
                stop_limit_aligned = align_price(sl_price_aligned * 1.001, lot["tickSize_str"], ROUND_UP)

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

            try:
                send_signed_request("POST", "/sapi/v1/margin/order/oco", params)
                direction = 1 if side == "BUY" else -1
                entry_f = float(entry_price)
                tp_f = float(tp_price_str)
                sl_f = float(sl_price_str)
                profit_tp = (tp_f - entry_f) * qty_f * direction
                loss_sl = (sl_f - entry_f) * qty_f * direction
                rr = abs(profit_tp / loss_sl) if loss_sl != 0 else 0
                logger.info(f"[TRADE {trade_id}] 📌 OCO placed for {symbol}: TP={tp_price_str} ({oco_side}), SL={sl_price_str} ({oco_side}), qty={qty_str:.5f}")
                logger.info(f"[TRADE {trade_id}] 🟢 TP PnL ≈ {profit_tp:.2f} USDC | 🔴 SL PnL ≈ {loss_sl:.2f} USDC | ⚖️ R:R {rr:.2f}")
                return True

            except Exception as e:
                logger.error(f"⚠️ Failed OCO for {symbol}, payload={params}: {e}")
                return False

        # --- SL ONLY ---
        if order_type == "SL_ONLY":
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

            try:
                send_signed_request("POST", "/sapi/v1/margin/order", params)
                logger.info(f"[TRADE {trade_id}] 🛑 SL placed for {symbol}: stop={sl_price_str}, limit={stop_limit_price}, qty={qty_str}")
                return True

            except Exception as e:
                logger.error(f"⚠️ Could not place SL for {symbol}: {e}")
                return False

        # --- TP ONLY ---
        if order_type == "TP_ONLY":
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

            try:
                send_signed_request("POST", "/sapi/v1/margin/order", params)
                logger.info(f"[TRADE {trade_id}] 🎯 TP placed for {symbol}: price={tp_price_str}, qty={qty_str}")
                return True

            except Exception as e:
                logger.error(f"⚠️ Could not place TP for {symbol}: {e}")
                return False

    except Exception as e:
        logger.error(f"⚠️ Could not place SL/TP for {symbol}: {e}")
        return False


# ====== SNAPSHOT METRICS ======
"""Periodic account snapshots stored in memory for the metrics dashboard: balance, margin level, debt, and trade activity."""

# --- SNAPSHOT PLACEHOLDERS
SNAPSHOT_HISTORY = []
SNAPSHOT_LOCK = Lock()

# --- SNAPSHOT THREADING ---
def start_snapshot_workers():
    Thread(target=snapshot_worker, daemon=True).start()
    logger.info("🚀 Snapshot worker started")

# --- SNAPSHOT MEMORY STORAGE ---
def store_snapshot(snapshot):
    global SNAPSHOT_HISTORY

    with SNAPSHOT_LOCK:
        # 📸 CLEAN SNAPSHOT
        clean_snapshot = {
            # ⌚ TIME
            "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),

            # 💰 BALANCE
            "totalBalanceUSDC": snapshot["totalBalanceUSDC"],
            "usdcBalance": snapshot["usdcBalance"],
            "totalDebt": snapshot["totalDebt"],
            "usdcBorrowed": snapshot["usdcBorrowed"],

            # ⚖ RISK
            "marginLevel": snapshot["marginLevel"],

            # 📈 ACTIVITY
            "longsToday": snapshot["longsToday"],
            "shortsToday": snapshot["shortsToday"],
            "totalLongs": snapshot["totalLongs"],
            "totalShorts": snapshot["totalShorts"]
        }

        SNAPSHOT_HISTORY.append(clean_snapshot)

        if len(SNAPSHOT_HISTORY) > MAX_SNAPSHOTS:
            SNAPSHOT_HISTORY.pop(0)

# --- GETTING BTCUSDC PRICE FOR SNAPSHOT
def get_btc_usdc_price():
    try:
        r = _request_with_retries("GET", f"{BASE_URL}/api/v3/ticker/price", params={"symbol": "BTCUSDC"})
        return float(r["price"])

    except Exception as e:
        logger.error(f"⚠️ BTC price fetch failed: {e}")
        raise

# --- SNAPSHOT FORMATION ---
def build_snapshot():
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
            assets_with_balance.append({"asset": asset["asset"], "balance": round(total_asset_balance, 8)})

        if asset["asset"] == "USDC":
            usdc_balance = free + locked
            usdc_borrowed = borrowed

    btc_usdc_price = get_btc_usdc_price()
    total_balance_usdc = float(acc["totalNetAssetOfBtc"]) * btc_usdc_price
    margin_level = float(acc["marginLevel"])

    # 🏷 ACCOUNT SNAPSHOT
    snapshot = {
        # ⌚ TIME
        "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),

        # 💰 BALANCE
        "totalBalanceUSDC": round(total_balance_usdc, 8),
        "usdcBalance": round(usdc_balance, 8),
        "totalDebt": round(total_debt, 8),
        "usdcBorrowed": round(usdc_borrowed, 8),
        "assetsWithBalance": assets_with_balance,

        # ⚖ RISK
        "marginLevel": margin_level,

        # 📈 ACTIVITY
        "longsToday": DAILY_LONGS,
        "shortsToday": DAILY_SHORTS,
        "totalLongs": TOTAL_LONGS,
        "totalShorts": TOTAL_SHORTS,
        "tradeId": TRADE_COUNTER,
    }

    # 🐍 VARIABLES SNAPSHOT
    snapshot["variables"] = {
        # 🎚 BOOL VARS
        "trading": TRADING,
        "testnet": TESTNET,
        "sl_override": SL_OVERRIDE,
        "tp_override": TP_OVERRIDE,

        # 🔢 ENV VARS
        "sl_pct": SL_PCT,
        "tp_pct": TP_PCT,
        "retries": RETRIES,
        "snapshot_interval": SNAPSHOT_INTERVAL,
        "max_snapshots": MAX_SNAPSHOTS,
        "log_view": LOG_VIEW,
        "login_limit": LOGIN_LIMIT,
        "login_retry": LOGIN_RETRY,
        "session_time": SESSION_TIME,
    }

    return snapshot

# --- SNAPSHOT WORKERS ---
def snapshot_worker():
    while True:
        try:
            snapshot = build_snapshot()
            store_snapshot(snapshot)
            logger.info("📸 Snapshot stored")

        except Exception as e:
            logger.error(f"⚠️ Snapshot error: {e}")
        finally:
            logger.info(f"⏱ Next snapshot in {SNAPSHOT_INTERVAL} day(s)")
            time.sleep(SNAPSHOT_INTERVAL * 86400)

# --- SNAPSHOT EXECUTION ---
try:
    start_snapshot_workers()

except Exception as e:
    logger.error(f"❌ Error starting snapshot workers: {e}")
    raise


# ====== DEPLOY LOADING ======
"""Loads Binance exchange info on startup and logs the deploy timestamp."""

# --- EXCHANGE INFO ---
EXCHANGE_INFO = None

# --- LOAD EXCANGE INFO ---
def load_exchange_info():
    global EXCHANGE_INFO

    logger.info("📡 Loading exchange info...")
    EXCHANGE_INFO = send_public_request("GET", "/api/v3/exchangeInfo")
    logger.info("ℹ Exchange info loaded")

# --- EXCHANGE INFO LOADING ---
try:
    load_exchange_info()

except Exception as e:
    logger.error(f"❌ Error loading exchange info: {e}")
    raise

# --- LOG DEPLOY ---
deploy_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
logger.info(f"🚀 Deployed at {deploy_time}")
logger.info(f"________________________________________")


# ====== MILESTONES ======
"""Detects and logs balance milestones (500, 1000, 2000... USDC) as they are reached for the first time."""

# --- MILESTONES SETTINGS ---
MILESTONES_USDC = [500, 1000, 2000, 5000, 10000, 25000, 50000]
REACHED_MILESTONES = set()

# --- CHECK MILESTONES ---
def check_milestones(total_balance_usdc: float):
    new_milestones = []

    for milestone in MILESTONES_USDC:
        if total_balance_usdc >= milestone and milestone not in REACHED_MILESTONES:
            REACHED_MILESTONES.add(milestone)
            new_milestones.append(milestone)

            # 🎉 MILESTONES LOGGER
            logger.info(
                f"🎉🎉 CONGRATS! 🎉🎉\n"
                f"💰 You reached {milestone:,.0f} USDC\n"
                f"🚀 Keep it up. Compounding is working\n" 
                f"🔥 Discipline > Luck\n"
            )

    return new_milestones


# ====== CENSORING KEYS ======
"""Redacts sensitive fields (admin_key, trading_key) from logged payloads."""

# --- SENSITIVE FIELDS ---
SENSITIVE_FIELDS = {"admin_key", "trading_key"}

# --- SANITIZE PAYLOAD ---
def sanitize_payload(payload: dict) -> dict:
    clean = payload.copy()
    for field in SENSITIVE_FIELDS:
        if field in clean:
            clean[field] = "******"
    return clean


# ====== ADMIN FUNCTIONS ======
"""Administrative operations: clear positions, borrow/repay USDC, toggle trading parameters, and restore defaults."""

# --- ADMIN CLEAR ---
def clear(symbol=None):
    if symbol:
        logger.admin(f"🔁 ADMIN ACTION: Converting {symbol} to USDC...")
        cancel(symbol)
    else:
        logger.admin("🔁 ADMIN ACTION: Converting ALL assets to USDC...")
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

        if asset_name == "USDC":
            continue

        asset_symbol = f"{asset_name}USDC"

        if symbol and asset_symbol != symbol:
            continue

        if free_qty <= 0 and locked_qty <= 0:
            continue

        try:
            get_symbol_lot(asset_symbol)

        except:
            logger.error(f"⚠️ No USDC pair for {asset_name}, skipping")
            continue

        try:
            logger.info(f"➡ Clearing {asset_name} (free={free_qty}, locked={locked_qty}, borrowed={borrowed_qty})")
            refund(asset_symbol)
            residual(asset_symbol)
            cleared_symbols.append(asset_symbol)

        except Exception as e:
            logger.error(f"⚠️ Could not convert {asset_symbol}: {e}")
            failed_symbols.append({"symbol": asset_symbol, "error": str(e)})

    logger.info("✅ CLEAR completed")
    return {"cleared": cleared_symbols, "failed": failed_symbols}

# --- ADMIN BORROW ---
def borrow(amount: float):
    logger.admin(f"📥 ADMIN ACTION: Borrow requested: {amount} USDC")

    if amount <= 0:
        raise ValueError("Borrow amount must be > 0")

    acc = get_margin_account()
    margin_level = float(acc["marginLevel"])
    logger.admin(f"🧮 Current ML: {margin_level:.2f}")

    if margin_level < ML_WARNING:
        logger.error(f"⚠️ Margin level too low for leveraging")
        raise Exception("❌ Margin level too low to safely borrow USDC")

    # 📥 LEVERAGE PARAMS
    params = {
        "asset": "USDC",
        "amount": format(amount, "f"),
        "timestamp": _now_ms()
    }

    resp = send_signed_request("POST", "/sapi/v1/margin/loan", params)
    logger.admin(f"✅ BORROW completed: {amount} USDC")
    return resp

# --- ADMIN REPAY ---
def repay(amount):
    logger.admin(f"💳 ADMIN ACTION: Repay requested: {amount}")

    if isinstance(amount, str) and amount.lower() == "all":
        margin_info = get_margin_account()

        borrowed_usdc = Decimal("0")
        for asset in margin_info["userAssets"]:
            if asset["asset"] == "USDC":
                borrowed_usdc = Decimal(asset["borrowed"])
                break

        if borrowed_usdc <= 0:
            logger.admin("ℹ️ No USDC debt to repay")
            return {"status": "nothing_to_repay"}

        amount = borrowed_usdc
        logger.admin(f"🔁 REPAY ALL → {amount} USDC")

    amount = Decimal(str(amount))

    if amount <= 0:
        raise ValueError("Repay amount must be > 0")

    # 💳 REPAY LEVERAGE PARAMS
    params = {
        "asset": "USDC",
        "amount": format(amount, "f"),
        "timestamp": _now_ms()
    }

    resp = send_signed_request("POST", "/sapi/v1/margin/repay", params)
    logger.admin(f"✅ REPAY completed: {amount} USDC")
    return resp

# --- ADMIN TRADING ---
def set_trading_state(state):
    global TRADING

    if state == "on":
        TRADING = True
        logger.admin("▶️ ADMIN ACTION: Trading RESUMED")
    elif state == "off":
        TRADING = False
        logger.admin("⏸️ ADMIN ACTION: Trading PAUSED")
    else:
        return {"status": "error", "msg": "invalid state"}

    return {"status": "ok", "trading": TRADING}

# --- ADMIN TESTNET ---
def set_testnet_state(state):
    global TESTNET

    if state == "on":
        TESTNET = True
        logger.admin("🧪 ADMIN ACTION: TESTNET MODE ENABLED")
    elif state == "off":
        TESTNET = False
        logger.admin("🌐 ADMIN ACTION: LIVE MODE ENABLED")
    else:
        return {"status": "error", "msg": "invalid state"}

    return {"status": "ok", "testnet": TESTNET}

# --- ADMIN SET SL ---
def set_sl(state=None, value=None):
    global SL_OVERRIDE, SL_PCT

    if state is not None:
        if state == "on":
            SL_OVERRIDE = True
            logger.admin("🟢 ADMIN ACTION: SL override ENABLED")
        elif state == "off":
            SL_OVERRIDE = False
            logger.admin("🔴 ADMIN ACTION: SL override DISABLED")
        else:
            return {"status": "error", "msg": "invalid state"}
        return {"status": "ok", "sl_override": SL_OVERRIDE}

    if value is not None:
        return set_var("sl_pct", value)

    return {"status": "error", "msg": "no state or value provided"}

# --- ADMIN SET TP ---
def set_tp(state=None, value=None):
    global TP_OVERRIDE, TP_PCT

    if state is not None:
        if state == "on":
            TP_OVERRIDE = True
            logger.admin("🟢 ADMIN ACTION: TP override ENABLED")
        elif state == "off":
            TP_OVERRIDE = False
            logger.admin("🔴 ADMIN ACTION: TP override DISABLED")
        else:
            return {"status": "error", "msg": "invalid state"}
        return {"status": "ok", "tp_override": TP_OVERRIDE}

    if value is not None:
        return set_var("tp_pct", value)

    return {"status": "error", "msg": "no state or value provided"}

# --- ADMIN SET VAR ---
def set_var(var_name, value):
    if var_name not in SETTABLE_VARS:
        return {"status": "error", "msg": f"unknown variable: {var_name}"}

    meta = SETTABLE_VARS[var_name]

    try:
        value = meta["type"](value)

    except Exception:
        return {"status": "error", "msg": f"invalid value for {var_name}"}

    value = max(meta["min"], min(value, meta["max"]))
    globals()[meta["var"]] = value
    logger.admin(f"🛠️ ADMIN ACTION: {meta['var']} updated → {value}")
    return {"status": "ok", var_name: value}

# --- ADMIN RESTORE ---
def restore():
    global SL_PCT, TP_PCT, RETRIES, LOG_VIEW, TRADING, TESTNET, SL_OVERRIDE, TP_OVERRIDE, LOGIN_LIMIT, LOGIN_RETRY, SESSION_TIME

    logger.admin("🛠️ ADMIN ACTION: RESTORE default trading parameters")
    TRADING = DFT_TRADING
    TESTNET = DFT_TESTNET
    SL_OVERRIDE = DFT_SL_OVERRIDE
    TP_OVERRIDE = DFT_TP_OVERRIDE
    SL_PCT = DFT_SL_PCT
    TP_PCT = DFT_TP_PCT
    RETRIES = DFT_RETRIES
    LOG_VIEW = DFT_LOG_VIEW
    LOGIN_LIMIT = DFT_LOGIN_LIMIT
    LOGIN_RETRY = DFT_LOGIN_RETRY
    SESSION_TIME = DFT_SESSION_TIME
    logger.admin(f"🔄 TRADING restored → {TRADING}")
    logger.admin(f"🔄 TESTNET restored → {TESTNET}")
    logger.admin(f"🔄 SL_OVERRIDE restored → {SL_OVERRIDE}")
    logger.admin(f"🔄 TP_OVERRIDE restored → {TP_OVERRIDE}")
    logger.admin(f"🔄 SL_PCT restored → {SL_PCT}")
    logger.admin(f"🔄 TP_PCT restored → {TP_PCT}")
    logger.admin(f"🔄 RETRIES restored → {RETRIES}")
    logger.admin(f"🔄 LOG_VIEW restored → {LOG_VIEW}")
    logger.admin(f"🔄 LOGIN_LIMIT restored → {LOGIN_LIMIT}")
    logger.admin(f"🔄 LOGIN_RETRY restored → {LOGIN_RETRY}")
    logger.admin(f"🔄 SESSION_TIME restored → {SESSION_TIME}")
    return {
        "TRADING": TRADING,
        "TESTNET": TESTNET,
        "SL_OVERRIDE": SL_OVERRIDE,
        "TP_OVERRIDE": TP_OVERRIDE,
        "status": "ok",
        "SL_PCT": SL_PCT,
        "TP_PCT": TP_PCT,
        "RETRIES": RETRIES,
        "LOG_VIEW": LOG_VIEW,
        "LOGIN_LIMIT": LOGIN_LIMIT,
        "LOGIN_RETRY": LOGIN_RETRY,
        "SESSION_TIME": SESSION_TIME,
    }

# ====== ADMIN PAYLOADS ======
"""Registry mapping admin action names to their handler functions and variables subjet to post-deploy setting."""

# --- ADMIN ACTIONS ---
ADMIN_ACTIONS = {
    "CLEAR": clear,
    "BORROW": borrow,
    "REPAY": repay,
    "TRADING": set_trading_state,
    "TESTNET": set_testnet_state,
    "SL": set_sl,
    "TP": set_tp,
    "SET": set_var,
    "RESTORE": restore,
}

# --- SETTABLE VARS ---
SETTABLE_VARS = {
    "sl_pct":       {"type": float, "min": MIN_SL_PCT,       "max": MAX_SL_PCT,       "var": "SL_PCT"},
    "tp_pct":       {"type": float, "min": MIN_TP_PCT,       "max": MAX_TP_PCT,       "var": "TP_PCT"},
    "retries":      {"type": int,   "min": MIN_RETRIES,      "max": MAX_RETRIES,      "var": "RETRIES"},
    "log_view":     {"type": int,   "min": MIN_LOG_VIEW,     "max": MAX_LOG_VIEW,     "var": "LOG_VIEW"},
    "login_limit":  {"type": int,   "min": MIN_LOGIN_LIMIT,  "max": MAX_LOGIN_LIMIT,  "var": "LOGIN_LIMIT"},
    "login_retry":  {"type": int,   "min": MIN_LOGIN_RETRY,  "max": MAX_LOGIN_RETRY,  "var": "LOGIN_RETRY"},
    "session_time": {"type": int,   "min": MIN_SESSION_TIME, "max": MAX_SESSION_TIME, "var": "SESSION_TIME"},
}


# ====== ADMIN SYSTEM ======
"""Session-based admin authentication: IP-tracked sessions with timeout, login rate limiting, and unauthorized request handling."""

# --- ADMIN PLACEHOLDERS ---
ADMIN_SESSIONS = {}
LOGIN_ATTEMPTS = {}

# --- ADMIN IP IDENTIFICATION ---
def get_ip():
    if request.headers.get("X-Forwarded-For"):
        return request.headers.get("X-Forwarded-For").split(",")[0].strip()

    return request.remote_addr

# --- ADMIN SESSION OPENING ---
def create_admin_session(ip):
    ADMIN_SESSIONS[ip] = time.time()
    logger.admin(f"🔓 Admin session opened for {ip}")

# --- ADMIN SESSION CLOSING ---
def destroy_admin_session(ip):
    if ip in ADMIN_SESSIONS:
        del ADMIN_SESSIONS[ip]
        logger.admin(f"🔐 Admin session closed for {ip}")

# --- ADMIN SESSION EXPIRING ---
def is_admin_authenticated():
    ip = get_ip()

    if ip not in ADMIN_SESSIONS:
        return False

    last_activity = ADMIN_SESSIONS[ip]

    if time.time() - last_activity > (SESSION_TIME * 60):
        logger.admin(f"🔒 Admin session expired for {ip}")
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

# --- CLEARING LOGIN ATTEMPTS ---
def reset_login_attempts(ip):
    if ip in LOGIN_ATTEMPTS:
        del LOGIN_ATTEMPTS[ip]


# ====== FLASK WEBHOOK ======
"""Webhook endpoint that receives trading signals, validates them, and dispatches trade execution in a background thread."""

# --- BACKEND ENDPOINTS ---
TRADE_LOCK = threading.RLock()

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)

    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    if not data:
        return jsonify({"error": "Empty payload"}), 400

    logger.info(f"📩 JSON received: {sanitize_payload(data)}")
    time.sleep(5)
    allowed, response = trading_guard()

    if not allowed:
        return response

    # ❓ RETURN FOR MISSING DATA
    if "symbol" not in data or "side" not in data:
        logger.error("❓ Missing trading fields")
        return jsonify({"error": "Missing trading fields"}), 400

    # 🚫 RETURN FOR INCORRECT KEY
    if TRADING_KEY:
        if data.get("trading_key") != TRADING_KEY:
            logger.error("🚫 Invalid or missing trading_key")
            return jsonify({"status": "blocked", "reason": "invalid trading key"}), 403

    executor.submit(process_trade, data)
    return jsonify({"status": "ok", "result": "accepted"}), 200

def process_trade(data):
    symbol = data["symbol"]
    side = data["side"].upper()
    start = time.time()

    try:
        with TRADE_LOCK:

            # ⛔ TRADING BLOCKING
            if not check_margin_level():
                logger.error("⛔ Trading blocked (critical margin condition)")
                return

            if TRADING_BLOCKED:
                logger.error("⛔ Trading blocked by margin safety system")
                return

            # 🧹 PRE-TRADE CLEANUP
            handle_pre_trade_cleanup(symbol)

            # 💹 MARKET BUY / SELL AND OCO
            if side == "BUY":
                resp = execute_long_margin(symbol, webhook_data=data)
                trade_id = resp.get("trade_id") if resp else "UNKNOWN"
            elif side == "SELL":
                resp = execute_short_margin(symbol, webhook_data=data)
                trade_id = resp.get("trade_id") if resp else "UNKNOWN"
            else:
                logger.error("⛔ Trading blocked due to invalid side")
                return

            # ⏳ LATENCY
            latency = time.time() - start
            logger.info(f"[TRADE {trade_id}] ⏳ Trade execution latency: {latency:.2f}s")

    except Exception as e:
        logger.error(f"🔥 CRITICAL TRADE ERROR: {e}", exc_info=True)


@app.route("/clear", methods=["GET"])
def admin_clear():
    if not is_admin_authenticated():
        return handle_unauthorized()

    symbol = request.args.get("symbol")
    threading.Thread(target=clear, args=(symbol,)).start()
    return jsonify({"status": "started"}), 200


@app.route("/borrow", methods=["GET"])
def admin_borrow():
    if not is_admin_authenticated():
        return handle_unauthorized()

    try:
        amount = float(request.args.get("amount", 0))

    except:
        return jsonify ({"error": "Invalid amount"}), 400

    try:
        result = borrow(amount)
        return jsonify({"amount": amount, "result": result}), 200

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
        result = repay(amount)
        return jsonify({"amount": amount, "result": result}), 200

    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/trading", methods=["GET"])
def admin_trading():
    if not is_admin_authenticated():
        return handle_unauthorized()

    state = request.args.get("state", "").lower()
    result = set_trading_state(state)
    return jsonify(result), 200


@app.route("/testnet", methods=["GET"])
def admin_testnet():
    if not is_admin_authenticated():
        return handle_unauthorized()

    state = request.args.get("state", "").lower()
    result = set_testnet_state(state)
    return jsonify(result), 200


@app.route("/sl", methods=["GET"])
def admin_sl():
    if not is_admin_authenticated():
        return handle_unauthorized()

    state = request.args.get("state")
    value = request.args.get("value")
    result = set_sl(state=state, value=value)
    return jsonify(result), 200


@app.route("/tp", methods=["GET"])
def admin_tp():
    if not is_admin_authenticated():
        return handle_unauthorized()

    state = request.args.get("state")
    value = request.args.get("value")
    result = set_tp(state=state, value=value)
    return jsonify(result), 200


@app.route("/set", methods=["GET"])
def admin_set():
    if not is_admin_authenticated():
        return handle_unauthorized()

    var_name = request.args.get("var")
    value = request.args.get("value")

    if not var_name or value is None:
        return jsonify({"status": "error", "msg": "missing var or value"}), 400

    result = set_var(var_name, value)
    return jsonify(result), 200


@app.route("/restore", methods=["GET"])
def admin_restore():
    if not is_admin_authenticated():
        return handle_unauthorized()

    result = restore()
    return jsonify(result), 200


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
    return jsonify({"bot_ready": BOT_READY, "trading": TRADING, "uptime_seconds": uptime, "mode": "TESTNET" if TESTNET else "LIVE"})


@app.route("/snapshot", methods=["GET"])
def admin_snapshot():
    if not is_admin_authenticated():
        return handle_unauthorized()

    snapshot = build_snapshot()
    milestones = check_milestones(snapshot["totalBalanceUSDC"])
    snapshot["milestonesReached"] = milestones
    return jsonify(snapshot), 200


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
            body.light .adam-pre{color:#cbd5e1}
            .adam-center{display:flex;flex-direction:column;align-items:center;justify-content:center;flex:1;min-width:200px;padding:0 16px}
            .logo{width:140px;margin-bottom:20px;opacity:0.95}
            .tagline{font-size:10px;letter-spacing:0.12em;color:#475569;text-transform:uppercase;margin-bottom:24px;font-family:'Courier New',monospace;text-align:center}
            .cards{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;width:100%;margin-bottom:24px}
            .card{background:#1e293b;border:0.5px solid #334155;border-radius:8px;padding:12px;text-align:center}
            body.light .card{background:#ffffff;border-color:#e2e8f0}
            .card-val{font-size:11px;font-family:'Courier New',monospace;color:#94a3b8;margin-bottom:4px}
            body.light .card-val{color:#475569}
            .card-label{font-size:9px;letter-spacing:0.06em;color:#475569;text-transform:uppercase}
            .btn-login{padding:8px 28px;font-size:11px;letter-spacing:0.06em;border:0.5px solid #334155;border-radius:6px;background:transparent;color:#94a3b8;cursor:pointer;text-decoration:none;transition:0.15s;font-family:'Inter',sans-serif;text-transform:uppercase}
            body.light .btn-login{border-color:#cbd5e1;color:#64748b}
            .btn-login:hover{background:#1e293b;color:#f1f5f9;border-color:#64748b}
            body.light .btn-login:hover{background:#e2e8f0;color:#0f172a}
            .footer{position:fixed;bottom:20px;font-size:10px;color:#1e293b;letter-spacing:0.08em;font-family:'Courier New',monospace}
            body.light .footer{color:#94a3b8}
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
                        <div class="card-val" id="mode">—</div>
                        <div class="card-label">Mode</div>
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
                                -----==+  
                        ====+*          
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
                    document.getElementById('mode').textContent = d.mode;
                    document.getElementById('mode').style.color = d.mode === 'TESTNET' ? '#fbbf24' : '#4ade80';
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
                reset_login_attempts(ip)

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
        <title>SGNT Login</title>
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
        <title>SGNT Dashboard</title>
        <link rel="icon" type="image/png" href="/static/sgnticon.png">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet">
        <style>
            *{box-sizing:border-box;margin:0;padding:0}
            body{background:#0f172a;color:#e2e8f0;font-family:'Inter',sans-serif;padding:20px;font-size:14px;transition:background 0.2s,color 0.2s}
            body.light{background:#f8fafc;color:#0f172a}
            .topbar{display:flex;justify-content:space-between;align-items:center;padding:0 0 14px;border-bottom:0.5px solid #1e293b;margin-bottom:4px}
            body.light .topbar{border-bottom-color:#e2e8f0}
            .topbar-left{display:flex;align-items:center;gap:10px}
            .topbar-right{display:flex;align-items:center;gap:8px}
            .dot{width:8px;height:8px;border-radius:50%;background:#1D9E75;display:inline-block}
            .dot.red{background:#E24B4A}
            .status-text{font-size:11px;color:#94a3b8;font-family:'Courier New',monospace}
            body.light .status-text{color:#64748b}
            .tag{font-size:10px;padding:2px 8px;border-radius:6px;border:0.5px solid;font-family:'Courier New',monospace}
            .tag-live{border-color:#1D9E75;color:#1D9E75}
            .tag-testnet{border-color:#BA7517;color:#BA7517}
            .theme-toggle{background:none;border:0.5px solid #334155;border-radius:20px;padding:4px 10px;cursor:pointer;font-size:13px;color:#94a3b8;transition:0.15s;display:flex;align-items:center;gap:6px}
            body.light .theme-toggle{border-color:#cbd5e1;color:#64748b}
            .theme-toggle:hover{background:#1e293b}
            body.light .theme-toggle:hover{background:#e2e8f0}
            .db{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;padding:12px 0}
            .card{background:#1e293b;border:0.5px solid #334155;border-radius:8px;padding:14px 16px}
            body.light .card{background:#ffffff;border-color:#e2e8f0}
            .card-title{font-size:10px;letter-spacing:0.08em;color:#64748b;text-transform:uppercase;margin-bottom:10px;border-bottom:0.5px solid #334155;padding-bottom:6px}
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
            .btn{padding:5px 12px;font-size:11px;border:0.5px solid #334155;border-radius:6px;background:#0f172a;color:#94a3b8;cursor:pointer;white-space:nowrap;transition:0.15s}
            body.light .btn{background:#f8fafc;border-color:#cbd5e1;color:#64748b}
            .btn:hover{background:#1e293b;color:#f1f5f9}
            body.light .btn:hover{background:#e2e8f0;color:#0f172a}
            .btn-danger{border-color:#7f1d1d;color:#fca5a5}
            .btn-danger:hover{background:#450a0a}
            .btn-success{border-color:#14532d;color:#86efac}
            .btn-success:hover{background:#052e16}
            .btn-minmax{padding:3px 7px;font-size:10px;border:0.5px solid #334155;border-radius:4px;background:transparent;color:#475569;cursor:pointer;font-family:'Courier New',monospace;transition:0.15s}
            .btn-minmax:hover{background:#1e293b;color:#94a3b8}
            body.light .btn-minmax{border-color:#cbd5e1;color:#94a3b8}
            body.light .btn-minmax:hover{background:#e2e8f0}
            .col-full{grid-column:1/-1}
            .col-2{grid-column:span 2}
            .section-label{font-size:10px;letter-spacing:0.08em;color:#475569;text-transform:uppercase;margin:10px 0 4px;grid-column:1/-1;padding-left:2px}
            .asset-row{display:flex;justify-content:space-between;font-size:11px;padding:2px 0;color:#94a3b8}
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

    <div class="topbar">
        <div class="topbar-left">
            <span class="dot" id="dot"></span>
            <span class="status-text" id="status-text">Loading...</span>
            <span class="tag tag-live" id="mode-tag">LIVE</span>
            <img src="/static/sgnticon.png" class="icon" alt="SGNT" onclick="window.location.href='/'" style="cursor: pointer; width: 25px;">
        </div>
        <div class="topbar-right">
            <button class="btn" onclick="loadData()">Update</button>
            <button class="btn btn-danger" onclick="doLogout()">Logout</button>
            <button class="theme-toggle" onclick="toggleTheme()" id="theme-btn">🌙</button>
        </div>
    </div>

    <div class="db">

        <div class="section-label">Account</div>

        <div class="card">
            <div class="card-title">Total Balance</div>
            <div class="metric-big" id="total-balance">—</div>
            <div class="metric-label">USDC</div>
        </div>
        <div class="card">
            <div class="card-title">Free USDC Balance</div>
            <div class="metric-big" id="usdc-balance">—</div>
            <div class="metric-label">USDC</div>
        </div>
        <div class="card">
            <div class="card-title">Margin level</div>
            <div class="metric-big" id="margin-level">—</div>
            <div class="margin-bar-bg"><div class="margin-bar-fill" id="margin-bar" style="width:0%"></div></div>
        </div>

        <div class="card">
            <div class="card-title">Debt</div>
            <div class="metric-row"><span class="k">Total</span><span class="v" id="total-debt">—</span></div>
            <div class="metric-row"><span class="k">Borrowed USDC</span><span class="v" id="usdc-borrowed">—</span></div>
        </div>
        <div class="card col-2">
            <div class="card-title">Assets with balance</div>
            <div id="assets-list"><span style="font-size:12px;color:#475569">—</span></div>
        </div>

        <div class="section-label">Trading</div>

        <div class="card">
            <div class="card-title">Activity</div>
            <div class="metric-row"><span class="k">Last Trade ID</span><span class="v" id="last-trade-id">—</span></div>
            <div class="metric-row"><span class="k">Longs Today</span><span class="v" id="longs-today">—</span></div>
            <div class="metric-row"><span class="k">Shorts Today</span><span class="v" id="shorts-today">—</span></div>
            <div class="metric-row"><span class="k">Total Longs</span><span class="v" id="total-longs">—</span></div>
            <div class="metric-row"><span class="k">Total Shorts</span><span class="v" id="total-shorts">—</span></div>
        </div>

        <div class="card">
            <div class="card-title">Control</div>
            <div class="toggle-row"><span class="toggle-label">Trading</span><label class="toggle"><input type="checkbox" id="tog-trading" onchange="callToggle('trading',this.checked)"><span class="slider"></span></label></div>
            <div class="toggle-row"><span class="toggle-label">Testnet</span><label class="toggle"><input type="checkbox" id="tog-testnet" onchange="callToggle('testnet',this.checked)"><span class="slider"></span></label></div>
            <div class="toggle-row"><span class="toggle-label">SL Override</span><label class="toggle"><input type="checkbox" id="tog-sl" onchange="callToggle('sl',this.checked)"><span class="slider"></span></label></div>
            <div class="toggle-row"><span class="toggle-label">TP Override</span><label class="toggle"><input type="checkbox" id="tog-tp" onchange="callToggle('tp',this.checked)"><span class="slider"></span></label></div>
        </div>

        <div class="card">
            <div class="card-title">Parameters</div>
            <div style="font-size:11px;color:#64748b;margin-bottom:4px">SL % <span id="sl-val" style="color:#f1f5f9">—</span></div>
            <div class="input-row">
                <input type="number" id="sl-input" placeholder="{{ MIN_SL_PCT }}–{{ MAX_SL_PCT }}" step="0.1" min="{{ MIN_SL_PCT }}" max="{{ MAX_SL_PCT }}">
                <button class="btn-minmax" onclick="setInputVal('sl-input',{{ MIN_SL_PCT }})">min</button>
                <button class="btn-minmax" onclick="setInputVal('sl-input',{{ MAX_SL_PCT }})">max</button>
                <button class="btn" onclick="setParam('sl','value',document.getElementById('sl-input').value)">Set</button>
            </div>
            <div style="font-size:11px;color:#64748b;margin:8px 0 4px">TP % <span id="tp-val" style="color:#f1f5f9">—</span></div>
            <div class="input-row">
                <input type="number" id="tp-input" placeholder="{{ MIN_TP_PCT }}–{{ MAX_TP_PCT }}" step="0.1" min="{{ MIN_TP_PCT }}" max="{{ MAX_TP_PCT }}">
                <button class="btn-minmax" onclick="setInputVal('tp-input',{{ MIN_TP_PCT }})">min</button>
                <button class="btn-minmax" onclick="setInputVal('tp-input',{{ MAX_TP_PCT }})">max</button>
                <button class="btn" onclick="setParam('tp','value',document.getElementById('tp-input').value)">Set</button>
            </div>
            <div style="font-size:11px;color:#64748b;margin:8px 0 4px">Retries <span id="retries-val" style="color:#f1f5f9">—</span></div>
            <div class="input-row">
                <input type="number" id="retries-input" placeholder="{{ MIN_RETRIES }}–{{ MAX_RETRIES }}" min="{{ MIN_RETRIES }}" max="{{ MAX_RETRIES }}">
                <button class="btn-minmax" onclick="setInputVal('retries-input',{{ MIN_RETRIES }})">min</button>
                <button class="btn-minmax" onclick="setInputVal('retries-input',{{ MAX_RETRIES }})">max</button>
                <button class="btn" onclick="setVar('retries',document.getElementById('retries-input').value)">Set</button>
            </div>
        </div>

        <div class="section-label">Operations</div>

        <div class="card">
            <div class="card-title">Borrow USDC</div>
            <div class="input-row"><input type="number" id="borrow-amt" placeholder="USDC quantity"><button class="btn btn-success" onclick="doBorrow()">Borrow</button></div>
        </div>

        <div class="card">
            <div class="card-title">Repay USDC</div>
            <div class="input-row">
                <input type="text" id="repay-amt" placeholder="Quantity (or 'all')">
                <button class="btn btn-success" onclick="doRepay()">Repay</button>
                <button class="btn" onclick="document.getElementById('repay-amt').value='all'">All</button>
            </div>
        </div>

        <div class="card">
            <div class="card-title">Clear</div>
            <div class="input-row"><input type="text" id="clear-sym" placeholder="Symbol (empty = all)"><button class="btn btn-danger" onclick="doClear()">Clear</button></div>
        </div>

        <div class="section-label">Admin</div>

        <div class="card">
            <div class="card-title">Logs</div>
            <div style="font-size:11px;color:#64748b;margin-bottom:4px">Log view <span id="log-view-val" style="color:#f1f5f9">—</span></div>
            <div class="input-row">
                <input type="number" id="log-view-input" placeholder="{{ MIN_LOG_VIEW }}–{{ MAX_LOG_VIEW }}" min="{{ MIN_LOG_VIEW }}" max="{{ MAX_LOG_VIEW }}">
                <button class="btn-minmax" onclick="setInputVal('log-view-input',{{ MIN_LOG_VIEW }})">min</button>
                <button class="btn-minmax" onclick="setInputVal('log-view-input',{{ MAX_LOG_VIEW }})">max</button>
                <button class="btn" onclick="setVar('log_view',document.getElementById('log-view-input').value)">Set</button>
            </div>
            <div style="margin-top:10px"><a href="/logs"><button class="btn" style="width:100%">See logs</button></a></div>
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
                <button class="btn" onclick="setVar('login_limit',document.getElementById('login-limit-input').value)">Set</button>
            </div>
        </div>

        <div class="card" style="display:flex;flex-direction:column;justify-content:space-between">
            <div class="card-title">System</div>
            <p style="font-size:12px;color:#94a3b8;margin-bottom:12px">Restore all parameters to default.</p>
            <button class="btn btn-danger" style="width:100%" onclick="doRestore()">Restore defaults</button>
            <div style="margin-top:8px"><a href="/metrics"><button class="btn" style="width:100%">See metrics</button></a></div>
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
                if (r.status === 403) { toast('Session expired', false); return null; }
                return await r.json();
            } catch(e) { toast('Network error', false); return null; }
        }

        async function loadData() {
            const d = await api('/snapshot');
            if (!d) return;

            const v = d.variables || {};
            const fmt = n => n != null ? parseFloat(n).toFixed(2) : '—';
            const fmtI = n => n != null ? parseInt(n) : '—';

            document.getElementById('total-balance').textContent = fmt(d.totalBalanceUSDC) + ' $';
            document.getElementById('usdc-balance').textContent = fmt(d.usdcBalance) + ' $';
            document.getElementById('total-debt').textContent = fmt(d.totalDebt) + ' $';
            document.getElementById('usdc-borrowed').textContent = fmt(d.usdcBorrowed) + ' $';

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

            const assets = d.assetsWithBalance || [];
            const NOTIONAL_THRESHOLD = 1;
            const above = assets.filter(a => a.balance >= NOTIONAL_THRESHOLD);
            const below = assets.filter(a => a.balance < NOTIONAL_THRESHOLD);
            let assetsHtml = '';
            if (above.length) {
                assetsHtml += above.map(a => `<div class="asset-row"><span>${a.asset}</span><span style="font-family:'Courier New',monospace">${a.balance}</span></div>`).join('');
            }
            if (below.length) {
                if (above.length) assetsHtml += `<div style="border-top:0.5px solid #334155;margin:4px 0;opacity:0.4"></div>`;
                assetsHtml += below.map(a => `<div class="asset-row" style="opacity:0.4"><span>${a.asset}</span><span style="font-family:'Courier New',monospace">${a.balance}</span></div>`).join('');
            }
            document.getElementById('assets-list').innerHTML = assetsHtml || '<span style="font-size:12px;color:#475569">No assets</span>';


            document.getElementById('tog-trading').checked = !!v.trading;
            document.getElementById('tog-testnet').checked = !!v.testnet;
            document.getElementById('tog-sl').checked = !!v.sl_override;
            document.getElementById('tog-tp').checked = !!v.tp_override;

            document.getElementById('sl-val').textContent = v.sl_pct != null ? v.sl_pct + '%' : '—';
            document.getElementById('tp-val').textContent = v.tp_pct != null ? v.tp_pct + '%' : '—';
            document.getElementById('retries-val').textContent = v.retries != null ? v.retries : '—';
            document.getElementById('log-view-val').textContent = v.log_view != null ? v.log_view : '—';
            document.getElementById('session-val').textContent = v.session_time != null ? v.session_time : '—';
            document.getElementById('login-limit-val').textContent = v.login_limit != null ? v.login_limit : '—';
            document.getElementById('login-retry-val').textContent = v.login_retry != null ? v.login_retry : '—';

            const live = !!v.trading;
            const testnet = !!v.testnet;
            document.getElementById('dot').className = 'dot' + (live ? '' : ' red');
            document.getElementById('status-text').textContent = live ? 'Trading active' : 'Trading paused';
            document.getElementById('mode-tag').textContent = testnet ? 'TESTNET' : 'LIVE';
            document.getElementById('mode-tag').className = 'tag ' + (testnet ? 'tag-testnet' : 'tag-live');

            const milestones = d.milestonesReached || [];
            if (milestones.length > 0) {
                showMilestone(milestones[milestones.length - 1]);
            }
        }

        async function callToggle(param, checked) {
            const state = checked ? 'on' : 'off';
            const d = await api(`/${param}?state=${state}`);
            if (d) toast(`${param} → ${state}`);
            await loadData();
        }

        async function setParam(param, key, val) {
            if (!val) { toast('Insert value', false); return; }
            const d = await api(`/${param}?${key}=${val}`);
            if (d && d.status === 'ok') toast(`${param} updated`);
            else toast('Error', false);
            await loadData();
        }

        async function setVar(varName, val) {
            if (!val && val !== 0) { toast('Insert value', false); return; }
            const d = await api(`/set?var=${varName}&value=${val}`);
            if (d && d.status === 'ok') toast(`${varName} updated`);
            else toast('Error', false);
            await loadData();
        }

        async function doBorrow() {
            const amt = document.getElementById('borrow-amt').value;
            if (!amt) { toast('Insert quantity', false); return; }
            const d = await api(`/borrow?amount=${amt}`);
            if (d) toast(`Borrow ${amt} USDC OK`);
            await loadData();
        }

        async function doRepay() {
            const amt = document.getElementById('repay-amt').value;
            if (!amt) { toast('Insert quantity or "all"', false); return; }
            const d = await api(`/repay?amount=${amt}`);
            if (d) toast(`Repay ${amt} OK`);
            await loadData();
        }

        async function doClear() {
            const sym = document.getElementById('clear-sym').value.trim();
            const url = sym ? `/clear?symbol=${sym}` : '/clear';
            const d = await api(url);
            if (d) toast(sym ? `Clear ${sym} OK` : 'Clear all OK');
            await loadData();
        }

        async function doRestore() {
            const d = await api('/restore');
            if (d) toast('Restore completed');
            await loadData();
        }

        async function doLogout() {
            await api('/logout');
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
    return render_template_string(html,
    MIN_SL_PCT=MIN_SL_PCT,
    MAX_SL_PCT=MAX_SL_PCT,
    MIN_TP_PCT=MIN_TP_PCT,
    MAX_TP_PCT=MAX_TP_PCT,
    MIN_RETRIES=MIN_RETRIES,
    MAX_RETRIES=MAX_RETRIES,
    MIN_LOG_VIEW=MIN_LOG_VIEW,
    MAX_LOG_VIEW=MAX_LOG_VIEW,
    MIN_LOGIN_LIMIT=MIN_LOGIN_LIMIT,
    MAX_LOGIN_LIMIT=MAX_LOGIN_LIMIT,
    MIN_LOGIN_RETRY=MIN_LOGIN_RETRY,
    MAX_LOGIN_RETRY=MAX_LOGIN_RETRY,
    MIN_SESSION_TIME=MIN_SESSION_TIME,
    MAX_SESSION_TIME=MAX_SESSION_TIME,
)


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
        if level:
            with open(filename, "r", encoding="utf-8") as f:
                filtered_lines = [line for line in f if level in line]

            return Response(
                "".join(filtered_lines),
                mimetype="text/plain",
                headers={"Content-Disposition": f"attachment; filename={filename}({level}).log"}
            )
        return send_file(filename, as_attachment=True, mimetype="text/plain")

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
            if LOG_VIEW == 0:
                latest_logs = []
            else:
                latest_logs = list(deque(f, maxlen=LOG_VIEW))

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>SGNT Logs</title>
        <link rel="icon" type="image/png" href="/static/sgnticon.png">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet">
        <style>
            *{box-sizing:border-box;margin:0;padding:0}
            body{background:#0f172a;color:#e2e8f0;font-family:'Inter',sans-serif;padding:20px;font-size:14px;transition:background 0.2s,color 0.2s}
            body.light{background:#f8fafc;color:#0f172a}
            .topbar{display:flex;justify-content:space-between;align-items:center;padding:0 0 14px;border-bottom:0.5px solid #1e293b;margin-bottom:16px}
            body.light .topbar{border-bottom-color:#e2e8f0}
            .topbar-left{display:flex;align-items:center;gap:12px}
            .topbar-right{display:flex;align-items:center;gap:8px}
            .topbar-title{font-size:13px;font-weight:500;color:#f1f5f9}
            body.light .topbar-title{color:#0f172a}
            .btn{padding:5px 12px;font-size:11px;border:0.5px solid #334155;border-radius:6px;background:#0f172a;color:#94a3b8;cursor:pointer;white-space:nowrap;transition:0.15s;text-decoration:none;display:inline-block}
            body.light .btn{background:#f8fafc;border-color:#cbd5e1;color:#64748b}
            .btn:hover{background:#1e293b;color:#f1f5f9}
            body.light .btn:hover{background:#e2e8f0;color:#0f172a}
            .btn-blue{border-color:#1e40af;color:#93c5fd}
            .theme-toggle{background:none;border:0.5px solid #334155;border-radius:20px;padding:4px 10px;cursor:pointer;font-size:13px;color:#94a3b8;transition:0.15s;display:flex;align-items:center;gap:6px}
            body.light .theme-toggle{border-color:#cbd5e1;color:#64748b}
            .theme-toggle:hover{background:#1e293b}
            body.light .theme-toggle:hover{background:#e2e8f0}
            .refresh-toggle{display:flex;align-items:center;gap:6px;font-size:11px;color:#64748b;font-family:'Courier New',monospace}
            .toggle{position:relative;width:28px;height:15px;cursor:pointer}
            .toggle input{opacity:0;width:0;height:0}
            .slider{position:absolute;inset:0;background:#334155;border-radius:15px;transition:0.2s}
            .slider:before{content:'';position:absolute;width:10px;height:10px;left:2px;top:2px;background:#94a3b8;border-radius:50%;transition:0.2s}
            input:checked+.slider{background:#1D9E75}
            input:checked+.slider:before{transform:translateX(13px);background:white}
            .btn-group{display:flex;gap:4px;flex-wrap:wrap}
            .filter-btn{padding:3px 10px;font-size:10px;border:0.5px solid;border-radius:4px;cursor:pointer;background:transparent;font-family:'Courier New',monospace;letter-spacing:0.04em}
            .f-info{border-color:#16a34a;color:#4ade80}
            .f-info:hover{background:#052e16}
            .f-warning{border-color:#b45309;color:#fbbf24}
            .f-warning:hover{background:#1c0f00}
            .f-error{border-color:#991b1b;color:#fca5a5}
            .f-error:hover{background:#450a0a}
            .f-admin{border-color:#6d28d9;color:#c4b5fd}
            .f-admin:hover{background:#1e0a3c}
            .f-date{border-color:#0e7490;color:#67e8f9}
            .f-date:hover{background:#001f2b}
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
        </style>
    </head>
    <body>

    <div class="topbar">
        <div class="topbar-left">
            <a href="/dashboard" class="btn">← Dashboard</a>
            <img src="/static/sgnticon.png" class="icon" alt="SGNT" onclick="window.location.href='/'" style="cursor: pointer; width: 25px;">
            <span class="topbar-title">Logs</span>
        </div>
        <div class="topbar-right">
            <div class="refresh-toggle">
                <label class="toggle">
                    <input type="checkbox" id="auto-refresh" onchange="toggleRefresh(this.checked)">
                    <span class="slider"></span>
                </label>
                <span>Auto-refresh</span>
            </div>
            <a href="/logs?download=all" class="btn btn-blue">Download all</a>
            <button class="theme-toggle" onclick="toggleTheme()" id="theme-btn">🌙</button>
        </div>
    </div>

    <div class="section-label">Live preview — last {{ preview_size }} lines</div>
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

    <script>
        let refreshInterval = null;

        function toggleRefresh(enabled) {
            if (enabled) {
                refreshInterval = setInterval(fetchLogs, 10000);
            } else {
                clearInterval(refreshInterval);
                refreshInterval = null;
            }
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
    </script>

    </body>
    </html>
    """

    return render_template_string(html, logs=log_files, preview=latest_logs, preview_size=LOG_VIEW)


@app.route("/metrics")
def metrics():
    if not is_admin_authenticated():
        return handle_unauthorized()

    if not SNAPSHOT_HISTORY:
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>SGNT Metrics</title>
            <link rel="icon" type="image/png" href="/static/sgnticon.png">
            <style>
                *{box-sizing:border-box;margin:0;padding:0}
                body{background:#0f172a;color:#94a3b8;font-family:'Inter',sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;margin:0;font-size:13px}
                img{width:120px;opacity:0.3;margin-bottom:20px}
                p{font-family:'Courier New',monospace;letter-spacing:0.06em}
            </style>
        </head>
        <body>
            <img src="/static/sgntlogo.png" alt="SGNT">
            <p>No snapshot data yet.</p>
        </body>
        </html>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>SGNT Metrics</title>
        <link rel="icon" type="image/png" href="/static/sgnticon.png">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet">
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            *{{box-sizing:border-box;margin:0;padding:0}}
            body{{background:#0f172a;color:#e2e8f0;font-family:'Inter',sans-serif;padding:20px;font-size:14px;transition:background 0.2s,color 0.2s}}
            body.light{{background:#f8fafc;color:#0f172a}}
            .topbar{{display:flex;justify-content:space-between;align-items:center;padding:0 0 14px;border-bottom:0.5px solid #1e293b;margin-bottom:16px}}
            body.light .topbar{{border-bottom-color:#e2e8f0}}
            .topbar-left{{display:flex;align-items:center;gap:12px}}
            .topbar-right{{display:flex;align-items:center;gap:8px}}
            .topbar-title{{font-size:13px;font-weight:500;color:#f1f5f9}}
            body.light .topbar-title{{color:#0f172a}}
            .logo{{height:22px;opacity:0.85;display:block}}
            .btn{{padding:5px 12px;font-size:11px;border:0.5px solid #334155;border-radius:6px;background:#0f172a;color:#94a3b8;cursor:pointer;white-space:nowrap;transition:0.15s;text-decoration:none;display:inline-block}}
            body.light .btn{{background:#f8fafc;border-color:#cbd5e1;color:#64748b}}
            .btn:hover{{background:#1e293b;color:#f1f5f9}}
            body.light .btn:hover{{background:#e2e8f0;color:#0f172a}}
            .theme-toggle{{background:none;border:0.5px solid #334155;border-radius:20px;padding:4px 10px;cursor:pointer;font-size:13px;color:#94a3b8;transition:0.15s}}
            body.light .theme-toggle{{border-color:#cbd5e1;color:#64748b}}
            .theme-toggle:hover{{background:#1e293b}}
            body.light .theme-toggle:hover{{background:#e2e8f0}}
            .chart-card{{background:#1e293b;border:0.5px solid #334155;border-radius:8px;padding:16px;margin-bottom:12px}}
            body.light .chart-card{{background:#ffffff;border-color:#e2e8f0}}
            .chart-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}}
            .chart-title{{font-size:11px;letter-spacing:0.06em;color:#64748b;text-transform:uppercase}}
            .dl-btn{{padding:3px 10px;font-size:10px;border:0.5px solid #334155;border-radius:4px;background:transparent;color:#64748b;cursor:pointer}}
            body.light .dl-btn{{border-color:#cbd5e1}}
            .dl-btn:hover{{background:#0f172a;color:#94a3b8}}
            body.light .dl-btn:hover{{background:#e2e8f0;color:#0f172a}}
            .snapshot-count{{font-size:11px;color:#475569;font-family:'Courier New',monospace}}
        </style>
    </head>
    <body>

    <div class="topbar">
        <div class="topbar-left">
            <a href="/dashboard" class="btn">← Dashboard</a>
            <img src="/static/sgnticon.png" class="icon" alt="SGNT" onclick="window.location.href='/'" style="cursor: pointer; width: 25px;">
        </div>
        <div class="topbar-right">
            <span class="snapshot-count">{len(SNAPSHOT_HISTORY)} snapshots</span>
            <button class="theme-toggle" onclick="toggleTheme()" id="theme-btn">🌙</button>
        </div>
    </div>

    <div class="chart-card">
        <div class="chart-header">
            <span class="chart-title">Balance</span>
            <button class="dl-btn" onclick="downloadChart('balanceChart')">Download</button>
        </div>
        <canvas id="balanceChart"></canvas>
    </div>

    <div class="chart-card">
        <div class="chart-header">
            <span class="chart-title">Margin level</span>
            <button class="dl-btn" onclick="downloadChart('marginChart')">Download</button>
        </div>
        <canvas id="marginChart"></canvas>
    </div>

    <div class="chart-card">
        <div class="chart-header">
            <span class="chart-title">Activity</span>
            <button class="dl-btn" onclick="downloadChart('activityChart')">Download</button>
        </div>
        <canvas id="activityChart"></canvas>
    </div>

    <script>
    const data = {json.dumps(SNAPSHOT_HISTORY)};
    const SNAPSHOT_INTERVAL = {SNAPSHOT_INTERVAL};
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
        return {{
            label,
            data: data.map(d => d[key]),
            borderColor: color,
            backgroundColor: color + '18',
            tension: 0.3,
            pointRadius: 2,
            pointHoverRadius: 4,
            fill: false
        }};
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
            dataset("Total Balance", "totalBalanceUSDC", "#22d3ee"),
            dataset("USDC Balance", "usdcBalance", "#4ade80"),
            dataset("Debt", "totalDebt", "#f87171"),
            dataset("Borrowed", "usdcBorrowed", "#fb923c")
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
                ctx.fillRect(
                    chartArea.left,
                    yTop,
                    chartArea.right - chartArea.left,
                    yBottom - yTop
                );
                ctx.restore();
            }};

            drawZone(1.25, 2, 'rgba(255, 255, 0, 0.2)');
            drawZone(1.16, 1.25, 'rgba(255, 165, 0, 0.25)');
            drawZone(1.1, 1.16, 'rgba(255, 0, 0, 0.25)');
        }}
    }};

    const marginData = data.map((d, i) => {
        const v = d.marginLevel;
        if (i > 0) {
            const prev = data[i-1].marginLevel;
            if (Math.abs(v - prev) > 100) return null;
        }
        return transform(v);
    }});

    new Chart(document.getElementById('marginChart'), {{
        type: 'line',
        data: {{
            labels,
            datasets: [{{
                label: "Margin Level",
                data: marginData,
                borderColor: "#a78bfa",
                backgroundColor: "#a78bfa18",
                tension: 0.3,
                pointRadius: 2,
                pointHoverRadius: 4,
                fill: false,
                spanGaps: false
            }}]
        }},
        options: {{
            ...commonOptions,
            scales: {{
                x: commonOptions.scales.x,
                y: {{
                    type: 'linear',
                    min: transform(1.1),
                    max: transform(1000),
                    ticks: {{
                        font: {{ size: 10 }},
                        callback: (v) => inverse(v).toFixed(2)
                    }},
                    grid: {{ color: gridColor() }}
                }}
            }}
        }},
        plugins: [backgroundZonesPlugin]
    }});

    const activityDatasets = [];
    if (SNAPSHOT_INTERVAL === 1) {{
        activityDatasets.push(
            {{type:'bar', label:'Longs Today', data: data.map(d => d.longsToday), backgroundColor:'rgba(74,222,128,0.5)', stack:'daily'}},
            {{type:'bar', label:'Shorts Today', data: data.map(d => d.shortsToday), backgroundColor:'rgba(248,113,113,0.5)', stack:'daily'}}
        );
    }}
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
                y: {{
                    beginAtZero: true,
                    stacked: SNAPSHOT_INTERVAL === 1,
                    ticks: {{ font: {{ size: 10 }}, stepSize: 1 }},
                    grid: {{ color: gridColor() }},
                    title: {{ display: true, text: 'Daily', font: {{ size: 10 }}, color: '#475569' }}
                }},
                y1: {{
                    beginAtZero: true,
                    position: 'right',
                    ticks: {{ font: {{ size: 10 }} }},
                    grid: {{ drawOnChartArea: false }},
                    title: {{ display: true, text: 'Total', font: {{ size: 10 }}, color: '#475569' }}
                }}
            }}
        }}
    }});
    </script>

    </body>
    </html>
    """


# ====== FLASK EXECUTION ======
"""Starts the Flask development server on the configured port."""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)

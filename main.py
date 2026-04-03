#   ███████╗   ██████╗   ███╗   ██╗  ████████╗
#   ██╔════╝  ██╔════╝   ████╗  ██║  ╚══██╔══╝
#   ███████╗  ██║  ███╗  ██╔██╗ ██║     ██║
#   ╚════██║  ██║   ██║  ██║╚██╗██║     ██║
#   ███████║  ╚██████╔╝  ██║ ╚████║     ██║
#   ╚══════╝   ╚═════╝   ╚═╝  ╚═══╝     ╚═╝

# ====== IMPORTS ======
import os
import io
import time
import math
import hmac
import hashlib
import zipfile
import logging
import requests
import functools
import threading
from collections import deque
from datetime import datetime
from threading import Lock, Thread
from urllib.parse import urlencode
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import TimedRotatingFileHandler
from flask import Flask, request, jsonify, redirect, url_for, send_file, render_template_string, Response


# ====== SETTINGS ======
TRADE_LOCK = threading.RLock()
print = functools.partial(print, flush=True)
app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=3)


# ====== VARIABLES ======
# --- DEFAULT VARIABLES ---
DFT_RETRIES = 3
DFT_SL_PCT = 2.0
DFT_TP_PCT = 4.0
DFT_TRADING = True
DFT_TESTNET = False
DFT_SL_OVERRIDE = True
DFT_TP_OVERRIDE = True
DFT_LOG_VIEW = 50

# --- ENVIRONMENT VARIABLES ---
LOG_VIEW = int(os.getenv("LOG_VIEW", "50"))                      # NUMBER
RETRIES = int(os.getenv("RETRIES", "3"))                         # NUMBER
SL_PCT = float(os.getenv("SL_PCT", "2"))                         # %
TP_PCT = float(os.getenv("TP_PCT", "4"))                         # %
MAX_RISK_PCT = float(os.getenv("MAX_RISK_PCT", "20"))            # %
MAX_RISK_PCT = max(0.1, min(MAX_RISK_PCT, 20))                   # %
DEFAULT_RISK_PCT = float(os.getenv("DEFAULT_RISK_PCT", "5"))     # %
DEFAULT_RISK_PCT = max(0.1, min(DEFAULT_RISK_PCT, MAX_RISK_PCT)) # %
COMMISSION = Decimal(os.getenv("COMMISSION", "0.1"))             # %
MAX_SNAPSHOTS = int(os.getenv("MAX_SNAPSHOTS", "500"))           # NUMBER
SNAPSHOT_INTERVAL = int(os.getenv("SNAPSHOT_INTERVAL", "1"))     # DAYS
BOOT_PERIOD = int(os.getenv("BOOT_PERIOD", "1"))                 # MINUTES
GRACE_PERIOD = int(os.getenv("GRACE_PERIOD", "2"))               # MINUTES
ADMIN_TIMEOUT = int(os.getenv("ADMIN_TIMEOUT", "5"))             # MINUTES
LOGIN_WINDOW = int(os.getenv("LOGIN_WINDOW", "5"))               # MINUTES
MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "5"))   # NUMBER
PORT = int(os.getenv("PORT", "5000"))                            # NUMBER

# --- TRADE COUNTER VARIABLES ---
TRADE_COUNTER = 0
DAILY_LONGS = 0
DAILY_SHORTS = 0
TOTAL_LONGS = 0
TOTAL_SHORTS = 0
CURRENT_DAY = datetime.utcnow().date()
LAST_TRADE = None

# --- BOOL VARIABLES ---
TRADING = os.getenv("TRADING", "true").lower() == "true"
TESTNET = os.getenv("TESTNET", "false").lower() == "true"
SL_OVERRIDE = os.getenv("SL_OVERRIDE", "true").lower() == "true"
TP_OVERRIDE = os.getenv("TP_OVERRIDE", "true").lower() == "true"

# --- SECRET VARIABLES ---
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
TESTNET_API_KEY = os.getenv("TESTNET_API_KEY")
TESTNET_API_SECRET = os.getenv("TESTNET_API_SECRET")
TRADING_KEY = os.getenv("TRADING_KEY")
ADMIN_KEY = os.getenv("ADMIN_KEY")


# ====== LOGGING ======
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

ADMIN_LEVEL = 25
logging.addLevelName(ADMIN_LEVEL, "ADMIN")

def admin(self, message, *args, **kwargs):
    if self.isEnabledFor(ADMIN_LEVEL):
        self._log(ADMIN_LEVEL, message, args, **kwargs)

logging.Logger.admin = admin

DATE_LEVEL = 26
logging.addLevelName(DATE_LEVEL, "DATE")

def date(self, message, *args, **kwargs):
    if self.isEnabledFor(DATE_LEVEL):
        self._log(DATE_LEVEL, message, args, **kwargs)

logging.Logger.date = date


# ====== APIS ======
# --- BINANCE / TESTNET CONFIGURATION ---
if TESTNET:
    BINANCE_API_KEY = os.getenv("TESTNET_API_KEY")
    BINANCE_API_SECRET = os.getenv("TESTNET_API_SECRET")
    BASE_URL = "https://testnet.binance.vision"

    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        logger.error("❌ Missing TESTNET API credentials")
        raise RuntimeError("Missing TESTNET API credentials")

    logger.info("🧪 Running in TESTNET mode")
    logger.info("🔐 Testnet API credentials loaded successfully")

else:
    BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
    BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
    BASE_URL = "https://api.binance.com"

    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        logger.error("❌ Missing BINANCE API credentials")
        raise RuntimeError("Missing BINANCE API credentials")

    logger.info("🌐 Running in LIVE mode")
    logger.info("🔐 Binance API credentials loaded successfully")


# ====== SAFE DEPLOYMENT PATTERN ======
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

    try:
        send_public_request("GET", "/api/v3/time")
    except Exception as e:
        logger.error(f"❌ Binance connectivity failed: {e}")
        return False

    try:
        get_balance_margin("USDC")
    except Exception as e:
        logger.error(f"❌ Account access failed: {e}")
        return False

    logger.info("✅ Health check passed")
    return True

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
    BOOT_SECS = BOOT_PERIOD * 60
    GRACE_SECS = GRACE_PERIOD * 60

    if uptime < BOOT_SECS:
        logger.info(f"⏳ Boot protection active ({int(uptime)}s/{BOOT_SECS}s)")
        return False

    if uptime < GRACE_SECS:
        logger.info(f"🟡 Deploy grace period ({int(uptime)}s/{GRACE_SECS}s)")
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
def _now_ms():
    return int(time.time() * 1000)


# ====== SIGNING AND REQUESTING ======
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
            if resp.status_code == 429:
                logger.error(f"🚫 429 RATE LIMIT hit (attempt {i+1}) → sleeping 3s")
                time.sleep(3)
                continue

            if resp.status_code == 200:
                try:
                    return resp.json()
                except Exception:
                    return resp.text
            else:
                logger.error(f"⚠️ Attempt {i+1} failed: {resp.text}")
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
    if EXCHANGE_INFO is None:
        logger.warning("⚠ Exchange info is none -> Reloading...")
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
            return {"stepSize_str": fs["stepSize"], "stepSize": float(fs["stepSize"]), "minQty": float(fs.get("minQty", 0.0)), "tickSize_str": ts["tickSize"], "tickSize": float(ts["tickSize"]), "minNotional": minNotional,}
    raise Exception(f"❌ Symbol not found: {symbol}")


# ====== SNAPSHOT METRICS ======
SNAPSHOT_HISTORY = []
SNAPSHOT_LOCK = Lock()

def start_snapshot_workers():
    Thread(target=snapshot_worker, daemon=True).start()
    logger.info("🚀 Snapshot worker started")

def store_snapshot(snapshot):
    global SNAPSHOT_HISTORY

    with SNAPSHOT_LOCK:
        clean_snapshot = {
            "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "totalBalanceUSDC": snapshot["totalBalanceUSDC"],
            "marginLevel": snapshot["marginLevel"],
            "totalDebt": snapshot["totalDebt"],
            "usdcBalance": snapshot["usdcBalance"],
            "usdcBorrowed": snapshot["usdcBorrowed"],
            "longsToday": snapshot["longsToday"],
            "shortsToday": snapshot["shortsToday"],
            "totalLongs": snapshot["totalLongs"],
            "totalShorts": snapshot["totalShorts"]
        }

        SNAPSHOT_HISTORY.append(clean_snapshot)

        if len(SNAPSHOT_HISTORY) > MAX_SNAPSHOTS:
            SNAPSHOT_HISTORY.pop(0)

def get_margin_account():
    logger.info("📊 Fetching margin account info...")
    params = {}
    acc = send_signed_request("GET", "/sapi/v1/margin/account", params)
    return acc

def get_btc_usdc_price():
    try:
        r = _request_with_retries("GET", f"{BASE_URL}/api/v3/ticker/price", params={"symbol": "BTCUSDC"})
        return float(r["price"])
    except Exception as e:
        logger.error(f"⚠️ BTC price fetch failed: {e}")
        raise

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
            assets_with_balance.append({
                "asset": asset["asset"],
                "balance": round(total_asset_balance, 8)
            })

        if asset["asset"] == "USDC":
            usdc_balance = free + locked
            usdc_borrowed = borrowed

    btc_usdc_price = get_btc_usdc_price()
    total_balance_usdc = float(acc["totalNetAssetOfBtc"]) * btc_usdc_price
    margin_level = float(acc["marginLevel"])

    snapshot = {
        "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),

        # 💰 BALANCE
        "totalBalanceUSDC": round(total_balance_usdc, 8),
        "usdcBalance": round(usdc_balance, 8),
        "totalDebt": round(total_debt, 8),
        "usdcBorrowed": round(usdc_borrowed, 8),
        "assetsWithBalance": assets_with_balance,

        # ⚖️ RISK
        "marginLevel": margin_level,

        # 📈 ACTIVITY
        "longsToday": DAILY_LONGS,
        "shortsToday": DAILY_SHORTS,
        "totalLongs": TOTAL_LONGS,
        "totalShorts": TOTAL_SHORTS,
        "tradeId": TRADE_COUNTER,
    }

    return snapshot

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

try:
    start_snapshot_workers()
except Exception as e:
    logger.error(f"❌ Error starting snapshot workers: {e}")
    raise


# ====== DEPLOY LOADING ======
# --- EXCHANGE INFO ---
EXCHANGE_INFO = None

# --- LOAD EXCANGE INFO ---
def load_exchange_info():
    global EXCHANGE_INFO

    logger.info("📡 Loading exchange info...")
    EXCHANGE_INFO = send_public_request("GET", "/api/v3/exchangeInfo")
    logger.info("✅ Exchange info loaded")

try:
    load_exchange_info()
except Exception as e:
    logger.error(f"❌ Error loading exchange info: {e}")
    raise

# --- LOG DEPLOY ---
now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
logger.info(f"🚀 Deployed at {now}")
logger.info(f"________________________________________")


# ====== TRADE COUNTER ======
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
TRADING_BLOCKED = False
MARGIN_MAX_RISK_PCT = MAX_RISK_PCT

def check_margin_level():
    global TRADING_BLOCKED, MARGIN_MAX_RISK_PCT

    try:
        account_info = get_margin_account()
        margin_level = float(account_info["marginLevel"])
        logger.info(f"🧮 Current Margin Level: {margin_level:.2f}")

        # ⚰ FORCED LIQUIDATION
        if margin_level <= 1.1001:
            logger.warning("⚰ Your account got liquidated")
            TRADING_BLOCKED = True
            clear()
            return False

        # 🚨 CRITICAL — CONTROLLED LIQUIDATION
        if margin_level < 1.16:
            logger.warning("🚨 CRITICAL! Margin < 1.16 — EXECUTING CONTROLLED LIQUIDATION")
            TRADING_BLOCKED = True
            clear()
            return False

        # 🔴 EMERGENCY — BLOCK NEW ENTRIES
        elif margin_level < 1.25:
            logger.warning("🔴 DANGER! Margin < 1.25 — BLOCKING NEW ENTRIES")
            TRADING_BLOCKED = True
            return True

        # 🟠 DEFENSIVE — LIMIT MAX RISK
        elif margin_level < 2:
            logger.warning("🟠 WARNING! Margin < 2 — LIMITING MAX RISK TO 2%")
            MARGIN_MAX_RISK_PCT = 2
            return True

        # 🟢 HEALTHY
        else:
            if TRADING_BLOCKED:
                logger.info("✅ Margin recovered — resuming normal operation")

            TRADING_BLOCKED = False
            MARGIN_MAX_RISK_PCT = MAX_RISK_PCT
            logger.info("✅ Margin level healthy")
            return True

    except Exception as e:
        logger.error(f"⚠️ Could not fetch margin level: {e}")
        return True


# ====== FINAL RISK RESOLUTION ======
def resolve_risk_pct(webhook_data=None):

    risk_pct = DEFAULT_RISK_PCT

    if webhook_data and "risk_pct" in webhook_data:
        try:
            risk_pct = float(webhook_data["risk_pct"])
        except Exception:
            logger.error("⚠️ Invalid risk_pct from webhook")

    risk_pct = min(risk_pct, MARGIN_MAX_RISK_PCT)
    return risk_pct / 100


# ====== PRE-TRADE CLEANUP ======
def handle_pre_trade_cleanup(symbol: str):
    base_asset = symbol.replace("USDC", "")
    logger.info(f"🔄 Cleaning previous environment for {symbol}...")

    # === 1️⃣ Cancel pending orders ===
    try:
        params = {"symbol": symbol, "timestamp": _now_ms()}
        send_signed_request("DELETE", "/sapi/v1/margin/openOrders", params)
        logger.info(f"🧹 Pending orders for {symbol} canceled")
    except Exception as e:
        if "Unknown order sent" in str(e):
            logger.info(f"ℹ️ No open orders to cancel for {symbol}")
        else:
            logger.error(f"⚠️ Couldn't cancel orders for {symbol}: {e}")


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
            logger.error(f"ℹ️ {base_asset} not present in margin account")
            return

        borrowed   = float(asset_data["borrowed"])
        free_base = float(asset_data["free"])
        free_usdc = float(usdc_data["free"]) if usdc_data else 0.0

        if borrowed <= 0:
            logger.info(f"✅ No active debt in {base_asset}")
        else:
            logger.info(f"💳 Active debt detected: {borrowed} {base_asset}")

            missing = borrowed - free_base

            if missing > 0:
                lot = get_symbol_lot(symbol)

                BUFFER = 1.02
                buy_qty = missing * BUFFER

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

                buy_params = {"symbol": symbol, "side": "BUY", "type": "MARKET", "quantity": qty_str, "timestamp": _now_ms()}
                send_signed_request("POST", "/sapi/v1/margin/order", buy_params)
                logger.info(f"🛒 Bought {qty_str} {base_asset} to reduce debt")

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
                logger.info(f"💰 Repay executed: {repay_amount} {base_asset}")

            remaining = borrowed - repay_amount
            if remaining > 0:
                logger.info(f"⚠️ Remaining debt after repay: {remaining:.8f} {base_asset}")
            else:
                logger.info(f"✅ Debt fully cleared for {base_asset}")

    except Exception as e:
        logger.error(f"⚠️ Error during repay in {base_asset}: {e}")

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
            logger.info(f"ℹ️ No residual {base_asset} to sell")
            return

        qty_str = floor_to_step_str(free, lot["stepSize_str"])
        if float(qty_str) <= 0:
            logger.info(f"ℹ️ Residual {base_asset} too small to sell")
            return

        sell_params = {"symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": qty_str, "timestamp": _now_ms()}
        send_signed_request("POST", "/sapi/v1/margin/order", sell_params)
        logger.info(f"🧹 Sold residual {qty_str} {base_asset} to USDC")

    except Exception as e:
        logger.error(f"⚠️ Error selling residual {base_asset}: {e}")


# ====== MAIN FUNCTIONS ======
# --- MARGIN LONG ---
def execute_long_margin(symbol, webhook_data=None):
    lot = get_symbol_lot(symbol)
    balance_usdc = get_balance_margin("USDC")
    risk_pct = resolve_risk_pct(webhook_data)
    qty_quote = balance_usdc * risk_pct
    params = {"symbol": symbol, "side": "BUY", "type": "MARKET", "quoteOrderQty": format(qty_quote, "f"), "timestamp": _now_ms()}
    resp = send_signed_request("POST", "/sapi/v1/margin/order", params)
    executed_qty, entry_price = extract_execution_info(resp)
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

    params = {"symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": qty_str, "timestamp": _now_ms()}
    resp = send_signed_request("POST", "/sapi/v1/margin/order", params)
    executed_qty, entry_price = extract_execution_info(resp)
    trade_id = next_trade_id()
    global DAILY_SHORTS, TOTAL_SHORTS
    DAILY_SHORTS += 1
    TOTAL_SHORTS += 1
    side = "SELL"
    handle_post_trade(symbol, side, resp, lot, webhook_data, trade_id)
    return {"order": resp, "trade_id": trade_id}

# --- BORROW ---
def borrowing(raw_qty, lot, price_est, symbol):
    borrow_amount = float(raw_qty.quantize(Decimal(str(lot["stepSize_str"])), rounding=ROUND_DOWN))

    if borrow_amount <= 0 or borrow_amount < lot.get("minQty", 0.0):
        raise Exception(f"Qty {borrow_amount} < minQty")

    if (borrow_amount * price_est) < lot.get("minNotional", 0.0):
        raise Exception("Notional too small")

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
        logger.warning(f"[TRADE {trade_id}] ⚠️ No execution detected")

    emoji = "📈" if side == "BUY" else "📉"
    logger.info(f"[TRADE {trade_id}] {emoji} {side} executed {symbol}: qty={executed_qty} (spent≈{(entry_price * executed_qty) if entry_price is not None else 'unknown'} USDC)")

    if executed_qty > 0 and entry_price:
        sl_from_web = None
        tp_from_web = None

        if webhook_data:
            sl_from_web = webhook_data.get("sl")
            tp_from_web = webhook_data.get("tp")

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

    if executed_qty > 0:
        update_last_trade(symbol, side)

    return executed_qty, entry_price

# --- LAST TRADE SHOWING ---
def update_last_trade(symbol: str, side: str):
    global LAST_TRADE

    LAST_TRADE = {
        "symbol": symbol,
        "side": side,
        "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    }


# ====== SL/TP FUNCTIONS ======
def place_sl_tp_margin(symbol: str, side: str, entry_price: float, executed_qty: float, lot: dict, sl_override=None, tp_override=None, trade_id=None):
    try:
        COMMISSION_BUFFER = Decimal("1") - (COMMISSION / Decimal("100"))
        oco_side = "SELL" if side == "BUY" else "BUY"

        # === Determine if SL/TP should be used ===
        use_sl = sl_override is not None or (SL_OVERRIDE and SL_PCT is not None)
        use_tp = tp_override is not None or (TP_OVERRIDE and TP_PCT is not None)

        if not use_sl and not use_tp:
            logger.info(f"[TRADE {trade_id}] ℹ️ No SL/TP requested for {symbol}")
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
            decimals = 8

        # === Align SL/TP to tickSize ===
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
                logger.error(f"⚠️ Skipping {label} for {symbol}: price {price_f} < tickSize {lot['tickSize']}")
                return False
            notional = price_f * qty_f
            if notional < lot.get("minNotional", 0.0):
                logger.error(f"⚠️ Skipping {label} for {symbol}: notional {notional:.8f} < minNotional {lot.get('minNotional')}")
                return False

        # === Place OCO ===
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
                logger.info(f"[TRADE {trade_id}] 📌 OCO placed for {symbol}: TP={tp_price_str}, SL={sl_price_str}, stopLimit={stop_limit_price} ({oco_side}), qty={qty_str}")
                logger.info(f"[TRADE {trade_id}] 🟢 TP PnL ≈ {profit_tp:.2f} USDC | 🔴 SL PnL ≈ {loss_sl:.2f} USDC | ⚖️ R:R {rr:.2f}")
                return True
            except Exception as e:
                logger.error(f"⚠️ Failed OCO for {symbol}, payload={params}: {e}")
                return False

        # === SL ONLY ===
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
            logger.info(f"[TRADE {trade_id}] 🛑 SL placed for {symbol}: stop={sl_price_str}, limit={stop_limit_price}, qty={qty_str}")
            return True

        # === TP ONLY ===
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
            logger.info(f"[TRADE {trade_id}] 🎯 TP placed for {symbol}: price={tp_price_str}, qty={qty_str}")
            return True

    except Exception as e:
        logger.error(f"⚠️ Could not place SL/TP for {symbol}: {e}")
        return False


# ====== MILESTONES ======
MILESTONES_USDC = [500, 1000, 2000, 5000, 10000, 25000, 50000]
REACHED_MILESTONES = set()

def check_milestones(total_balance_usdc: float):
    new_milestones = []

    for milestone in MILESTONES_USDC:
        if total_balance_usdc >= milestone and milestone not in REACHED_MILESTONES:
            REACHED_MILESTONES.add(milestone)
            new_milestones.append(milestone)

            logger.info(
                f"🎉🎉 CONGRATS! 🎉🎉\n"
                f"💰 You reached {milestone:,.0f} USDC\n"
                f"🚀 Keep it up. Compounding is working.\n"
                f"🔥 Discipline > Luck\n"
            )

    return new_milestones


# ====== CENSORING KEYS ======
SENSITIVE_FIELDS = {"admin_key", "trading_key"}

def sanitize_payload(payload: dict) -> dict:
    clean = payload.copy()
    for field in SENSITIVE_FIELDS:
        if field in clean:
            clean[field] = "***REDACTED***"
    return clean


# ====== ADMIN FUNCTIONS ======
def clear(symbol=None):
    if symbol:
        logger.admin(f"🔁 Converting {symbol} to USDC...")
    else:
        logger.admin("🔁 Converting ALL assets to USDC...")

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
            get_symbol_lot(asset_symbol)
        except:
            logger.warning(f"⚠️ No USDC pair for {asset_name}, skipping")
            continue

        try:
            logger.admin(f"↪ Clearing {free_qty} {asset_name}")
            handle_pre_trade_cleanup(asset_symbol)
            cleared_symbols.append(asset_symbol)

        except Exception as e:
            logger.error(f"⚠️ Could not convert {asset_symbol}: {e}")
            failed_symbols.append({"symbol": asset_symbol, "error": str(e)})

    logger.admin("✅ CLEAR completed")
    return {"cleared": cleared_symbols, "failed": failed_symbols}

def read():
    logger.admin("📊 Reading Cross Margin account snapshot...")
    snapshot = build_snapshot()
    logger.admin("────────── 📊 ACCOUNT VARIABLES 📊 ──────────")
    logger.admin(f"├─ 🤖 Trading              : {TRADING}")
    logger.admin(f"├─ 🧪 Testnet Mode         : {TESTNET}")
    logger.admin(f"├─ 🟥 Stop Loss Override   : {SL_OVERRIDE}")
    logger.admin(f"├─ 🟩 Take Profit Override : {TP_OVERRIDE}")
    logger.admin(f"├─ 🔴 Stop Loss Value      : {SL_PCT} %")
    logger.admin(f"├─ 🟢 Take Profit Value    : {TP_PCT} %")
    logger.admin(f"├─ 🔄 Retries Value        : {RETRIES}")
    logger.admin(f"├─ 📸 Snapshot Interval    : Every {SNAPSHOT_INTERVAL} day(s)")
    logger.admin("__________ 📊 TRADING STATUS 📊 ──────────")
    logger.admin(f"├─ 📢 Last Trade ID        : Trade No. {snapshot['tradeId']}")
    logger.admin(f"├─ 📈 Longs Today          : {snapshot['longsToday']}")
    logger.admin(f"├─ 📉 Shorts Today         : {snapshot['shortsToday']}")
    logger.admin(f"├─ 📈 Total Longs          : {snapshot['totalLongs']}")
    logger.admin(f"├─ 📉 Total Shorts         : {snapshot['totalShorts']}")
    logger.admin(f"├─ ⌚ Last Trade           : {LAST_TRADE}")
    logger.admin("────────── 📊 ACCOUNT SNAPSHOT 📊 ──────────")
    logger.admin(f"├─ 💰 Total Balance (USDC) : {snapshot['totalBalanceUSDC']:.8f} $")
    logger.admin(f"├─ 💵 USDC Balance         : {snapshot['usdcBalance']:.8f} $")
    logger.admin(f"├─ 💳 USDC Borrowed        : {snapshot['usdcBorrowed']:.8f} $")
    logger.admin(f"├─ 💸 Total Debt           : {snapshot['totalDebt']:.8f} $")
    logger.admin(f"├─ ⚖️ Margin Level         : {snapshot['marginLevel']}")

    if snapshot["totalDebt"] == 0 and snapshot["marginLevel"] == 999.00:
        logger.admin("├─ ✅ No debt")

    logger.admin(f"├─ 🧾 Assets with balance:")
    for a in snapshot['assetsWithBalance']:
        logger.admin(f"│   ├─ {a['asset']} : {a['balance']}")

    logger.admin("──────────────────────────────────────────────")

    milestones = check_milestones(snapshot["totalBalanceUSDC"])
    snapshot["milestonesReached"] = milestones
    return snapshot

def borrow(amount: float):
    logger.admin(f"📥 ADMIN BORROW requested: {amount} USDC")

    if amount <= 0:
        raise ValueError("Borrow amount must be > 0")

    acc = get_margin_account()
    margin_level = float(acc["marginLevel"])
    logger.admin(f"🧮 Current Margin Level: {margin_level:.2f}")

    if margin_level < 2:
        raise Exception("❌ Margin level too low to safely borrow USDC")

    params = {"asset": "USDC", "amount": format(amount, "f"), "timestamp": _now_ms()}
    resp = send_signed_request("POST", "/sapi/v1/margin/loan", params)
    logger.admin(f"✅ BORROW completed: {amount} USDC")
    return resp

def repay(amount):
    logger.admin(f"💳 ADMIN REPAY requested: {amount}")
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

    params = {"asset": "USDC", "amount": format(amount, "f"), "timestamp": _now_ms()}
    resp = send_signed_request("POST", "/sapi/v1/margin/repay", params)
    logger.admin(f"✅ REPAY completed: {amount} USDC")
    return resp

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

def set_sl(state=None, value=None):
    global SL_OVERRIDE, SL_PCT

    if state is not None:

        if state == "on":
            SL_OVERRIDE = True
            logger.admin("🟢 SL override ENABLED")
        elif state == "off":
            SL_OVERRIDE = False
            logger.admin("🔴 SL override DISABLED")
        else:
            return {"status": "error", "msg": "invalid state"}

        return {"status": "ok", "sl_override": SL_OVERRIDE}

    if value is not None:

        try:
            value = float(value)
        except:
            return {"status": "error", "msg": "invalid SL value"}

        SL_PCT = max(0.1, min(value, 50))
        logger.admin(f"🛠️ ADMIN ACTION: SL value updated → {SL_PCT}")
        return {"status": "ok", "sl_value": SL_PCT}

    return {"status": "error", "msg": "no state or value provided"}

def set_tp(state=None, value=None):
    global TP_OVERRIDE, TP_PCT

    if state is not None:

        if state == "on":
            TP_OVERRIDE = True
            logger.admin("🟢 TP override ENABLED")
        elif state == "off":
            TP_OVERRIDE = False
            logger.admin("🔴 TP override DISABLED")
        else:
            return {"status": "error", "msg": "invalid state"}

        return {"status": "ok", "tp_override": TP_OVERRIDE}

    if value is not None:

        try:
            value = float(value)
        except:
            return {"status": "error", "msg": "invalid TP value"}

        TP_PCT = max(0.1, min(value, 50))
        logger.admin(f"🛠️ ADMIN ACTION: TP value updated → {TP_PCT}")
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
        logger.admin(f"🛠️ ADMIN ACTION: RETRIES value updated → {RETRIES}")
        return {"status": "ok", "retries_value": RETRIES}

    return {"status": "error", "msg": "no state or value provided"}

def set_log_view(value=None):
    global LOG_VIEW

    if value is not None:

        try:
            value = int(value)
        except:
            return {"status": "error", "msg": "invalid LOG_VIEW value"}

        LOG_VIEW = max(0, min(value, 80))
        logger.admin(f"🛠️ ADMIN ACTION: LOG_VIEW value updated → {LOG_VIEW}")
        return {"status": "ok", "log_view_value": LOG_VIEW}

    return {"status": "error", "msg": "no state or value provided"}

def restore():
    global RETRIES, SL_PCT, TP_PCT, TRADING, TESTNET, SL_OVERRIDE, TP_OVERRIDE, LOG_VIEW

    logger.admin("🛠️ ADMIN ACTION: RESTORE default trading parameters")
    RETRIES = DFT_RETRIES
    SL_PCT = DFT_SL_PCT
    TP_PCT = DFT_TP_PCT
    TRADING = DFT_TRADING
    TESTNET = DFT_TESTNET
    SL_OVERRIDE = DFT_SL_OVERRIDE
    TP_OVERRIDE = DFT_TP_OVERRIDE
    LOG_VIEW = DFT_LOG_VIEW
    logger.admin(f"🔄 RETRIES restored → {RETRIES}")
    logger.admin(f"🔄 SL_PCT restored → {SL_PCT}")
    logger.admin(f"🔄 TP_PCT restored → {TP_PCT}")
    logger.admin(f"🔄 TRADING restored → {TRADING}")
    logger.admin(f"🔄 TESTNET restored → {TESTNET}")
    logger.admin(f"🔄 SL_OVERRIDE restored → {SL_OVERRIDE}")
    logger.admin(f"🔄 TP_OVERRIDE restored → {TP_OVERRIDE}")
    logger.admin(f"🔄 LOG_VIEW restored → {LOG_VIEW}")
    return {"status": "ok", "RETRIES": RETRIES, "SL_PCT": SL_PCT, "TP_PCT": TP_PCT, "TRADING": TRADING, "TESTNET": TESTNET, "SL_OVERRIDE": SL_OVERRIDE, "TP_OVERRIDE": TP_OVERRIDE, "LOG_VIEW": LOG_VIEW}


# ====== ADMIN ACTIONS ======
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
    "LOG_VIEW": set_log_view,
}


# ====== ADMIN SYSTEM ======
# --- ADMIN PLACEHOLDERS ---
ADMIN_SESSIONS = {}
LOGIN_ATTEMPTS = {}

# --- ADMIN SESSIONS ---
def get_ip():
    if request.headers.get("X-Forwarded-For"):
        return request.headers.get("X-Forwarded-For").split(",")[0].strip()
    return request.remote_addr

def create_admin_session(ip):
    ADMIN_SESSIONS[ip] = time.time()
    logger.admin(f"🔓 Admin session opened for {ip}")

def destroy_admin_session(ip):
    if ip in ADMIN_SESSIONS:
        del ADMIN_SESSIONS[ip]
        logger.admin(f"🔒 Admin session closed for {ip}")

def is_admin_authenticated():
    ip = get_ip()

    if ip not in ADMIN_SESSIONS:
        return False

    last_activity = ADMIN_SESSIONS[ip]

    if time.time() - last_activity > (ADMIN_TIMEOUT * 60):
        logger.admin(f"🔒 Admin session expired for {ip}")
        del ADMIN_SESSIONS[ip]
        return False

    ADMIN_SESSIONS[ip] = time.time()
    return True

def handle_unauthorized():
    if "text/html" in request.headers.get("Accept", ""):
        return redirect(url_for("login"))
    else:
        return jsonify({"error": "unauthorized"}), 403

# --- LOGIN ATTEMPTS ---
def is_rate_limited(ip):
    now = time.time()
    attempts = LOGIN_ATTEMPTS.get(ip, [])

    attempts = [t for t in attempts if now - t < (LOGIN_WINDOW * 60)]
    attempts.append(now)
    LOGIN_ATTEMPTS[ip] = attempts

    return len(attempts) > MAX_LOGIN_ATTEMPTS

def reset_login_attempts(ip):
    if ip in LOGIN_ATTEMPTS:
        del LOGIN_ATTEMPTS[ip]


# ====== FLASK WEBHOOK ======
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    if not data:
        return jsonify({"error": "Empty payload"}), 400

    logger.info(f"📩 Webhook received: {sanitize_payload(data)}")
    allowed, response = trading_guard()

    if not allowed:
        return response

    if "symbol" not in data or "side" not in data:
        logger.error("❓ Missing trading fields")
        return jsonify({"error": "Missing trading fields"}), 400

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

            logger.info("🔒 TRADE LOCK ACQUIRED")

            if not check_margin_level():
                logger.admin("⛔ Trading blocked (critical margin condition)")
                return

            if TRADING_BLOCKED:
                logger.admin("⛔ Trading blocked by margin safety system")
                return

            handle_pre_trade_cleanup(symbol)

            if side == "BUY":
                resp = execute_long_margin(symbol, webhook_data=data)
                trade_id = resp.get("trade_id") if resp else "UNKNOWN"
            elif side == "SELL":
                resp = execute_short_margin(symbol, webhook_data=data)
                trade_id = resp.get("trade_id") if resp else "UNKNOWN"
            else:
                logger.error("⛔ Trading blocked due to invalid side")
                return

            latency = time.time() - start
            logger.info(f"[TRADE {trade_id}] ⏳ Trade execution latency: {latency:.2f}s")
            logger.info(f"[TRADE {trade_id}] 🔓 TRADE LOCK RELEASED")
    except Exception as e:
        logger.error(f"🔥 CRITICAL TRADE ERROR: {e}", exc_info=True)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":

        ip = get_ip()
        now = time.time()

        attempts = LOGIN_ATTEMPTS.get(ip, [])
        attempts = [t for t in attempts if now - t < (LOGIN_WINDOW * 60)]
        LOGIN_ATTEMPTS[ip] = attempts

        if len(attempts) >= MAX_LOGIN_ATTEMPTS:
            retry_after = int((LOGIN_WINDOW * 60) - (now - attempts[0]))

            if request.is_json:
                return jsonify({
                    "error": "Too many login attempts",
                    "retry_after": retry_after
                }), 429
            else:
                error = f"Too many attempts. Try again in {retry_after}s."
                return render_template_string(html, error=error)

        if request.is_json:
            data = request.get_json()
            admin_key = data.get("admin_key")
        else:
            admin_key = request.form.get("admin_key")

        if admin_key == ADMIN_KEY:
            create_admin_session(ip)
            reset_login_attempts(ip)

            if request.is_json:
                return jsonify({"status": "authorized"}), 200

            return redirect(url_for("dashboard"))

        else:
            attempts.append(now)
            LOGIN_ATTEMPTS[ip] = attempts

            if request.is_json:
                return jsonify({"error": "Invalid admin key"}), 401

            error = "Invalid admin key"

    html = """
    <html>
    <head>
        <title>SGNT Login</title>
        <link rel="icon" type="image/png" href="/static/sgnticon.png">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet">
        <style>
            body {
                background: linear-gradient(135deg, #0f172a, #020617);
                color: white;
                font-family: 'Inter', sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
            }

            .login-box {
                background: #1e293b;
                padding: 40px;
                border-radius: 15px;
                box-shadow: 0 0 40px rgba(0,0,0,0.6);
                width: 300px;
                text-align: center;
            }

            h1 {
                margin-bottom: 20px;
                font-weight: 600;
            }

            input {
                width: 100%;
                padding: 12px;
                margin-top: 10px;
                border-radius: 8px;
                border: none;
                background: #0f172a;
                color: white;
            }

            button {
                margin-top: 20px;
                width: 100%;
                padding: 12px;
                border: none;
                border-radius: 8px;
                background: #3b82f6;
                color: white;
                font-weight: 600;
                cursor: pointer;
            }

            button:hover {
                background: #2563eb;
            }

            .error {
                color: #ef4444;
                margin-top: 10px;
                font-size: 14px;
            }
        </style>
    </head>
    <body>

        <form method="POST" class="login-box">
            <h1>🔐 SGNT</h1>

            <input type="password" name="admin_key" placeholder="Enter Admin Key" required>

            <button type="submit">Login</button>

            {% if error %}
                <div class="error">{{ error }}</div>
            {% endif %}
        </form>

    </body>
    </html>
    """

    return render_template_string(html, error=error)


@app.route("/clear", methods=["GET"])
def admin_clear():
    if not is_admin_authenticated():
        return handle_unauthorized()

    symbol = request.args.get("symbol")
    result = clear(symbol)
    return jsonify({"status": "ok", "result": result}), 200


@app.route("/read", methods=["GET"])
def admin_read():
    if not is_admin_authenticated():
        return handle_unauthorized()

    snapshot = read()
    return jsonify(snapshot), 200


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


@app.route("/retries", methods=["GET"])
def admin_retries():
    if not is_admin_authenticated():
        return handle_unauthorized()

    value = request.args.get("value")
    result = set_retries(value)
    return jsonify(result), 200


@app.route("/log_view", methods=["GET"])
def admin_log_view():
    if not is_admin_authenticated():
        return handle_unauthorized()

    value = request.args.get("value")
    result = set_log_view(value)
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
    return jsonify({"status": "logged_out"}), 200


@app.route("/health", methods=["GET"])
def health():
    uptime = int(time.time() - BOOT_TIME)
    return jsonify({"bot_ready": BOT_READY, "trading": TRADING, "uptime_seconds": uptime, "mode": "TESTNET" if TESTNET else "LIVE"})


@app.route("/dashboard")
def dashboard():
    if not is_admin_authenticated():
        return handle_unauthorized()

    html = """
    <html>
    <head>
        <title>SGNT Dashboard</title>
        <link rel="icon" type="image/png" href="/static/sgnticon.png">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet">
        <style>
            body {
                background-color: #0f172a;
                color: white;
                font-family: 'Inter', sans-serif;
                text-align: center;
            }
            .container {
                margin-top: 100px;
            }
            button {
                padding: 15px 30px;
                margin: 20px;
                font-size: 18px;
                background: #3b82f6;
                border: none;
                color: white;
                border-radius: 10px;
                cursor: pointer;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📊 SGNT Control Panel</h1>

            <a href="/metrics">
                <button>📈 Metrics</button>
            </a>

            <a href="/logs">
                <button>🧾 Logs</button>
            </a>
        </div>
    </body>
    </html>
    """
    return html


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

        return send_file(
            memory_file,
            as_attachment=True,
            download_name="sgnt_logs.zip",
            mimetype="application/zip"
        )

    if filename:
        if not filename.endswith(".log"):
            return {"error": "invalid file type"}, 400

        if not os.path.isfile(filename):
            return {"error": "file not found"}, 404

        if level:
            with open(filename, "r", encoding="utf-8") as f:
                filtered_lines = (line for line in f if level in line)

            return Response(
                "".join(filtered_lines),
                mimetype="text/plain",
                headers={
                    "Content-Disposition":
                    f"attachment; filename={filename}({level}).log"
                }
            )

        return send_file(
            filename,
            as_attachment=True,
            mimetype="text/plain"
        )


    log_files = []

    for f in os.listdir("."):
        if f.endswith(".log") and os.path.isfile(f):
            size_mb = os.path.getsize(f) / (1024 * 1024)
            modified = os.path.getmtime(f)

            log_files.append({
                "name": f,
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
    <html>
    <head>
        <title>SGNT Logs</title>
        <link rel="icon" type="image/png" href="/static/sgnticon.png">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet">
        <style>
            body {
                background-color: #0f172a;
                color: white;
                font-family: 'Inter', sans-serif;
                padding: 20px;
            }

            table {
                width: 100%;
                border-collapse: collapse;
                margin-top: 30px;
            }

            th, td {
                border: 1px solid #334155;
                padding: 8px;
                text-align: center;
            }

            th {
                background-color: #1e293b;
            }

            button {
                padding: 5px 10px;
                margin: 2px;
                border: none;
                border-radius: 5px;
                cursor: pointer;
            }

            .download { background: #3b82f6; color: white; }
            .info { background: #22c55e; }
            .warning { background: #eab308; }
            .error { background: #ef4444; }
            .admin { background: #8b5cf6; }
            .date { background: #06b6d4; }

            pre {
                background: #1e293b;
                padding: 15px;
                border-radius: 10px;
                max-height: 400px;
                overflow-y: scroll;
            }
        </style>
    </head>
    <body>

    <h1>🧾 SGNT Logs</h1>

    <a href="/logs?download=all">
        <button class="download">📦 Download ALL logs</button>
    </a>

    <h2>🔥 Latest Log Preview (last {{ preview_size }} lines)</h2>
    <pre>
{% for line in preview %}
{{ line }}
{% endfor %}
    </pre>

    <h2>📁 Log Files</h2>

    <table>
        <tr>
            <th>File</th>
            <th>Size</th>
            <th>Modified</th>
            <th>Download</th>
            <th>Filters</th>
        </tr>

        {% for log in logs %}
        <tr>
            <td>{{ log.name }}</td>
            <td>{{ log.size }}</td>
            <td>{{ log.modified }}</td>

            <td>
                <a href="/logs?file={{ log.name }}">
                    <button class="download">Download</button>
                </a>
            </td>

            <td>
                <a href="/logs?file={{ log.name }}&level=INFO">
                    <button class="info">INFO</button>
                </a>

                <a href="/logs?file={{ log.name }}&level=WARNING">
                    <button class="warning">WARNING</button>
                </a>

                <a href="/logs?file={{ log.name }}&level=ERROR">
                    <button class="error">ERROR</button>
                </a>

                <a href="/logs?file={{ log.name }}&level=ADMIN">
                    <button class="admin">ADMIN</button>
                </a>

                <a href="/logs?file={{ log.name }}&level=DATE">
                    <button class="date">DATE</button>
                </a>

            </td>
        </tr>
        {% endfor %}

    </table>

    </body>
    </html>
    """

    return render_template_string(html, logs=log_files, preview=latest_logs, preview_size=LOG_VIEW)


@app.route("/metrics")
def metrics():
    if not is_admin_authenticated():
        return handle_unauthorized()

    if not SNAPSHOT_HISTORY:
        return "No data yet"

    return f"""
    <html>
    <head>
        <title>SGNT Metrics</title>
        <link rel="icon" type="image/png" href="/static/sgnticon.png">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet">
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body {{
                background-color: #0f172a;
                color: white;
                font-family: 'Inter', sans-serif;
            }}
            canvas {{
                background: #1e293b;
                border-radius: 10px;
                padding: 10px;
                margin-bottom: 10px;
            }}
            button {{
                margin-bottom: 30px;
                margin-right: 10px;
                padding: 8px 12px;
                border: none;
                border-radius: 6px;
                background: #2563eb;
                color: white;
                cursor: pointer;
            }}
            button:hover {{
                background: #1d4ed8;
            }}
        </style>
    </head>
    <body>

    <h1>📊 Trading Dashboard</h1>

    <canvas id="balanceChart"></canvas>
    <button onclick="downloadChart('balanceChart')">Download Balance</button>

    <canvas id="marginChart"></canvas>
    <button onclick="downloadChart('marginChart')">Download Margin</button>

    <canvas id="activityChart"></canvas>
    <button onclick="downloadChart('activityChart')">Download Activity</button>

    <script>
    const data = {SNAPSHOT_HISTORY};
    const SNAPSHOT_INTERVAL = {SNAPSHOT_INTERVAL};
    const labels = data.map(d => d.time);

    function dataset(label, key, color) {{
        return {{
            label: label,
            data: data.map(d => d[key]),
            borderColor: color,
            tension: 0.2
        }};
    }}

    function downloadChart(chartId) {{
        const canvas = document.getElementById(chartId);
        const link = document.createElement('a');
        link.download = chartId + '.png';
        link.href = canvas.toDataURL('image/png');
        link.click();
    }}

    const balanceChart = new Chart(document.getElementById('balanceChart'), {{
        type: 'line',
        data: {{
            labels,
            datasets: [
                dataset("Total Balance", "totalBalanceUSDC", "cyan"),
                dataset("USDC Balance", "usdcBalance", "green"),
                dataset("Debt", "totalDebt", "red"),
                dataset("Borrowed", "usdcBorrowed", "orange")
            ]
        }}
    }});

    const marginChart = new Chart(document.getElementById('marginChart'), {{
        type: 'line',
        data: {{
            labels,
            datasets: [dataset("Margin Level", "marginLevel", "purple")]
        }},
        options: {{
            scales: {{
                y: {{ min: 0, max: 1000 }}
            }}
        }}
    }});

    const activityDatasets = [];
    
    if (SNAPSHOT_INTERVAL === 1) {{
        activityDatasets.push(
            {{
                type: 'bar',
                label: 'Today Longs',
                data: data.map(d => d.longsToday),
                backgroundColor: 'rgba(34,197,94,0.6)',
                stack: 'daily'
            }},
            {{
                type: 'bar',
                label: 'Today Shorts',
                data: data.map(d => d.shortsToday),
                backgroundColor: 'rgba(239,68,68,0.6)',
                stack: 'daily'
            }}
        );
    }}
    
    activityDatasets.push(
        {{
            type: 'line',
            label: 'Total Longs',
            data: data.map(d => d.totalLongs),
            borderColor: 'green',
            tension: 0.2,
            yAxisID: 'y1'
        }},
        {{
            type: 'line',
            label: 'Total Shorts',
            data: data.map(d => d.totalShorts),
            borderColor: 'red',
            tension: 0.2,
            yAxisID: 'y1'
        }},
        {{
            type: 'line',
            label: 'Trades',
            data: data.map(d => d.tradeID),
            borderColor: 'white',
            tension: 0.2,
            yAxisID: 'y1'
        }}
    );
    
    const activityChart = new Chart(document.getElementById('activityChart'), {{
        data: {{
            labels,
            datasets: activityDatasets
        }},
        options: {{
            responsive: true,
            scales: {{
                y: {{
                    beginAtZero: true,
                    stacked: SNAPSHOT_INTERVAL === 1,
                    title: {{ display: true, text: "Daily Activity" }}
                }},
                y1: {{
                    beginAtZero: true,
                    position: 'right',
                    title: {{ display: true, text: "Total Metrics" }},
                    grid: {{ drawOnChartArea: false }}
                }}
            }}
        }}
    }});

    </script>

    </body>
    </html>
    """


# ====== FLASK EXECUTION ======
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)

import os
import hmac
import hashlib
import time
import json
import requests
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from flask import Flask, request, jsonify

app = Flask(__name__)

# === CONFIG ===
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
BASE_URL = "https://api.binance.com"
TRADE_URL = BASE_URL + "/api/v3/order"
EXCHANGE_INFO_URL = BASE_URL + "/api/v3/exchangeInfo"
ACCOUNT_MARGIN_URL = BASE_URL + "/sapi/v1/margin/account"
BORROW_URL = BASE_URL + "/sapi/v1/margin/loan"
REPAY_URL = BASE_URL + "/sapi/v1/margin/repay"
HEADERS = {"X-MBX-APIKEY": BINANCE_API_KEY}

# Config por defecto si no llega en JSON
STOP_LOSS_PCT = 0.04
TAKE_PROFIT_PCT = 0.04
TRADE_PORTION = 0.05
DRY_RUN = False

# === UTILS ===

def _sign(params: dict) -> dict:
    query = "&".join([f"{k}={v}" for k, v in params.items()])
    signature = hmac.new(BINANCE_API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    params["signature"] = signature
    return params


def _request_with_retries(method, url, headers=None, params=None, data=None, max_retries=3):
    for attempt in range(max_retries):
        try:
            r = requests.request(method, url, headers=headers, params=params, data=data, timeout=10)
            if r.status_code == 200:
                return r.json()
            else:
                print(f"⚠️ Attempt {attempt+1} failed: {r.text}")
        except Exception as e:
            print(f"⚠️ Attempt {attempt+1} exception: {e}")
        time.sleep(1)
    raise Exception("❌ Request failed after retries")


def get_lot_size_info(symbol):
    data = _request_with_retries("GET", EXCHANGE_INFO_URL, headers=HEADERS, params={"symbol": symbol})
    filters = {f["filterType"]: f for f in data["symbols"][0]["filters"]}
    lot_size = filters["LOT_SIZE"]
    price_filter = filters["PRICE_FILTER"]
    return {
        "stepSize_str": lot_size["stepSize"],
        "tickSize_str": price_filter["tickSize"]
    }


def floor_to_step_str(qty: float, step_size_str: str) -> str:
    step = Decimal(step_size_str)
    q = Decimal(str(qty))
    steps = (q / step).to_integral_value(rounding=ROUND_DOWN)
    adjusted = steps * step
    decimals = -step.as_tuple().exponent if step.as_tuple().exponent < 0 else 0
    return f"{adjusted:.{decimals}f}"


# === ✅ NUEVA FUNCIÓN MEJORADA ===
def format_price_to_tick(price: float, tick_size_str: str, rounding=ROUND_DOWN) -> str:
    """
    Ajusta price a múltiplos exactos de tick_size. rounding puede ser ROUND_DOWN o ROUND_UP.
    Devuelve string con los decimales correctos exigidos por Binance.
    """
    d_tick = Decimal(str(tick_size_str))
    p = Decimal(str(price))

    # Truncar o elevar al múltiplo exacto más cercano permitido
    steps = (p / d_tick).to_integral_value(rounding=rounding)
    adjusted = steps * d_tick

    decimals = -d_tick.as_tuple().exponent if d_tick.as_tuple().exponent < 0 else 0
    return f"{adjusted:.{decimals}f}"


def get_balance_margin(asset):
    if DRY_RUN:
        print(f"[DRY_RUN] get_balance_margin({asset}) -> simulated 1000")
        return 1000.0
    params = {"timestamp": int(time.time() * 1000)}
    signed = _sign(params)
    data = _request_with_retries("GET", ACCOUNT_MARGIN_URL, headers=HEADERS, params=signed)
    bal = next((b for b in data["userAssets"] if b["asset"] == asset), None)
    return float(bal["free"]) if bal else 0.0


def borrow_asset(asset, amount):
    if DRY_RUN:
        print(f"[DRY_RUN] borrow_asset({asset}, {amount}) -> simulated")
        return True
    params = {"asset": asset, "amount": amount, "timestamp": int(time.time() * 1000)}
    signed = _sign(params)
    _request_with_retries("POST", BORROW_URL, headers=HEADERS, params=signed)
    print(f"📥 Borrowed {amount} {asset}")
    return True


def place_order_margin(symbol, side, quantity, price=None):
    if DRY_RUN:
        print(f"[DRY_RUN] place_order_margin({symbol}, {side}, qty={quantity}, price={price}) -> simulated")
        return {"orderId": 12345}
    params = {
        "symbol": symbol,
        "side": side,
        "type": "MARKET" if not price else "LIMIT",
        "quantity": quantity,
        "timestamp": int(time.time() * 1000),
    }
    if price:
        params["price"] = price
        params["timeInForce"] = "GTC"

    signed = _sign(params)
    return _request_with_retries("POST", TRADE_URL, headers=HEADERS, params=signed)


def place_sl_tp_margin(symbol, entry_side, entry_price, sl_price, tp_price, qty, lot):
    opp_side = "SELL" if entry_side == "BUY" else "BUY"

    sl_round = ROUND_DOWN if opp_side == "SELL" else ROUND_UP
    tp_round = ROUND_UP if opp_side == "SELL" else ROUND_DOWN

    sl_price_str = format_price_to_tick(sl_price, lot["tickSize_str"], rounding=sl_round)
    tp_price_str = format_price_to_tick(tp_price, lot["tickSize_str"], rounding=tp_round)
    qty_str = floor_to_step_str(qty, lot["stepSize_str"])

    if DRY_RUN:
        print(f"[DRY_RUN] SL/TP {symbol} {opp_side} sl={sl_price_str} tp={tp_price_str} qty={qty_str}")
        return

    for label, p in [("SL", sl_price_str), ("TP", tp_price_str)]:
        params = {
            "symbol": symbol,
            "side": opp_side,
            "type": "LIMIT",
            "price": p,
            "quantity": qty_str,
            "timeInForce": "GTC",
            "timestamp": int(time.time() * 1000),
        }
        signed = _sign(params)
        _request_with_retries("POST", TRADE_URL, headers=HEADERS, params=signed)
        print(f"✅ {label} {symbol} @ {p} placed successfully.")


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    print(f"📩 Webhook received: {data}")

    symbol = data.get("symbol")
    side = data.get("side")
    entry_price = float(data.get("entry_price", 0))
    sl = float(data.get("sl", 0))
    tp = float(data.get("tp", 0))

    lot = get_lot_size_info(symbol)

    # Balance & cantidad
    quote = symbol.replace("USDC", "")
    balance = get_balance_margin("USDC")
    trade_usdc = balance * TRADE_PORTION
    qty = trade_usdc / entry_price
    qty_str = floor_to_step_str(qty, lot["stepSize_str"])

    if "SELL" in side.upper():
        borrow_asset(quote, qty_str)

    # Orden de entrada
    order = place_order_margin(symbol, side, qty_str)
    print(f"✅ {side} opened {symbol} qty={qty_str}")

    # SL/TP
    if not sl or not tp:
        sl = entry_price * (1 - STOP_LOSS_PCT if side == "BUY" else 1 + STOP_LOSS_PCT)
        tp = entry_price * (1 + TAKE_PROFIT_PCT if side == "BUY" else 1 - TAKE_PROFIT_PCT)

    try:
        place_sl_tp_margin(symbol, side, entry_price, sl, tp, qty, lot)
    except Exception as e:
        print(f"⚠️ Could not place SL/TP for {symbol}: {e}")

    return jsonify({"code": "success", "message": "Order executed"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

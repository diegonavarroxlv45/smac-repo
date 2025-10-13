import os
import time
import hmac
import hashlib
import requests
import functools
from decimal import Decimal, ROUND_DOWN
from flask import Flask, request, jsonify

# --- Configuración (ajustables con ENV) ---
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
BASE_URL = "https://api.binance.com"

# Risk / sizing
BUY_PCT = float(os.getenv("BUY_PCT", "0.04"))               # % del balance USD(C) a usar por orden
MAX_USDC_PER_ORDER = float(os.getenv("MAX_USDC_PER_ORDER", "100"))  # tope por orden en USDC
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.97"))   # fallback SL (si alerta no manda)
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "1.06"))# fallback TP
COMMISSION_BUFFER = Decimal(os.getenv("COMMISSION_BUFFER", "0.999")) # margen para qty OCO-like

# Operación de prueba
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes")

# Force stdout flush so logs appear immediately in Render
print = functools.partial(print, flush=True)

app = Flask(__name__)

# ---------------- HELPERS ----------------

def sign_params_query(params: dict, secret: str) -> (str, str):
    """
    Construye query string en el orden de inserción y devuelve (query_string, signature).
    Usa el orden de keys tal como fueron añadidas al dict.
    """
    # Assumes insertion ordered dict (python3.7+)
    query = "&".join([f"{k}={params[k]}" for k in params.keys()])
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query, signature

def send_signed_request(method: str, path: str, params: dict):
    """
    Envía petición firmada a Binance construyendo query + signature.
    Devuelve r.json() (lanza HTTPError si status != 2xx).
    """
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
    """
    Obtiene el balance 'free' del Margín (Cross) para asset.
    Endpoint: /sapi/v1/margin/account
    """
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
    # data contains "userAssets": [...]
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
    # fallback: assume last 4 chars quote
    return symbol[:-4], symbol[-4:]

# ---------------- POSITIONS / ORDERS (MARGIN) ----------------

def cancel_margin_open_orders(symbol: str):
    """
    Cancela órdenes abiertas en Margin para el símbolo.
    Endpoint: DELETE /sapi/v1/margin/openOrders
    """
    ts = int(time.time() * 1000)
    params = {"symbol": symbol, "timestamp": ts}
    try:
        resp = send_signed_request("DELETE", "/sapi/v1/margin/openOrders", params)
        print(f"🗑 Cancelled openOrders for {symbol} (resp: {resp if DRY_RUN else 'ok'})")
    except Exception as e:
        print(f"⚠️ Could not cancel openOrders for {symbol}: {e}")

def close_position_margin(symbol: str):
    """
    Cancela órdenes y vende todo el balance disponible del asset en Margin.
    Usa endpoint POST /sapi/v1/margin/order (type MARKET).
    """
    try:
        cancel_margin_open_orders(symbol)
        base_asset, _ = detect_base_asset(symbol)
        qty = get_margin_balance(base_asset)
        if qty <= 0:
            print(f"ℹ️ No balance to close for {symbol}")
            return None

        lot = get_lot_info(symbol)
        if not lot:
            print(f"⚠️ No lot info for {symbol}, cannot SELL")
            return None

        qty_str = floor_to_step_str(qty, lot["stepSize_str"])
        if float(qty_str) < lot["minQty"]:
            print(f"⚠️ Close qty {qty_str} < minQty {lot['minQty']} for {symbol}")
            return None

        params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": qty_str,
            "timestamp": int(time.time() * 1000),
        }
        resp = send_signed_request("POST", "/sapi/v1/margin/order", params)
        print(f"✅ Closed position {symbol}, qty={qty_str}, order={resp.get('orderId') if resp else resp}")
        return resp
    except Exception as e:
        print(f"❌ Error closing {symbol}: {e}")
        return None

def execute_buy_margin(symbol: str, entry_price_from_alert: float = None, sl_from_alert: float = None, tp_from_alert: float = None):
    """
    Ejecuta una compra en Margin gastando BUY_PCT del capital USDC (con tope MAX_USDC_PER_ORDER)
    y coloca SL (STOP_LOSS_LIMIT) y TP (LIMIT) como órdenes separadas en Margin.
    """
    try:
        # 1) Market buy using quoteOrderQty so we control USDC spent
        lot = get_lot_info(symbol)
        if not lot:
            print(f"⚠️ No lot info for {symbol}")
            return {"error": "no_lot"}

        entry_price = float(entry_price_from_alert) if entry_price_from_alert else get_price(symbol)
        usdc_balance = get_margin_balance("USDC")
        print(f"💰 Margin USDC balance: {usdc_balance:.8f}")

        if usdc_balance <= 5:
            print("⚠️ Not enough USDC to buy on margin")
            return {"error": "low_usdc"}

        usdc_to_use = usdc_balance * BUY_PCT
        if usdc_to_use > MAX_USDC_PER_ORDER:
            print(f"⚠️ usdc_to_use {usdc_to_use:.8f} > MAX_USDC_PER_ORDER {MAX_USDC_PER_ORDER}, capping.")
            usdc_to_use = MAX_USDC_PER_ORDER

        quote_qty_str = format(Decimal(str(usdc_to_use)).quantize(Decimal("0.00000001")), "f")
        print(f"🔢 Using USDC to buy (BUY_PCT={BUY_PCT*100}%): {usdc_to_use:.8f} -> quoteOrderQty={quote_qty_str}")

        # Before trading: cancel any open margin orders for the symbol (important)
        cancel_margin_open_orders(symbol)

        # Place market buy on margin: use quoteOrderQty param
        params_buy = {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quoteOrderQty": quote_qty_str,
            "timestamp": int(time.time() * 1000),
        }
        buy_resp = send_signed_request("POST", "/sapi/v1/margin/order", params_buy)
        executed_qty = float(buy_resp.get("executedQty", "0"))
        executed_quote = float(buy_resp.get("cummulativeQuoteQty", "0") or 0)
        print(f"✅ Margin BUY executed {symbol}: executedQty={executed_qty}, spent≈{executed_quote}")

        # 2) Determine TP and SL values
        if tp_from_alert is not None:
            tp_price = float(tp_from_alert)
            print(f"ℹ️ Using TP from alert: {tp_price}")
        else:
            tp_price = entry_price * TAKE_PROFIT_PCT
            print(f"ℹ️ TP fallback computed: {tp_price}")

        if sl_from_alert is not None:
            sl_price = float(sl_from_alert)
            print(f"ℹ️ Using SL from alert: {sl_price}")
        else:
            sl_price = entry_price * STOP_LOSS_PCT
            print(f"ℹ️ SL fallback computed: {sl_price}")

        tick = lot.get("tickSize_str")
        if not tick:
            print("⚠️ No tick size info; cannot format TP/SL exactly.")
            return {"order": buy_resp, "oco_error": "no_tick"}

        # adjust prices to tick
        tp_adj = ceil_to_step_str(tp_price, tick)
        sl_adj = floor_to_step_str(sl_price, tick)
        stop_limit_adj = floor_to_step_str(float(Decimal(str(sl_adj)) * Decimal("0.999")), tick)

        # qty for SL/TP (leave buffer for commission)
        oco_qty_dec = (Decimal(str(executed_qty)) * COMMISSION_BUFFER)
        oco_qty_str = floor_to_step_str(float(oco_qty_dec), lot["stepSize_str"])

        print("🔧 Preparing SL/TP (Margin separate orders):", {"oco_qty": oco_qty_str, "tp_adj": tp_adj, "sl_adj": sl_adj, "stop_limit_adj": stop_limit_adj})

        # 3) Place SL (stop-limit) and TP (limit) as separate margin orders.
        # Try to place TP then SL; if either fails, log and continue (we will attempt fallbacks).
        results = {"order": buy_resp}
        try:
            params_tp = {
                "symbol": symbol,
                "side": "SELL",
                "type": "LIMIT",
                "quantity": oco_qty_str,
                "price": tp_adj,
                "timeInForce": "GTC",
                "timestamp": int(time.time() * 1000)
            }
            tp_resp = send_signed_request("POST", "/sapi/v1/margin/order", params_tp)
            results["tp"] = tp_resp
            print(f"✅ Margin TP placed: price={tp_adj}, orderId={tp_resp.get('orderId')}")
        except Exception as e_tp:
            print(f"❌ Failed to place margin TP: {e_tp}")
            results["tp_error"] = str(e_tp)

        try:
            params_sl = {
                "symbol": symbol,
                "side": "SELL",
                "type": "STOP_LOSS_LIMIT",
                "quantity": oco_qty_str,
                "price": stop_limit_adj,
                "stopPrice": sl_adj,
                "timeInForce": "GTC",
                "timestamp": int(time.time() * 1000)
            }
            sl_resp = send_signed_request("POST", "/sapi/v1/margin/order", params_sl)
            results["sl"] = sl_resp
            print(f"✅ Margin SL placed: stopPrice={sl_adj}, orderId={sl_resp.get('orderId')}")
        except Exception as e_sl:
            print(f"❌ Failed to place margin SL: {e_sl}")
            results["sl_error"] = str(e_sl)

        return results

    except Exception as e:
        print(f"❌ Error in execute_buy_margin({symbol}): {e}")
        return {"error": str(e)}

# ---------------- WEBHOOK ----------------

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        # accept JSON or form payload
        if request.is_json:
            data = request.get_json()
        else:
            data = request.form.to_dict()

        if not data or "symbol" not in data or "side" not in data:
            return jsonify({"error": "Invalid payload"}), 400

        symbol = data["symbol"].upper()
        side = data["side"].upper()

        # read optional fields from Pine alert
        entry_price = None
        sl = None
        tp = None
        try:
            if "entry_price" in data:
                entry_price = float(data["entry_price"])
            if "sl" in data:
                sl = float(data["sl"])
            if "tp" in data:
                tp = float(data["tp"])
        except Exception:
            pass

        if side == "SELL":
            print(f"🔎 SELL signal received for {symbol} — closing margin position if any.")
            resp = close_position_margin(symbol)
            return jsonify({"status": "SELL processed", "closed": resp}), 200

        elif side == "BUY":
            print(f"🟢 BUY signal for {symbol} — attempting margin BUY")
            result = execute_buy_margin(symbol, entry_price_from_alert=entry_price, sl_from_alert=sl, tp_from_alert=tp)
            return jsonify({"status": "BUY executed (or attempted)", "result": result}), 200

        return jsonify({"status": "ignored"}), 200
    except Exception as e:
        print(f"❌ Error in webhook handler: {e}")
        return jsonify({"error": str(e)}), 500

# ---------------- MAIN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

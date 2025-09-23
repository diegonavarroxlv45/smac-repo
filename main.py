import os
import time
import hmac
import hashlib
import requests
import functools
from decimal import Decimal, ROUND_DOWN
from flask import Flask, request, jsonify

# --- Configuración ---
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
BASE_URL = "https://api.binance.com"

# Porcentajes / buffers
STOP_LOSS_PCT = 0.975   # si no viene SL en el webhook usa -2.5% por defecto (ajusta si quieres)
TAKE_PROFIT_PCT = 1.05  # si no viene TP en el webhook usa +5% por defecto (ajusta si quieres)
BUY_PCT = 0.04          # 4% por alerta (lo que pediste)
COMMISSION_BUFFER = Decimal("0.999")  # margen para qty OCO (evitar rechazos por comisiones)

# flush en todos los prints
print = functools.partial(print, flush=True)

app = Flask(__name__)

# ---------------- HELPERS ----------------
def sign_params_query(params: dict, secret: str) -> str:
    """Construye query string en el orden de inserción y devuelve signature."""
    query = "&".join([f"{k}={params[k]}" for k in params.keys()])
    sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query, sig

def send_signed_request(method: str, path: str, params: dict):
    """
    Envía petición firmada construyendo la query string exactamente en el orden
    con el que se firma (evita problemas por reordenado de params).
    Devuelve response.json() o lanza requests.HTTPError.
    """
    query, sig = sign_params_query(params, BINANCE_API_SECRET)
    url = f"{BASE_URL}{path}?{query}&signature={sig}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    if method.upper() == "GET":
        r = requests.get(url, headers=headers, timeout=10)
    elif method.upper() == "POST":
        r = requests.post(url, headers=headers, timeout=10)
    elif method.upper() == "DELETE":
        r = requests.delete(url, headers=headers, timeout=10)
    else:
        raise ValueError("Unsupported method")
    r.raise_for_status()
    return r.json()

def get_price(symbol: str) -> float:
    r = requests.get(f"{BASE_URL}/api/v3/ticker/price", params={"symbol": symbol}, timeout=10)
    r.raise_for_status()
    return float(r.json()["price"])

def get_balance(asset="USDC") -> float:
    ts = int(time.time() * 1000)
    params = {"timestamp": ts}
    # firma y llamada de forma simple (orden de inserción)
    q, sig = sign_params_query(params, BINANCE_API_SECRET)
    url = f"{BASE_URL}/api/v3/account?{q}&signature={sig}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    data = r.json()
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
    """Trunca hacia abajo al múltiplo de step_str y devuelve string con decimales correctos."""
    v = Decimal(str(value))
    step = Decimal(str(step_str))
    d = (v // step) * step
    decimals = -step.as_tuple().exponent if step.as_tuple().exponent < 0 else 0
    return format(d.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_DOWN), f".{decimals}f")

def ceil_to_step_str(value: float, step_str: str) -> str:
    """Redondea hacia arriba al múltiplo de step_str y devuelve string con decimales correctos."""
    v = Decimal(str(value))
    step = Decimal(str(step_str))
    # n = ceil(v / step)
    div = (v / step)
    n = div.to_integral_value(rounding=ROUND_DOWN)
    if (div - n) > Decimal("0"):
        n = n + 1
    d = n * step
    decimals = -step.as_tuple().exponent if step.as_tuple().exponent < 0 else 0
    return format(d.quantize(Decimal(1).scaleb(-decimals)), f".{decimals}f")

def detect_base_asset(symbol: str):
    quote_candidates = ["USDC", "USDT", "BUSD", "EUR", "USD", "BTC", "ETH", "BNB"]
    for q in quote_candidates:
        if symbol.endswith(q):
            return symbol[:-len(q)], q
    # fallback: assume last 4 chars are quote (e.g. USDC/USDT)
    return symbol[:-4], symbol[-4:]

# ---------------- POSITIONS ----------------
def close_position(symbol: str):
    """
    Cancela órdenes abiertas y vende cualquier balance libre del asset base.
    """
    try:
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        ts = int(time.time() * 1000)

        # Cancel open orders (DELETE /api/v3/openOrders)
        params = {"symbol": symbol, "timestamp": ts}
        try:
            send_signed_request("DELETE", "/api/v3/openOrders", params)
            print(f"🗑 Cancelled openOrders for {symbol} (if any).")
        except Exception as e:
            # No dramático; seguimos
            print(f"⚠️ Could not cancel openOrders for {symbol}: {e}")

        base_asset, _ = detect_base_asset(symbol)
        qty = get_balance(base_asset)
        if qty <= 0:
            print(f"ℹ️ No balance to close for {symbol}")
            return

        lot = get_lot_info(symbol)
        if not lot:
            print(f"⚠️ No lot info for {symbol}, cannot SELL")
            return

        qty_str = floor_to_step_str(qty, lot["stepSize_str"])
        if float(qty_str) < lot["minQty"]:
            print(f"⚠️ Close qty {qty_str} < minQty {lot['minQty']} for {symbol}")
            return

        # Market SELL using signed helper
        order_params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": qty_str,
            "timestamp": int(time.time() * 1000)
        }
        resp = send_signed_request("POST", "/api/v3/order", order_params)
        print(f"✅ Closed position {symbol}, qty={qty_str}, resp_orderId={resp.get('orderId')}")
    except Exception as e:
        print(f"❌ Error closing {symbol}: {e}")

# ---------------- BUY / OCO ----------------
def execute_buy(symbol: str, entry_price_from_alert: float = None, sl_from_alert: float = None, tp_from_alert: float = None):
    """
    Ejecuta market BUY usando BUY_PCT del balance USDC y coloca OCO (SL + TP).
    Si alerta trae sl/tp/entry, los usa; sino usa porcentajes por defecto.
    """
    try:
        lot = get_lot_info(symbol)
        if not lot:
            print(f"⚠️ No lot info for {symbol}")
            return None

        # precio de referencia: preferir entry_price de la alerta (TradingView),
        # sino usar price spot
        if entry_price_from_alert:
            entry_price = float(entry_price_from_alert)
        else:
            entry_price = get_price(symbol)

        usdc_balance = get_balance("USDC")
        print(f"💰 USDC balance: {usdc_balance:.6f}")

        if usdc_balance <= 5:
            print("⚠️ Not enough USDC to buy")
            return None

        usdc_to_use = usdc_balance * BUY_PCT
        # seguridad extra: no usar más de X USDC por orden accidental (opcional)
        # if usdc_to_use > 100: usdc_to_use = 100

        print(f"🔢 USDC to use (BUY_PCT={BUY_PCT*100}%): {usdc_to_use:.6f}")

        raw_qty = Decimal(str(usdc_to_use)) / Decimal(str(entry_price))
        qty_str = floor_to_step_str(float(raw_qty), lot["stepSize_str"])
        qty = float(qty_str)

        print(f"🔧 Calculated qty -> raw={raw_qty} -> qty_str={qty_str}")

        # Validaciones
        if qty < lot["minQty"] or (qty * entry_price) < (lot.get("minNotional", 0.0) or 0.0):
            print(f"⚠️ Qty {qty_str} too small for {symbol} (minQty={lot['minQty']}, minNotional={lot.get('minNotional')})")
            return None

        # Ejecutar market BUY (signed request)
        order_params = {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quantity": qty_str,
            "timestamp": int(time.time() * 1000)
        }
        order_resp = send_signed_request("POST", "/api/v3/order", order_params)
        executed_qty = float(order_resp.get("executedQty", qty_str))
        executed_quote = float(order_resp.get("cummulativeQuoteQty", 0.0))
        print(f"✅ BUY {symbol} executed: executedQty={executed_qty}, spentQuote={executed_quote}")

        # Determinar SL y TP (preferir valores enviados por TradingView)
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

        # Formateo de precios según tickSize
        tick = lot.get("tickSize_str")
        if not tick:
            print("⚠️ No tickSize info; cannot format prices for OCO reliably.")
            return {"order": order_resp, "oco_error": "no_tick"}

        # Para TP, usamos ceil -> asegurarnos TP >= objetivo
        tp_adj = ceil_to_step_str(tp_price, tick)
        # Para SL y stopLimit usamos floor -> SL <= objetivo
        sl_adj = floor_to_step_str(sl_price, tick)
        stop_limit_adj = floor_to_step_str(float(Decimal(str(sl_adj)) * Commission = Decimal('0.999') if False else 1), tick)  # placeholder
        # We'll compute a practical stopLimit slightly below sl_adj:
        stop_limit_adj = floor_to_step_str(float(Decimal(str(sl_adj)) * Decimal("0.999")), tick)

        # qty para OCO: dejar margen para comisiones
        oco_qty = Decimal(str(executed_qty)) * COMMISSION_BUFFER
        oco_qty_str = floor_to_step_str(float(oco_qty), lot["stepSize_str"])

        print("🔧 OCO params prepared:", {"oco_qty": oco_qty_str, "tp_adj": tp_adj, "sl_adj": sl_adj, "stop_limit_adj": stop_limit_adj})

        # Preparar y enviar OCO
        oco_params = {
            "symbol": symbol,
            "side": "SELL",
            "quantity": oco_qty_str,
            "price": tp_adj,
            "stopPrice": sl_adj,
            "stopLimitPrice": stop_limit_adj,
            "stopLimitTimeInForce": "GTC",
            "timestamp": int(time.time() * 1000)
        }
        oco_resp = send_signed_request("POST", "/api/v3/order/oco", oco_params)
        print(f"✅ OCO placed for {symbol}: TP={tp_adj}, SL={sl_adj}")
        return {"order": order_resp, "oco": oco_resp}

    except Exception as e:
        print(f"❌ Error in execute_buy({symbol}): {e}")
        return None

# ---------------- WEBHOOK ----------------
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        if request.is_json:
            data = request.get_json()
        else:
            data = request.form.to_dict()

        if not data or "symbol" not in data or "side" not in data:
            return jsonify({"error": "Invalid payload"}), 400

        symbol = data["symbol"].upper()
        side = data["side"].upper()

        # parse optional fields from your Pine JSON
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
            # si vienen strings etc, ignora y sigue con None
            pass

        if side == "SELL":
            print(f"🔎 Closing existing position before SELL {symbol}")
            close_position(symbol)
            return jsonify({"status": "SELL processed"})

        elif side == "BUY":
            print(f"🟢 BUY signal for {symbol}")
            result = execute_buy(symbol, entry_price_from_alert=entry_price, sl_from_alert=sl, tp_from_alert=tp)
            return jsonify({"status": "BUY executed", "result": result})

        return jsonify({"status": "ignored"})
    except Exception as e:
        print(f"❌ Error in webhook: {e}")
        return jsonify({"error": str(e)}), 500

# ---------------- MAIN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

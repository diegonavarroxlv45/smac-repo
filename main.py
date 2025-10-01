import os
import time
import hmac
import hashlib
import requests
import functools
from flask import Flask, request, jsonify

# --- Configuración ---
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
BASE_URL = "https://api.binance.com"

BUY_PCT = float(os.getenv("BUY_PCT", "0.04"))   # 4% por defecto
MAX_USDC_PER_ORDER = float(os.getenv("MAX_USDC_PER_ORDER", "100"))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes")

print = functools.partial(print, flush=True)
app = Flask(__name__)

# ---------------- HELPERS ----------------
def sign_params_query(params: dict, secret: str) -> (str, str):
    query = "&".join([f"{k}={params[k]}" for k in params.keys()])
    sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query, sig

def send_signed_request(method: str, path: str, params: dict, base=BASE_URL):
    ts = int(time.time() * 1000)
    params["timestamp"] = ts
    query, sig = sign_params_query(params, BINANCE_API_SECRET)
    url = f"{base}{path}?{query}&signature={sig}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    if DRY_RUN:
        print(f"[DRY_RUN] would {method} {url}")
        return {"dry_run": True}
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

def get_balance(asset="USDC") -> float:
    ts = int(time.time() * 1000)
    params = {"timestamp": ts}
    q, sig = sign_params_query(params, BINANCE_API_SECRET)
    url = f"{BASE_URL}/api/v3/account?{q}&signature={sig}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    if DRY_RUN:
        print(f"[DRY_RUN] get_balance({asset}) -> simulated 1000")
        return 1000.0
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    data = r.json()
    bal = next((b for b in data.get("balances", []) if b["asset"] == asset), None)
    return float(bal["free"]) if bal else 0.0

def detect_base_asset(symbol: str):
    quote_candidates = ["USDC", "USDT", "BUSD", "EUR", "USD", "BTC", "ETH", "BNB"]
    for q in quote_candidates:
        if symbol.endswith(q):
            return symbol[:-len(q)], q
    return symbol[:-4], symbol[-4:]

# ---------------- CONVERSION ----------------
def convert_asset(from_asset, to_asset, amount):
    """
    Realiza una conversión usando Binance Convert API.
    """
    try:
        # 1) Obtener cotización
        quote = send_signed_request("POST", "/sapi/v1/convert/getQuote", {
            "fromAsset": from_asset,
            "toAsset": to_asset,
            "fromAmount": str(amount)
        }, base="https://api.binance.com")

        quote_id = quote.get("quoteId")
        if not quote_id:
            print(f"❌ No se pudo obtener quote para {from_asset}->{to_asset}")
            return None

        # 2) Aceptar cotización
        result = send_signed_request("POST", "/sapi/v1/convert/acceptQuote", {
            "quoteId": quote_id
        }, base="https://api.binance.com")

        print(f"✅ Convert {from_asset}->{to_asset}: {amount}, result={result}")
        return result
    except Exception as e:
        print(f"❌ Conversion error: {e}")
        return None

# ---------------- LOGIC ----------------
def execute_buy(symbol: str):
    base_asset, quote_asset = detect_base_asset(symbol)
    usdc_balance = get_balance("USDC")
    if usdc_balance <= 5:
        print("⚠️ Not enough USDC to buy")
        return None

    usdc_to_use = usdc_balance * BUY_PCT
    if usdc_to_use > MAX_USDC_PER_ORDER:
        usdc_to_use = MAX_USDC_PER_ORDER

    return convert_asset("USDC", base_asset, usdc_to_use)

def close_position(symbol: str):
    base_asset, quote_asset = detect_base_asset(symbol)
    qty = get_balance(base_asset)
    if qty <= 0:
        print(f"ℹ️ No {base_asset} balance to sell")
        return None
    return convert_asset(base_asset, "USDC", qty)

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

        if side == "SELL":
            print(f"🔎 SELL signal {symbol} -> convertir a USDC")
            res = close_position(symbol)
            return jsonify({"status": "SELL processed", "result": res})

        elif side == "BUY":
            print(f"🟢 BUY signal {symbol} -> convertir USDC a token")
            res = execute_buy(symbol)
            return jsonify({"status": "BUY executed", "result": res})

        return jsonify({"status": "ignored"})
    except Exception as e:
        print(f"❌ Error in webhook: {e}")
        return jsonify({"error": str(e)}), 500

# ---------------- MAIN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

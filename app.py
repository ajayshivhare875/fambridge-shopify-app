"""FamBridge UPI — Shopify App (blueprint).

Flow:
  1. Merchant installs app (OAuth) -> shop token stored.
  2. Customer checks out with manual payment "UPI (FamBridge)" -> order created (pending).
  3. Shopify fires orders/create webhook -> app creates FamBridge order
     (note = "shopify_{shop}_{order_id}") -> QR page link stored.
  4. Customer opens QR page (/pay/<token>), pays via UPI app.
  5. FamBridge detects payment -> webhook -> app marks Shopify order paid.

Env:
  SHOPIFY_API_KEY, SHOPIFY_API_SECRET, APP_URL (https://...onrender.com),
  FAMBRIDGE_API_KEY (merchant's own key; per-shop override supported),
  FAMBRIDGE_BASE (optional, default gateway URL)
"""
import os
import json
import time
import secrets
import urllib.parse
import requests
from flask import Flask, request, jsonify, redirect, Response, \
    render_template

from shopify_client import ShopifyClient, verify_webhook_hmac
from fambridge_client import FamBridgeClient, verify_webhook

app = Flask(__name__)

SHOPIFY_API_KEY = os.environ.get("SHOPIFY_API_KEY", "")
SHOPIFY_API_SECRET = os.environ.get("SHOPIFY_API_SECRET", "")
APP_URL = os.environ.get("APP_URL", "").rstrip("/")
FAMBRIDGE_API_KEY = os.environ.get("FAMBRIDGE_API_KEY", "")
FAMBRIDGE_BASE = os.environ.get("FAMBRIDGE_BASE", "")

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)
SHOPS_FILE = os.path.join(DATA_DIR, "shops.json")    # shop -> {token, fambridge_key}
ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")  # pay_token -> {...}

SCOPES = "read_orders,write_orders"

# ---------------- storage ----------------
def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def load_shops():
    return _load(SHOPS_FILE)


def save_shops(d):
    _save(SHOPS_FILE, d)


def load_orders():
    return _load(ORDERS_FILE)


def save_orders(d):
    _save(ORDERS_FILE, d)


# ---------------- helpers ----------------
def shopify_client_for(shop):
    shops = load_shops()
    rec = shops.get(shop)
    if not rec:
        return None
    return ShopifyClient(shop, rec["token"])


def fambridge_for(shop):
    shops = load_shops()
    rec = shops.get(shop, {})
    key = rec.get("fambridge_key") or FAMBRIDGE_API_KEY
    return FamBridgeClient(key, FAMBRIDGE_BASE or
                           "https://fambridge-gateway.onrender.com")


@app.after_request
def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


# ---------------- public routes ----------------
@app.route("/")
def index():
    return Response(
        "<h2>FamBridge UPI for Shopify</h2>"
        "<p>Auto-verified UPI payments for your Shopify store.</p>"
        "<p>Install: <code>/auth?shop=YOURSTORE.myshopify.com</code></p>",
        mimetype="text/html")


@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "fambridge-shopify-app"})


# ---------------- OAuth ----------------
@app.route("/auth")
def auth_start():
    shop = (request.args.get("shop") or "").strip().lower()
    if not shop.endswith(".myshopify.com"):
        return "Invalid shop domain. Use ?shop=yourstore.myshopify.com", 400
    redirect_uri = APP_URL + "/auth/callback"
    params = urllib.parse.urlencode({
        "client_id": SHOPIFY_API_KEY,
        "scope": SCOPES,
        "redirect_uri": redirect_uri,
        "state": secrets.token_hex(8),
    })
    return redirect(f"https://{shop}/admin/oauth/authorize?{params}")


@app.route("/auth/callback")
def auth_callback():
    shop = (request.args.get("shop") or "").strip().lower()
    code = request.args.get("code") or ""
    if not shop or not code:
        return "Missing shop/code", 400
    # Exchange code for access token
    r = requests.post(
        f"https://{shop}/admin/oauth/access_token",
        json={"client_id": SHOPIFY_API_KEY,
              "client_secret": SHOPIFY_API_SECRET,
              "code": code},
        timeout=20)
    if r.status_code != 200:
        return f"OAuth failed: {r.text[:200]}", 400
    token = r.json()["access_token"]
    shops = load_shops()
    shops[shop] = {"token": token, "installed_at": int(time.time())}
    save_shops(shops)
    # Register orders/create webhook
    try:
        sc = ShopifyClient(shop, token)
        requests.post(
            sc.base + "/webhooks.json",
            headers=sc._headers(),
            json={"webhook": {
                "topic": "orders/create",
                "address": APP_URL + "/webhooks/orders/create",
                "format": "json"}},
            timeout=20)
    except Exception as e:
        print("webhook register failed:", e, flush=True)
    return (f"✅ Installed on {shop}!<br><br>"
            "Next: in Shopify Admin → Settings → Payments → "
            "add a <b>Manual payment method</b> named exactly "
            "<b>UPI (FamBridge)</b> with instructions:<br>"
            f"<code>Pay via UPI. You will receive a QR code after checkout.</code>")


# ---------------- Shopify webhooks ----------------
@app.route("/webhooks/orders/create", methods=["POST"])
def orders_create():
    raw = request.get_data()
    if not verify_webhook_hmac(raw, request.headers.get(
            "X-Shopify-Hmac-Sha256"), SHOPIFY_API_SECRET):
        return "bad hmac", 401
    shop = request.headers.get("X-Shopify-Shop-Domain", "").lower()
    order = request.get_json(force=True) or {}

    # Only handle our manual UPI payment method
    gateways = [g.get("gateway", "") for g in
                order.get("payment_gateway_names", [])]
    # payment_gateway_names is a list of strings in newer versions
    if isinstance(order.get("payment_gateway_names"), list):
        gateways = order["payment_gateway_names"]
    if not any("fambridge" in str(g).lower() for g in gateways):
        return jsonify({"ok": True, "skipped": "not fambridge"})

    order_id = str(order["id"])
    total = float(order.get("total_price", "0") or 0)
    currency = order.get("currency", "INR")
    if currency != "INR":
        print(f"non-INR order {order_id}: {currency}", flush=True)

    note = f"shopify_{shop}_{order_id}"
    try:
        fb = fambridge_for(shop)
        fb_order = fb.create_order(total, note)
    except Exception as e:
        print(f"fambridge create_order failed: {e}", flush=True)
        return jsonify({"ok": False, "error": str(e)}), 502

    pay_token = secrets.token_urlsafe(16)
    orders = load_orders()
    orders[pay_token] = {
        "shop": shop,
        "shopify_order_id": order_id,
        "fambridge_order_id": fb_order["order_id"],
        "amount": total,
        "currency": currency,
        "qr_base64": fb_order.get("qr_base64", ""),
        "upi_link": fb_order.get("upi_link", ""),
        "status": "pending",
        "created": int(time.time()),
    }
    save_orders(orders)

    pay_url = f"{APP_URL}/pay/{pay_token}"
    # Add the pay link as an order note for the merchant
    try:
        sc = shopify_client_for(shop)
        if sc:
            sc.add_order_note(order_id,
                              f"FamBridge UPI: customer pay link: {pay_url}")
    except Exception as e:
        print("add note failed:", e, flush=True)

    return jsonify({"ok": True, "pay_url": pay_url})


# ---------------- FamBridge webhook ----------------
@app.route("/webhooks/fambridge", methods=["POST"])
def fambridge_hook():
    raw = request.get_data()
    try:
        data = json.loads(raw)
    except Exception:
        return "bad json", 400
    # data: {order_id, amount, note, utr, ...}
    note = data.get("note", "")
    parts = note.split("_")
    # expected: shopify_<shop>_<order_id>  (shop contains dots)
    if len(parts) < 3 or parts[0] != "shopify":
        return jsonify({"ok": True, "skipped": "note not shopify"})
    order_id = parts[-1]
    shop = "_".join(parts[1:-1])

    # Verify HMAC using the shop's FamBridge key
    shops = load_shops()
    fb_key = (shops.get(shop, {}).get("fambridge_key")
              or FAMBRIDGE_API_KEY)
    if not verify_webhook(raw, request.headers.get("X-Signature"),
                          fb_key):
        return "bad hmac", 401

    # Find our pay record and mark paid
    orders = load_orders()
    pay_token = None
    for tok, rec in orders.items():
        if (rec.get("shop") == shop and
                rec.get("shopify_order_id") == order_id):
            pay_token = tok
            break
    if pay_token:
        orders[pay_token]["status"] = "paid"
        orders[pay_token]["paid_at"] = int(time.time())
        orders[pay_token]["utr"] = data.get("utr", "")
        save_orders(orders)

    # Mark the Shopify order as paid
    try:
        sc = shopify_client_for(shop)
        if not sc:
            return jsonify({"ok": False,
                            "error": "shop not installed"}), 400
        order = sc.get_order(order_id)
        amount = float(order.get("total_price", "0") or 0)
        sc.mark_paid(order_id, amount, order.get("currency", "INR"))
        sc.add_order_note(
            order_id,
            f"✅ FamBridge UPI payment verified "
            f"(UTR: {data.get('utr', 'n/a')}, "
            f"₹{data.get('amount', '?')})")
    except Exception as e:
        print(f"mark_paid failed: {e}", flush=True)
        return jsonify({"ok": False, "error": str(e)}), 502

    return jsonify({"ok": True, "order_id": order_id})


# ---------------- customer QR page ----------------
@app.route("/pay/<token>")
def pay_page(token):
    orders = load_orders()
    rec = orders.get(token)
    if not rec:
        return "Invalid or expired payment link.", 404
    return render_template(
        "qr_page.html",
        qr_base64=rec.get("qr_base64", ""),
        upi_link=rec.get("upi_link", ""),
        amount=rec.get("amount", 0),
        currency=rec.get("currency", "INR"),
        order_id=rec.get("shopify_order_id", ""),
        status=rec.get("status", "pending"),
        token=token)


@app.route("/api/pay_status/<token>")
def pay_status(token):
    orders = load_orders()
    rec = orders.get(token)
    if not rec:
        return jsonify({"status": "unknown"}), 404
    return jsonify({"status": rec.get("status", "pending")})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))

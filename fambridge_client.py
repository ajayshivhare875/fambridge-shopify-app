"""FamBridge API client for the Shopify app.

Wraps the FamBridge gateway API:
  POST /api/create_order  -> {order_id, upi_link, qr_base64, amount}
  GET  /api/order_status  -> {status: pending|paid|expired}
Webhooks from FamBridge are HMAC-SHA256 signed (see app.py).
"""
import hmac
import hashlib
import requests

DEFAULT_BASE = "https://fambridge-gateway.onrender.com"


class FamBridgeClient:
    def __init__(self, api_key, base_url=DEFAULT_BASE):
        self.api_key = api_key
        self.base = base_url.rstrip("/")

    def create_order(self, amount_rupees, note):
        """Create a FamBridge order. amount_rupees: float. Returns dict."""
        r = requests.post(
            self.base + "/api/create_order",
            json={"api_key": self.api_key,
                  "amount": amount_rupees,
                  "note": note[:200]},
            timeout=30)
        data = r.json()
        if r.status_code == 401:
            raise ValueError("Invalid FamBridge API key")
        if r.status_code == 429:
            raise RuntimeError("FamBridge rate limit hit, retry shortly")
        r.raise_for_status()
        return data  # {order_id, upi_link, qr_base64, amount, ...}

    def order_status(self, order_id):
        r = requests.get(
            self.base + "/api/order_status",
            params={"api_key": self.api_key, "order_id": order_id},
            timeout=20)
        if r.status_code == 404:
            return {"status": "unknown"}
        r.raise_for_status()
        return r.json()


def verify_webhook(raw_body, signature, api_key):
    """Verify FamBridge webhook HMAC-SHA256 signature."""
    expected = hmac.new(api_key.encode(), raw_body,
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")

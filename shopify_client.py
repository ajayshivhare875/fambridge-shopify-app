"""Shopify Admin API client for the FamBridge UPI app.

Uses a per-shop access token obtained via OAuth (stored by app.py).
API version: 2024-01 (stable).
"""
import hmac
import hashlib
import base64
import requests

API_VERSION = "2024-01"


class ShopifyClient:
    def __init__(self, shop, access_token):
        # shop: "mystore.myshopify.com"
        self.shop = shop
        self.token = access_token
        self.base = f"https://{shop}/admin/api/{API_VERSION}"

    def _headers(self):
        return {"X-Shopify-Access-Token": self.token,
                "Content-Type": "application/json"}

    def get_order(self, order_id):
        r = requests.get(f"{self.base}/orders/{order_id}.json",
                         headers=self._headers(), timeout=20)
        r.raise_for_status()
        return r.json()["order"]

    def mark_paid(self, order_id, amount, currency="INR"):
        """Mark a pending manual-payment order as paid.

        Creates a 'sale' transaction with status success, which flips
        financial_status from 'pending' to 'paid'.
        """
        r = requests.post(
            f"{self.base}/orders/{order_id}/transactions.json",
            headers=self._headers(),
            json={"transaction": {
                "kind": "sale",
                "status": "success",
                "amount": str(amount),
                "currency": currency,
                "gateway": "FamBridge UPI",
            }},
            timeout=20)
        r.raise_for_status()
        return r.json()["transaction"]

    def add_order_note(self, order_id, note):
        """Append a note to the order timeline (visible in admin)."""
        order = self.get_order(order_id)
        existing = order.get("note") or ""
        new_note = (existing + "\n" + note).strip()
        r = requests.put(
            f"{self.base}/orders/{order_id}.json",
            headers=self._headers(),
            json={"order": {"id": order_id, "note": new_note}},
            timeout=20)
        r.raise_for_status()
        return r.json()["order"]


def verify_webhook_hmac(raw_body, hmac_header, api_secret):
    """Verify Shopify webhook HMAC-SHA256 (base64)."""
    digest = hmac.new(api_secret.encode(), raw_body,
                      hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, hmac_header or "")

# FamBridge UPI for Shopify

Shopify app that lets merchants accept **auto-verified UPI payments** via
FamBridge. Works around Shopify's closed payment-gateway ecosystem using a
manual payment method + webhook auto-verification.

## How it works

1. Merchant installs the app (OAuth) on their store.
2. Merchant adds a **Manual payment method** named `UPI (FamBridge)` in
   Shopify Admin → Settings → Payments.
3. Customer checks out with "UPI (FamBridge)" → order created as
   payment-pending.
4. Shopify fires `orders/create` webhook → app calls FamBridge
   `/api/create_order` (note = `shopify_<shop>_<order_id>`) → QR page
   link saved + added as an order note.
5. Customer opens `/pay/<token>`, scans QR, pays via any UPI app.
6. FamBridge detects the payment (~25s via Gmail) → webhook →
   app marks the Shopify order **paid** via Admin API.

## Setup

### 1. Shopify Partners app

1. Go to [partners.shopify.com](https://partners.shopify.com) → Apps →
   **Create app** (Custom distribution or public).
2. Note the **API key** and **API secret**.
3. Set the app URL to your deployed URL (e.g.
   `https://fambridge-shopify-app.onrender.com`) and the redirect URL to
   `https://fambridge-shopify-app.onrender.com/auth/callback`.
4. Request scopes: `read_orders`, `write_orders`.

### 2. Deploy

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

Or manually: new Web Service from this repo, Python, Singapore, free.
Set env vars in the Render dashboard:

| Key | Value |
|---|---|
| `SHOPIFY_API_KEY` | from Partners dashboard |
| `SHOPIFY_API_SECRET` | from Partners dashboard |
| `APP_URL` | `https://fambridge-shopify-app.onrender.com` |
| `FAMBRIDGE_API_KEY` | merchant's `fp_...` key (from @Fampayautoverify_bot) |
| `FAMBRIDGE_BASE` | `https://fambridge-gateway.onrender.com` |

### 3. Merchant steps (per store)

1. Visit `https://fambridge-shopify-app.onrender.com/auth?shop=STORE.myshopify.com`
   → approve → "Installed".
2. Shopify Admin → Settings → Payments → **Manual payment methods** →
   add `UPI (FamBridge)`, instructions:
   `Pay via UPI — you will get a QR code right after checkout.`
3. In the app's `data/shops.json`, optionally set a per-shop
   `fambridge_key` to use a different FamBridge key per merchant.

### 4. Showing the QR to customers

Options, easiest first:

- **Order note (done automatically):** the pay link is added as an order
  note — merchant can copy it into the confirmation email.
- **Thank-you page script:** Settings → Checkout → Additional scripts —
  needs the pay URL; simplest is to email it (see below).
- **Email (recommended):** Shopify → Settings → Notifications → Order
  confirmation → add: `Pay now: {{ ... }}` — the app can't inject
  template vars, so instead use the order note + a Flow automation,
  or send the link via the merchant's own email tool.
- **Checkout UI extension (best UX, most work):** build a small
  extension that calls `/api/pay_status` — out of scope for this
  blueprint.

## API

- `GET /auth?shop=` — start OAuth
- `POST /webhooks/orders/create` — Shopify → app (HMAC verified)
- `POST /webhooks/fambridge` — FamBridge → app (HMAC verified)
- `GET /pay/<token>` — customer QR page (auto-refreshes)
- `GET /api/pay_status/<token>` — JSON `{"status": "pending"|"paid"}`

## Notes & limits

- Amounts are exact INR; FamBridge matches by exact amount.
- Orders expire in FamBridge after 15 min; the QR page keeps polling.
- Storage is a local JSON file (`data/`) — move to Postgres/Supabase
  before any real traffic (Render disk is ephemeral).
- This is a **blueprint**: OAuth + webhook HMAC + order flow are
  implemented; test end-to-end on a Shopify dev store before selling it.

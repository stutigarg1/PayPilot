# PayPilot

**Describe it. Review it. Let PayPal confirm it.**

PayPilot turns a natural-language collection request into a reviewed PayPal
Sandbox checkout. The buyer approves on PayPal; the backend captures the order,
verifies its amount, currency, reference and completion status, and issues a receipt.
No live payments, payouts, bank connections, or automatic approvals are supported.

## What works

- Next.js / TypeScript / Tailwind responsive payment dashboard.
- Gemini structured intent extraction, with a clearly labeled rule-based parser when no key is configured.
- Immutable reviewed amounts using decimal arithmetic; USD, EUR and GBP, from 0.01 to 1000.
- Duplicate checkout blocking for matching amount, currency and normalized purpose within 24 hours.
- Explicit acknowledgment for amounts of 250 or more.
- PayPal Sandbox checkout, buyer approval, idempotent capture and reconciliation after uncertain responses.
- Owner-scoped history, verified collection totals per currency, and downloadable JSON receipts.
- Local SQLite storage without accounts, or Supabase PostgreSQL and Supabase Auth for hosted use.
- Persistent daily Gemini call limits and capped prompt/output sizes. No automatic model retries.

## Tools used

| Tool | Role in PayPilot |
|---|---|
| **PayPal Orders API v2 (Sandbox)** | Core payment rail. The backend gets an OAuth2 client-credentials token, creates a `CAPTURE` order (`POST /v2/checkout/orders`) with a `PayPal-Request-Id` for idempotency, sends the buyer to PayPal's approval link, then reads (`GET /v2/checkout/orders/{id}`) and captures (`POST .../capture`) the order. Every capture is checked for status, amount, currency and reference before a receipt is issued. See `backend/app/paypal.py`. |
| **Google Gemini** (`gemini-2.5-flash` by default, via the Gemini API) | Turns a free-text collection request into a structured payment intent (amount, currency, purpose) using JSON-schema constrained output at temperature 0. It is instructed to refuse payouts, transfers, refunds, multiple payments and anything ambiguous, and never to invent missing values. Its output is re-validated by Pydantic; the model cannot approve, create or execute payments. See `backend/app/agent.py`. |
| **FastAPI, SQLAlchemy, Pydantic, httpx** | Backend API, storage, validation and HTTP calls to PayPal and Gemini. |
| **Next.js, React, TypeScript, Tailwind CSS** | Payment dashboard frontend. |
| **SQLite / Supabase** | Local storage; Supabase PostgreSQL and Auth for hosted use. |
| **Render** | Hosting blueprint in `render.yaml`. |
| **Claude Code** | AI coding assistant used during development. |

## Run locally (no accounts required)

Requires Python 3.11+ and Node.js 20.9+. These commands start a single-user
workspace accessible only from the same computer. Run them from the folder you
cloned the repository into.

**Windows (PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Copy-Item backend\.env.example backend\.env
Copy-Item frontend\.env.local.example frontend\.env.local
cd frontend; npm ci; cd ..
```

Terminal 1 (backend):

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Terminal 2 (frontend):

```powershell
cd frontend
npm run dev
```

**macOS / Linux**

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
cp frontend/.env.local.example frontend/.env.local
(cd frontend && npm ci)
```

Terminal 1: `cd backend && ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1`

Terminal 2: `cd frontend && npm run dev`

Open **http://localhost:3000**. API docs: **http://localhost:8000/docs**.
Use `localhost`, not `127.0.0.1`, in the browser so the configured CORS origin
and PayPal return URL agree. Do not overwrite an existing `.env` when repeating setup.

Try:

```text
Collect USD 25.00 for design consultation
Collect EUR 350.00 for website development
Collect GBP 45.00 for a product workshop
```

Without credentials, plans, checks and persistent history work. Checkout is
disabled and collection totals remain zero. **No fake payments or receipts are created.**
The local parser intentionally accepts only `Collect/Charge/Request <CURRENCY>
<AMOUNT> for <PURPOSE>`; Gemini enables more flexible phrasing.

## Connect PayPal Sandbox

1. In the [PayPal Developer Dashboard](https://developer.paypal.com/dashboard/),
   make sure the toggle at the top says **Sandbox** (not Live).
2. **Apps & Credentials → Create App** (type *Merchant*), linked to a Sandbox
   **business** account. Copy its Client ID and Secret.
3. Set `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` in `backend\.env`. Never use live credentials.
4. **Testing Tools → Sandbox Accounts → Create account**: a **Personal (buyer)**
   account with **Country/Region: United States**. Open it to see its email and
   password; it comes with a fake balance. Restart FastAPI.
5. Prepare a plan, review its amount/purpose, and select **Approve plan & open PayPal**.
6. Sign in with the US Sandbox **buyer**, select **Complete Purchase**, and let
   PayPal return you to PayPilot.
7. PayPilot verifies buyer approval server-side, captures and verifies the payment,
   updates totals, and enables the receipt.

### Sandbox troubleshooting

- **"This seller doesn't accept payments in your currency"**: your Sandbox buyer
  (and possibly business) account was created in a country such as India, where
  PayPal does not support this checkout. Developer accounts outside the US often
  get default Sandbox accounts in their own country. Create a **United States**
  personal buyer account and pay with it. If that still fails, also create a
  **United States** business account, create a new app linked to it, and use
  that app's credentials.
- **PayPal skips the login and uses the wrong buyer**: PayPal remembers the last
  Sandbox sign-in. Use **Not you? / Change** on the checkout page, log out at
  https://www.sandbox.paypal.com, or open http://localhost:3000 in a
  private/incognito window and resume the payment from **Recent activity**.
- **Confirm a payment independently**: sign in at https://www.sandbox.paypal.com
  as the Sandbox **business** account and check its activity, or call
  `GET /v2/checkout/orders/{order_id}`; the order and its capture should both be
  `COMPLETED`, with the same capture ID shown on the PayPilot receipt.

If you cancel, the app does not request capture; the order stays available to resume.
If you close the return page, open the payment from history and select **Already
approved? Verify payment**. A browser redirect or query-string token is never proof
of payment. Network failures remain unverified; retry the **same** payment rather
than creating a replacement. Stable request IDs prevent duplicate creates/captures
within PayPal's idempotency retention window.

All collection requests go to the merchant account configured by the Sandbox REST
app, not to an arbitrary recipient named in chat.

## Enable Gemini

Set `GEMINI_API_KEY` from [Google AI Studio](https://aistudio.google.com/apikey)
in `backend\.env`, choose an eligible free-tier `GEMINI_MODEL`, and restart.
`gemini-2.5-flash` is a configurable starting value, not a guarantee of
current availability or free-tier eligibility.
`GET /api/health` reports `"parser": "gemini"` once the key is loaded.

With Gemini, natural phrasing works and out-of-scope requests are refused:

```text
Can you bill my client forty-five pounds for the logo redesign workshop?   -> GBP 45.00
Send 100 dollars to my friend                                             -> rejected (payout)
```

Default limits are **20 calls/user/day**, **100 calls across the app/day** (UTC),
1000 prompt characters and 512 output tokens. Failed calls consume the daily
allowance too. Validation, risk checks, totals and payment execution never require
model calls. Only synthetic data may be submitted; no real financial or personal
information. Do not enable billing to bypass a quota.

## Supabase / public hosting

Local mode has no login and must **never** be exposed using a tunnel or public
server. It rejects non-loopback clients and untrusted browser origins.
Production startup refuses local authentication.

For hosted use:

1. Create a free Supabase project. Obtain its Auth URL, public anon key and
   PostgreSQL connection string. Use the dashboard's session pooler connection
   if the host does not support IPv6; include `sslmode=require`.
2. Backend environment:

   ```dotenv
   APP_ENV=production
   AUTH_MODE=supabase
   FRONTEND_URL=https://your-frontend.onrender.com
   DATABASE_URL=postgresql://postgres.PROJECT:PASSWORD@POOLER:5432/postgres?sslmode=require
   SUPABASE_URL=https://PROJECT.supabase.co
   SUPABASE_ANON_KEY=YOUR_PUBLIC_ANON_KEY
   PAYPAL_CLIENT_ID=YOUR_SANDBOX_CLIENT_ID
   PAYPAL_CLIENT_SECRET=YOUR_SANDBOX_SECRET
   ```

3. Frontend build-time environment:

   ```dotenv
   NEXT_PUBLIC_API_URL=https://your-backend.onrender.com
   NEXT_PUBLIC_SUPABASE_URL=https://PROJECT.supabase.co
   NEXT_PUBLIC_SUPABASE_ANON_KEY=YOUR_PUBLIC_ANON_KEY
   ```

4. Set the Supabase Auth site URL to your frontend URL; enable email/password
   sign-in and configure confirmation URLs as needed. Use synthetic demo accounts.
5. The backend creates its two tables and enables PostgreSQL row-level security
   in the same startup transaction, with **no browser-access policies**. Connect
   using the table owner so only this backend accesses the tables directly.
   Each API request independently validates the Supabase session and scopes all
   payment access to its authenticated owner. No service-role key belongs in the frontend.
6. Deploy the two web services using `render.yaml`, or configure them manually.
   Supply the unsynced variables in Render. Frontend public environment variables
   require a rebuild after changes. Start the backend with **one worker and one
   instance**: local locks serialize payment transitions and model-quota reservations.

Render's frontend service must use its assigned `PORT`; the supplied blueprint
does this. SQLite on Render's ephemeral filesystem is **not persistent**; use
Supabase for hosted data. All secrets stay in environment variables.

The project targets **zero recurring subscription cost**, not unlimited usage.
Check current free-tier quotas, regional eligibility, account/card requirements,
and cold-start behavior yourself before deploying. Do not supply billing details,
enable paid upgrades or create paid resources. Free-tier services may pause or
spin down, and no free plan is guaranteed indefinitely.

## Validation

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check .

Set-Location ..\frontend
npm run lint
npm run typecheck
npm run build
```

Backend tests use a temporary SQLite database, mocked Gemini, and a fake Sandbox
adapter: they never send payments or require credentials. Actual provider connectivity
and buyer checkout must still be tested with your own Sandbox accounts.

## Scope and known boundaries

This is a hackathon MVP, not production payment infrastructure. It deliberately
uses a controlled sequence (extract, validate, review, approve, capture, verify),
not an unconstrained tool-calling agent. Risk checks are deterministic heuristics,
not fraud detection. Totals describe verified **collections**, not spending from a
bank account, and currencies are never silently combined.

There are no webhooks, refunds, payouts, recurring payments or background capture.
Reconciliation happens on checkout return or explicit verification. Duplicate
detection is purpose-based, not invoice-based, and intentionally has no override.
History lists the latest 100 plans; totals include all verified captures. Model
call limits are application safeguards, not a provider billing guarantee. PayPal
idempotency keys expire: if a request remains uncertain for hours, inspect the
Sandbox dashboard before retrying. Multi-worker/multi-instance deployments require
database-level transition locking and quota reservations before scaling.

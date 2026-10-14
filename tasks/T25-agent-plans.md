# T25 - Dorj Agent paid plans through the Samin gateway (owner decisions 2026-10-14)

## Decisions (do not change them)
- Plans, 30 days, models qwen3 + glm, price = credit (like chat), about 2x the chat plans:
  | id | title fa / en | price toman | credit toman |
  |---|---|---|---|
  | a200 | پایه / Basic | 200,000 | 200,000 |
  | a500 | حرفه‌ای / Pro | 500,000 | 500,000 |
  | a1200 | سازمانی / Business | 1,200,000 | 1,200,000 |
  Token prices = the chat prices (qwen3 in 5 / out 15, glm in 10 / out 30 toman per 1K tokens). Data in config, not code.
- The user buys INSIDE the Dorj Agent app (new «اشتراک» / "Subscription" page reachable from the account
  menu + a banner when credit is empty). The plan belongs to the nova account (realm nova, tenant username).
- Same gateway as Dorj Chat. Today `DORJ_CHAT_IPG_MODE=samin-develop`; mock mode for tests.

## Design (follow it; if something blocks it, stop and tell me before inventing another one)
ONE payment implementation: dorj-chat chat-api already has the tested Samin client (`ipg.py`), the server-side
Inquire + ConfirmTransaction check, the single-grant guarantee, the develop hostAliases and the node pinning.
Do not copy it into nova.
1. chat-api: add a product dimension. Wallet/ledger rows get `product` ("chat" | "agent"); agent accounts are
   keyed `nova:<username>`. New INTERNAL endpoints (ClusterIP only, not routed by the dorj-site proxy, auth by
   a shared secret header from a k8s Secret, constant-time compare):
   `GET /internal/agent/{user}/wallet`, `POST /internal/agent/{user}/purchase {plan_id, return_url}` -> gateway URL,
   `POST /internal/agent/{user}/usage {model, in_tokens, out_tokens, request_id}` (idempotent by request_id).
   The gateway return goes to chat-api's existing public `/pay/return` flow, which then redirects to
   `https://nova.isigpu.local/billing/done?ref=...` for agent purchases (allowlisted URL, never a URL from the request).
   NetworkPolicy: only ns nova may reach /internal/*.
2. nova-control: `/api/billing/*` for the app (tenant token auth as today) that calls those internal endpoints;
   the `/llmapi` proxy reads `usage` from every completion (add `stream_options.include_usage` for streams),
   reports it (batched, retry, idempotent) and refuses new LLM calls with a clear 402 + Persian message when the
   agent wallet has no active plan or no credit. Grace: a call already running is never cut.
   Existing users today have no plan: add config `NOVA_BILLING_ENFORCE=false` (default false) so nothing changes
   for them until the owner turns it on. nova-admins are never blocked.
3. UI (NovaAgent-1.6.6 branch `dorj-1.6.6-t25` on top of `dorj-1.6.6-t23`): Subscription page with the 3 cards
   (same lock behaviour as chat: once a plan is chosen and the button pressed, other cards are disabled until
   the gateway opens or it fails), current plan / credit / expiry, usage this period. The gateway opens in the
   system browser (shell.openExternal), the app polls the wallet and shows success. fa + en, RTL, Vazirmatn.
4. Admin: nova admin panel shows each user's plan / credit / expiry (table with filter, sort, pagination).

## 5. User profile page (owner request 2026-10-14)
A «پروفایل من» / "My profile" page in the app (account menu at the bottom of the sidebar, avatar with initials).
The Subscription page from step 3 is a tab of it. Tabs:
- **Account:** display name, username, email, group/role (admin badge for nova-admins), member since.
  Read-only, because the identity comes from AD through realm nova (read-only federation): say so in one
  short line. No password change here.
- **Subscription:** step 3 (plan, credit, expiry, buy/renew, payment history from the ledger: date, plan, amount,
  reference, status).
- **Usage:** this period's tokens and toman per model (qwen3 / glm with their normal names, NO product renaming - owner 2026-10-14)
  , a small daily bar chart, number of conversations.
- **Devices / sessions:** this PC (OS, client version, connected folder roots) and a «خروج از همه دستگاه‌ها» /
  "Sign out everywhere" button that rotates the tenant token (the same action the admin panel already has),
  with a confirm modal and a toast.
- **Preferences:** language fa/en, theme (dark / light), default model. Saved per account (server side, not
  only localStorage, so they follow the user to another PC).
- **Sign out** (fixes HANDOFF open item 3): revokes the token server-side (nova-control logout endpoint) AND clears
  the app's stored token (safeStorage `workspaceTokenEncrypted` + plaintext `workspaceToken`), localStorage
  backend registry and per-user keys, then shows the login window. Test: after sign out, a request with the
  old token gets 401 and a different user who signs in sees none of the first user's data.
  This part touches the desktop client (overlay.py) - say in the report whether a new client build
  (NOVA_CLIENT_REV) is needed and build it, but do not publish it.
API: nova-control `GET /api/me` (account + prefs + wallet summary), `PUT /api/me/prefs`, `GET /api/me/usage`.
fa + en, RTL, Vazirmatn, no letter-spacing; every action has a success/failure toast; the payment-history
table has filter, sort, pagination. Screenshots of every tab into tasks/T25-evidence/.

## Tests
- chat-api unit/integration (real test Postgres): internal auth (missing / wrong secret = 401), agent purchase in
  mock mode, single grant, usage idempotency, chat wallets unaffected.
- nova-control unit: usage extraction for stream and non-stream, 402 when enforced and empty, admins exempt.
- e2e (mock gateway): buy a200 from the app UI -> wallet active -> an LLM call decrements credit.
- Security check list in the report: no route from outside to /internal, secret not logged, amounts only
  from server config, return URL allowlisted.
Do NOT deploy, do NOT change config.env tags. Commit in each repo (no tags). Reply `T25 ready <hashes>`.

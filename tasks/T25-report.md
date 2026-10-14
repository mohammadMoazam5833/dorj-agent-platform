# T25 report — Dorj Agent paid plans

## Result
Implemented the Dorj Agent product as a separate billing dimension on chat-api, exposed it to NovaAgent through nova-control, and added the desktop profile/subscription UI. No deployment and no tag. Model names remain `qwen3` and `GLM`; the supported agent aliases in chat-api are `qwen3` and `glm`.

## chat-api (`/workdir/dorj-chat`)
- Added `product` (`chat` or `agent`) to wallets, payments and ledger and made wallets keyed by `(email, product)` with a backward-compatible migration.
- Added separate configured agent plans `a200`, `a500`, and `a1200`: 200,000 / 500,000 / 1,200,000 toman, each 30 days and with 200,000 / 500,000 / 1,200,000 toman credit. Prices and model access remain server-side configuration.
- Agent wallets use `nova:<username>` as the email key. Agent activation requires credit and an unexpired plan; usage charges only token credit.
- Added the namespace-internal API on port `8010`: plans, wallet, purchase, idempotent usage reporting and history. It authenticates with a shared secret and uses constant-time comparison. The purchase body return URL is ignored; agent payment return redirects to the configured `https://nova.isigpu.local/billing/done` URL.
- Kept chat and agent rows separated in reserve/charge/grant/payment-history paths. Reused the existing Samin request/inquire/confirm and single-open-purchase logic rather than copying payment logic into nova-control.
- Tests: **19 passed** against the real test PostgreSQL.

## nova-control (`/workdir/nova-platform/control`)
- Added tenant-authenticated `/api/me`, `/api/me/prefs`, `/api/me/usage` and `/api/billing/{plans,wallet,purchase,history}`. `/api/billing/purchase` validates only a known plan id and does not forward a return URL.
- Added `/llmapi` pre-call enforcement with `NOVA_BILLING_ENFORCE=false` by default; nova-admins are exempt. Enforcement calls the wallet before starting a new completion and returns HTTP 402 with a Persian message. In-flight requests are not interrupted.
- For normal and streaming completions, nova-control extracts OpenAI/LiteLLM usage, enables `stream_options.include_usage` for streams, and queues an idempotent usage report after the upstream finishes. Reports are batched and retried; request IDs are stable per call.
- `/llmapi` now carries all agent-server LLM traffic and may arrive with only the user's LiteLLM key and no nova identity. In that case nova-control resolves the workspace Secret by constant-time comparison of the LiteLLM key to find the nova user. The key is not logged or returned. If the key cannot be resolved, the request returns 401 rather than creating unattributable billing. Directly authenticated nova calls continue to work.
- Added server-side per-account preferences and admin columns for plan, credit and expiry.
- Tests: **34 passed**, including OCR reconciliation after the CTO's committed changes. The T25 `/llmapi` assertions were reapplied on top of that HEAD.

## NovaAgent desktop (`/workdir/NovaAgent-1.6.6`)
- Added “My profile” / «پروفایل من» in the account menu with Account, Subscription, Usage, Devices and Preferences tabs.
- Subscription shows current plan, credit, expiry, model access, payment history with filtering/sorting/pagination, and the three real plan cards. Choosing a plan and pressing buy locks the other cards while the gateway is opening or payment is pending; success or failure restores the UI.
- Payment opens the gateway in the system browser through the Electron bridge, then polls wallet/history and shows success/failure.
- Account identity is read-only. Preferences are saved through `/api/me/prefs` and follow the account.
- Devices shows local tunnel/connection state and folder roots; sign-out-everywhere has confirmation.
- Sign out calls `/api/auth/logout`, clears web/session storage, and asks the main process to clear safeStorage/plain workspace tokens and restart at login. The bridge validates that the sender is the main window, and `openExternal` accepts only HTTP(S).
- Added fa/en RTL copy using Vazirmatn and the existing dark theme. Evidence screenshots are in `tasks/T25-evidence/` and contain synthetic sample data.
- Typecheck and targeted component/service tests: **5 passed**. Added a Chromium Playwright flow test for profile purchase; it drives the mocked plan purchase, waits for paid wallet state, makes the completion usage report path, and asserts the deducted credit/usage summary: **1 passed**.

## Build
`npm run build:desktop` completed the Vite client and SSR build. It then stopped in the offline environment when the packaging step tried to download `uv` from `api.github.com`. No Electron installer was produced and nothing was published. A new packaged client should be produced on a networked build host after review; the code changes also require the packaged NovaAgent client, not just the remote web bundle.

## Security checks
- No ingress or dorj-site route exposes `/internal/*`; the NetworkPolicy allows port 8010 only from namespace `nova`, while public traffic remains on port 8000 through ingress/dorj-site.
- The internal API requires a nonempty shared secret and constant-time comparison; nova-control loads it from a Kubernetes Secret and does not log it.
- Plan amounts, credit, expiry, model access, and return URL are controlled by chat-api server configuration. Purchase input cannot set an amount or return URL.
- LiteLLM keys are only compared locally in nova-control, are not logged or returned, and unresolvable keys cannot obtain an attributed billing identity.
- Usage reports are idempotent by request id, so retries do not double-charge.
- No secrets, real customer data, tokens or unrelated dirty files are included in this commit.

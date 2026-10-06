# NovaAgent platform

Multi-user, Cursor-class coding agents (a standalone product, separate from Dorj): **https://nova.isigpu.local** (web) and the
**`nova`** CLI. Architecture with C4/sequence/agent-loop diagrams: [`docs/architecture.html`](docs/architecture.html).

| Part | Path | What it is |
|---|---|---|
| Control plane | `control/` | `nova-control` (FastAPI): identity, per-user workspaces on Kubernetes, LiteLLM key broker, HTTP+WebSocket proxy, metrics, OTel |
| CLI | `cli/` | `nova` — username/password login, logout, `run`, `chat`, `conversations`, `ws`, `models`, `usage` |
| Ops | `deploy/` | Grafana dashboard. Kubernetes manifest + script live in k8s-deploy: `k8s/nova-manifest.yml.tmpl`, `scripts/140-deploy-nova.sh` |
| Web UI | `/workdir/NovaAgent` | NovaAgent (OpenHands Agent Canvas fork), built with base path `/`, telemetry off |

## How a request flows
browser / desktop / CLI → ingress-nginx (TLS) → `nova-control` (Keycloak-backed tenant-token auth) →
the caller's own agent-server pod in `nova-ws` (or the reverse-workspace gateway for local tools) →
LiteLLM (per-user virtual key) → model servers. The browser receives only its own tenant-token boot
context plus an HttpOnly cookie; workspace session and LiteLLM keys remain server-side. CLI/desktop
credentials stay locally protected, and `POST /api/auth/logout` revokes the tenant token server-side.

## CLI
```bash
pipx install ./cli            # or: pip install --user ./cli
export NOVA_CA_BUNDLE=/path/to/isigpu-root-ca.crt   # internal CA
nova login                    # prompts for username/password
nova run "fix the failing auth tests" --model qwen3
nova chat
nova ws status | start | stop
nova usage
```
Exit codes: `0` ok, `1` error, `2` quota/budget, `3` auth.

## Develop
```bash
cd control && pip install -r requirements.txt pytest && python -m pytest -q
```
Deploy: `k8s-deploy/scripts/140-deploy-nova.sh [--rebuild] [--skip-sso]` (bump `NOVA_CONTROL_TAG` per rebuild).

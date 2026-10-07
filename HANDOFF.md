# NovaAgent platform + platform.isigpu.local — handoff

_Written 2026-10-07 from the live system and the git history. No secrets in this file._

## Kholaseh (Finglish)

NovaAgent yek **app-e desktop** baraye moshtari-hast: Windows `.exe` va Linux `.deb`/AppImage.
- Moshtari app ro nasb mikoneh, ba hesab-e AD-e sazman login mikoneh, va agent **rooye system-e khod-e moshtari** kar mikoneh: poosheh-ha, terminal, Python/Node-e hamoon system.
- Model-ha (Qwen/GLM) az LiteLLM-e khodemoon miyan, va moshtari hich setup-i nadareh.
- Server rooye Kubernetes dar `https://nova.isigpu.local` ejra misheh.
- Code-e client va gateway az repo-ye git-e NovaAgent-e, ba overlay-haye kochik va mostanad.
- Noskheh-ye live: client `1.6.5+nova.2`.

**Mored-e foori:** repo-ye NovaAgent ba'd az akharin build be `1.6.6` va "Dorj Agent" rebrand shodeh, va ba'zi fix-ha ro khodesh ezafeh kardeh. Pas `140-deploy-nova.sh --rebuild` alan **mishkaneh**. Bakhsh-e "Blocker" ro bekhoonid.

**platform.isigpu.local (Dorj):** taghirat-e akhir ro dar §9 bebinid:
- **Storage:** layout-e yek-ja (`shared-models`), quota, alert-haye Ceph.
- **Admin panel:** `v70`.
- **UI-e dorjService:** `0.3.31`.
- **Login:** modal-haye jadid.
- **Do outage-e infra:** etcd va Ceph.

13 commit-e `dorjService` hanooz push nashodan.

---

## 1. Current live state

| Component | Version | Notes |
|---|---|---|
| Desktop client | `1.6.5+nova.2` | Built from NovaAgent `1.6.5` + `desktop/overlay.py`; `.exe` (NSIS), `.deb`, AppImage on `/download/` |
| nova-control | `0.4.4` deployed | `config.env` already says `0.4.5` (commit `2da16ed`, token revocation + server-side logout) — **not deployed yet** |
| nova-gateway | `0.1.2` | NovaAgent gateway + `patch_gateway.py` + `ws_close_codes.py` |
| agent-server | `1.38.0-nova11` | PyPI `openhands-agent-server==1.38.0` + NovaAgent `sidecar_bridge` + `nova_server_patches` + NovaAgent `SOUL.md` |
| nova-downloads | `1.6.5-nova.2-2` | NovaAgent `scripts/download-site`, rendered by `download/render.py` |
| Keycloak realm | `nova` | AD (`OU=platform`) federated **read-only**; theme `keycloak-theme/nova`; admins = group `nova-admins` |
| Tags | `nova-platform` → `nova-0.5.1` (latest, includes `2da16ed`); my last: `nova-0.5.0`. `k8s-deploy` → `v1.1015.0` (my last: `v1.1001.0`) | |

URLs:
- `https://nova.isigpu.local/download/` — public download page (installers, CLI wheel, root CA, `SHA256SUMS`)
- `https://nova.isigpu.local/admin` — admin panel (NovaAgent's own UI; sign-in = AD user in `nova-admins`)
- `https://nova.isigpu.local/` — no web product: a browser without the app's token is redirected to `/download/`
- Grafana dashboard uid `nova-agent`; Prometheus rules `nova.slo`

**DNS:** the record `nova.isigpu.local → 172.16.50.202` must exist on the AD DNS (`dc-srv`, 172.16.10.200). Adding it from here was blocked; the owner adds it. Without it customers need a hosts entry.

## 2. Architecture (what runs where)

```
Customer PC                               Kubernetes (ns nova / nova-ws)
───────────                               ───────────────────────────────
NovaAgent desktop app (Electron, thin)    ingress-nginx (TLS, nova-tls)
 ├─ sign-in window ──POST /api/auth/login──► nova-control ── Keycloak realm "nova" (password grant, client nova-desktop)
 ├─ main window ── UI bundle (?t=token) ──►      │  tenant registry = Secret nova-customers (NovaAgent .nova-customers.json format)
 ├─ local-tools-sidecar :18765 (files/shell)     │  /api/*, /sockets/* ──► ws-<user> agent-server pod (nova-ws, scale-to-zero 30 min)
 └─ reverse tunnel ──WSS /customer-workspace──► nova-control ──► nova-gateway (1 replica) ◄── agent-server /c/<user>/v1 (sidecar_bridge)
                                                 │  /llmapi/* ──► LiteLLM (user's own virtual key; models qwen3, glm)
                                                 └─ /admin (NovaAgent admin UI, k8s-backed API)
```

- The agent "brain" (agent-server) runs in the cluster; **its file and terminal tools run on the customer's PC** through `sidecar_bridge` → gateway → tunnel → sidecar. `nova_server_patches` makes that work for the stock agent-server. It handles:
  - customer roots;
  - no server-side mkdir/git-init/worktree for customer paths;
  - the `isdir` checks;
  - `/llmapi` → internal LiteLLM;
  - a reasoning-effort map;
  - `<USER_MACHINE>` context (OS, home, shell, runtimes from the sidecar hello);
  - NovaAgent identity (SOUL / self-documentation).
- Token = the tenant ("workspace") token from `/api/auth/login`. It is used as the UI session key, as the tunnel credential, and by the agent-server to reach the gateway. The admin panel's "sign out devices" rotates it. There is no cookie-by-name and no default tenant (the VM version's holes).
- Full design doc: `docs/architecture.html` (Artifact `https://claude.ai/artifact/KKnEsvnRn28q677Va2dXSd`; last edited in commit `2da16ed`).

## 3. Repos and paths

| Path | What |
|---|---|
| `/workdir/nova-platform` | Our code (git, tags `nova-*`): `control/` (FastAPI edge), `agent-server/` (image + `nova_server_patches`), `gateway/` (wrapper + `patch_gateway.py`), `patches/ws_close_codes.py`, `desktop/` (`build-client.sh`, `overlay.py`, `login-prompt.html`, `loading.html`, `org-root-ca.crt`), `download/` (`render.py`, nginx), `cli/` (`nova`), `e2e/`, `deploy/` (Grafana), `docs/` |
| `/workdir/NovaAgent` | Upstream product source (`github.com/mohammadMoazam5833/NovaAgent`). Client, gateway, sidecar, admin UI, download page and `SOUL.md` all come from here. **Never edited in place by us** — changes go through overlays/patches that assert their anchors |
| `/workdir/k8s-deploy` | Deploy toolkit (git, tags `v1.*`): `scripts/140-deploy-nova.sh`, `scripts/141-setup-nova-realm.sh`, `k8s/nova-manifest.yml.tmpl`, `keycloak-theme/nova`, `config.env` (`NOVA_*`) |
| `/workdir/NovaAgent-bugfix-doc.md` | Customer bug report (Wayland splash, session_replaced) — fixed in `1.6.5+nova.2` |

## 4. Build and deploy

```bash
cd /workdir/k8s-deploy
./scripts/141-setup-nova-realm.sh            # realm, AD federation, clients, nova-admins (idempotent)
./scripts/140-deploy-nova.sh [--rebuild] [--skip-sso]
```
`140` builds and pushes only what is missing in the local registry, unless `--rebuild` is given:
- agent-server image: copies NovaAgent `SOUL.md`;
- gateway image;
- nova-control image: UI bundle built from NovaAgent + `nova-admin.mjs`;
- client installers: `desktop/build-client.sh`, using the wine builder image;
- download image.

It then applies the manifest and secrets and runs a smoke test.

Version knobs in `config.env`:

| Knob | Meaning |
|---|---|
| `NOVA_CONTROL_TAG`, `NOVA_GATEWAY_TAG`, `NOVA_AGENT_SERVER_TAG` | Image tags |
| `NOVA_CLIENT_REV` | Client version becomes `<NovaAgent version>+nova.N`. Use `+`, not `-`: a `-` turns into a Debian `~`, which apt treats as a downgrade |
| `NOVA_DOWNLOADS_REV` | Republish the download page for the same client version |

Gotchas:
- Run kubectl/curl with proxy env unset (`env -u HTTPS_PROXY -u HTTP_PROXY ...`) on this host.
- Images: apt during the agent-server build goes through the proxy (no apt mirror); pip/npm go through Nexus; base images are mirrored in `172.16.50.89:5000`.
- Docker tags cannot contain `+` (script 140 replaces it).

## 5. Tests (`/workdir/nova-platform/e2e`)

Credentials only via env (`E2E_USER`, `E2E_PASS`; test user `clusteradmin` — password from the owner). Also `NOVA_CA_BUNDLE` (org root CA) and `NOVA_PY` (python with httpx) for the CLI test.

| Test | How | Proves |
|---|---|---|
| `server.spec.js` | `npx playwright test -c playwright.config.js server.spec.js` | No web product, sign-in contract, token-only identity (forged headers / VM cookie ignored), UI boot with company LLM preset, `/llmapi` allowlist |
| `cli.spec.js` | same | `nova login/whoami/models/usage/run/logout` |
| `admin.spec.js` | same | Admin sign-in, dashboard, users, conversations, logs, LLM |
| `download.spec.js` | same | Public page, every file matches `SHA256SUMS` |
| `identity.spec.js` | same | Agent answers "NovaAgent" (en + fa), never "OpenHands" |
| `desktop.spec.js` | `./run-desktop.sh <shots>` | Installs the `.deb` in Ubuntu 22.04 (read-only `/dev/shm`), sign-in, UI, agent writes a file **on that machine**, `<USER_MACHINE>` has its OS/home |
| `wayland.spec.js` | `./run-wayland.sh [deb] [shots]` (+ `NOVA_EXPECT=stuck` for an old build, `NOVAAGENT_TEST_IGNORE_READY_TO_SHOW=1`) | Under weston: main window revealed (fallback). `+nova.1` reproduces the deadlock |
| `tunnel-replace.sh` | `./tunnel-replace.sh` | Same account on two PCs: first gets `4001 session_replaced`, stops; second stays; one replacement |
| `load30.sh` | `./load30.sh 30` (cleans up) | 30 tenants/PCs/workspaces, per-tenant isolation; cold start p50 33 s / p95 42 s. **Check etcd size before running** (§7) |

Control-plane unit tests: run `pytest` in the `nova-control` image against `control/` (13 tests).

## 6. Blocker — NovaAgent upstream moved to 1.6.6 "Dorj Agent"

New upstream commits after `1.6.5+nova.2` was built:
- **`0b8de83` rebrand Nova Agent → Dorj Agent:**
  - `productName "Dorj Agent Client"`, artifact names `Dorj Agent-Client-…` (with a space), new icons;
  - `package.json` `1.6.6`.
- **`8761d71` "harden workspace auth and session replacement":**
  - `ws.mjs` now sends and decodes close codes. Its API is `onClose(info)` with an object, where ours was `onClose(code, reason)`.
  - `client.mjs` stops on `4001`.
  - The token is stored encrypted with `safeStorage` (`workspaceTokenEncrypted`).

Effect, verified by a dry run on 2026-10-07:
- **Client:** `desktop/overlay.py` fails, because `patches/ws_close_codes.py` cannot find its `ws.mjs` anchors. Further edits will also miss: the `client.mjs` `onClose`, the thin-config product/artifact names, and the `forgetWorkspaceToken` logic, which only deletes the plaintext `workspaceToken`.
- **Gateway:** `patch_gateway.py` still applies and is still needed. The upstream gateway still closes a replaced session without 4001 and still has the shared-id cross-tenant mix-up. `ws_close_codes.py` fails there too.
- **Result:** `140-deploy-nova.sh --rebuild`, or any client/gateway rebuild, stops until the patches are reconciled. The live system is unaffected.

What to do:
1. **Branding:** confirm with the owner whether the product name is now "Dorj Agent" (memory says Nova is a product separate from Dorj — the rebrand may reverse that). It affects:
   - the `login-prompt.html` / `loading.html` texts;
   - SOUL / identity (`nova_server_patches.NOVA_SOUL`, the realm display name and the theme);
   - the download file names, which `render.py` matches by pattern (`NovaAgent-…`);
   - `identity.spec.js`.
2. **`patches/ws_close_codes.py`:** make it a no-op when `ws.mjs` already encodes close payloads. Upstream now does. Keep only what upstream lacks: echoing the peer code and `onClose` for socket drops (1006). Then adapt `overlay.py`'s `client.mjs` edit to `onClose(info)`. Upstream stops only on 4001; we stop on any 4xxx, exit 3, which shows the "account open elsewhere" dialog, and we ignore stale sockets.
3. **`overlay.py` `forgetWorkspaceToken`:** also delete `workspaceTokenEncrypted`.
4. **Thin config:** re-check every anchor against `1.6.6`. Decide whether artifact names with a space are wanted; URLs and NSIS handle them, but the download page and the tests expect `NovaAgent-…`.
5. **Rebuild and re-run the whole e2e table** before publishing. Bump `NOVA_CLIENT_REV` or reset it for `1.6.6+nova.1`.
6. **Gateway:** consider upstreaming `patch_gateway.py` (the cross-tenant response mix-up and 4001) to NovaAgent so it can be dropped here.

## 7. Operations notes

- **etcd:** quota 2 GiB, counted on the DB *file*.
  - **Outage on 2026-10-05:** `NOSPACE` → all writes failed and the control-plane VIP `.201` dropped.
  - **Cause:** write amplification. The admin-panel Falco ring-buffer ConfigMap is rewritten per alert, plus kube-vip ARP alerts. nova-control's per-call API connections contributed; fixed in 0.4.2.
  - **Recovery** (compact, defrag, disarm) and open items are in `k8s-deploy/CLAUDE.md`. Check `etcdctl endpoint status` before load tests. Since then `k8s-deploy` has added quota/alerts work for Ceph (§261); I have not verified whether the etcd follow-ups were done.
- **Admin panel actions:**
  - start/stop/restart workspace → scale/rollout;
  - "sign out devices" → token rotation + workspace restart;
  - delete → registry removal + scale to 0; the PVC is kept.
- **The `godarzi` account:** two machines on old clients were ping-ponging (151 replacements in 15 min on 2026-10-05). Both machines must install `+nova.2` or later, and remove the customer's XWayland workaround: `~/.local/bin/novaagent-x11.sh` and the `.desktop` override in `~/.local/share/applications/`.
- **Restarts:** a Keycloak restart is needed for every theme change (`THEME_NAME=nova scripts/88-…`). It affects all apps' logins, so ask first. The GLM restart (~48 min) needs explicit owner approval.

## 8. Open items / backlog

- **Blocker §6:** reconcile with upstream `1.6.6`, then rebuild.
- **Deploy nova-control `0.4.5`:** commit `2da16ed`, token revocation + server-side logout. Not deployed yet; run its e2e first.
- **Verification gaps:**
  - The Windows `.exe` has never been run on a real Windows PC; wine's NSIS silent install aborts.
  - Windows-specific machine context (PowerShell, `C:\` paths) is implemented but untested.
  - The Wayland fix was verified under weston only; please confirm on real GNOME Wayland.
- **DNS:** the record on `dc-srv` (owner).
- **Leftovers in Dorj SSO:** the Dex client `nova-cli` and the `nova` callback. Removing them restarts Dex and the platform oauth2-proxy, so ask first.
- **Admin panel:** the tables (NovaAgent UI) lack the filter/sort/pagination the project requires.
- **Billing:** `NOVA_MONTHLY_BUDGET_USD=0`, so budgets are not enforced; qwen3 has no LiteLLM price (spend shows 0). The owner said pricing is not needed for now.
- **Customer-side safety:**
  - The sidecar roots are the whole home folder.
  - There is no per-command confirmation on the customer PC.
  - Review item 1 in `/workdir/novaagent` still applies.
- **Scale:** the gateway is a single replica (tunnels live in memory). Fine for ~30 customers; HA needs consistent routing by tenant.

## 9. platform.isigpu.local (Dorj) — changes and state

Live images:
- `dorj-service` **0.3.31** (React UI, `DORJ_SERVICE_TAG`);
- `kubeflow-admin-panel` **v70**;
- `fplatform-centraldashboard` **v3** (Polymer UI removed).

Code lives in:
- `/workdir/dorjService` (branch `goodarzi-phase3`);
- `/workdir/k8s-deploy` (admin panel `kubeflow-admin-panel/`, Keycloak themes `keycloak-theme/`, scripts, `RUNBOOK.md`, `CLAUDE.md`).

### 9.1 Latest changes (2026-10-06 / 07)

**Storage / Ceph — after the Ceph full outage (RUNBOOK §261)**

| Commit | Change |
|---|---|
| `ce15063`, `7e76a50` | RUNBOOK §261: Ceph hit `full_ratio` 95% → every write froze → Keycloak's Postgres hung → 503 on every SSO login (2026-10-06 21:43 → 07 ~05:00 UTC). Diagnosis + recovery commands + prevention |
| `5264a46` | `scripts/142` 8 Ti ResourceQuota on `model-mirror` |
| `b8de34e` | `scripts/143` Ceph capacity alerts emailed to postmaster |
| `a5b1c05`, `35d4644` | Ceph free-space guard before every model write (admin panel v68) and in `scripts/47` |
| `72a0978`, `58bc0b8` | Single model storage layout: `shared-models` only (v69); staged downloads into `shared-models/.incoming` then atomic move (v70) |
| `113b8d1`, `6ca56cf`, `cab6a36`, `d6096be` | `scripts/144` migrates models to the shared layout (no partial downloads, pod-name length fix, deletes the succeeded Job before the mirror PVC) |

**Admin panel and login**

| Commit | Change |
|---|---|
| `b64ee27` | HF token settings for gated model downloads (+ dorjService `f03a8b3` HF token card) |
| `fdba384`, `60d08b0` | Keycloak login theme: contact-admin modal matches the password modal; category select → choice cards |

**dorjService UI (2026-10-06, 15 commits after tag `dorj-0.3.18`):**
- Charts are now ECharts (dashboard, usage, monitoring), with a loading skeleton, an empty state and a single legend.
- The user account moved into the header widget (prefs, password, sign-out).
- The home icon was removed; the Dorj logo now goes home.
- New change-password modal with strength validation.
- YAML tab removed from notebook details.

### 9.2 Earlier platform work in this session (2026-10-03 … 05)

| Area | What | Where |
|---|---|---|
| dorjService cutover | React UI serves platform.isigpu.local (phase 3.7, rollback guard) | scripts 135/138 |
| Polymer UI | Removed, backup kept | tag `polymer-ui-removed`, `backups/polymer-ui-removal-20261004-161038` |
| Sidebar / UX | Email, backups, contributors, admin panel, Persian digits / Jalali, collapse toggle; Access tab fixes; spinners | dorjService `0.3.14`–`0.3.18` |
| Logout | Real sign-out: Keycloak end-session → `/oauth2/sign_out` (+ Dex post-logout URIs) | `src/lib/session.js`, script 134 `--logout-uris` |
| Login theme | `dorj-v3`: split layout, 3D floating "D", formal wording, mobile = form only, docs panel on the login page | `keycloak-theme/dorj-v3` |
| Dex | Key-rotation window fixed (signingKeys 168 h, istiod JWKS refresh 15 s) | script 139 |
| Admin panel | Keycloak 503 → internal Service (v64/65), skip 404 groups, SGLang `SGLANG_TRACE_LEVEL=2` (v66) | `kubeflow-admin-panel/server.py` |
| Tempo | PVC 80Gi, 8Gi memory, trace sharding; UI guard for giant traces | script 118, `Monitoring.jsx` |
| etcd outage 2026-10-05 | NOSPACE → all writes failed, VIP `.201` down; compact+defrag+disarm; root cause = write amplification (Falco ring-buffer ConfigMap) | `CLAUDE.md` (etcd section) |

### 9.3 Open items on the platform

- **Push dorjService:** 13 commits are ahead of `origin/goodarzi-phase3`. Pushing is done by the owner — the assistant is not allowed to push.
- **GLM restart** (~48 min) to apply `SGLANG_TRACE_LEVEL=2`. Code is done; it waits for the owner's go.
- **etcd follow-ups:**
  - debounce the admin-panel Falco ring-buffer write;
  - a Falco exception for kube-vip's ARP;
  - periodic defrag;
  - a DB-size alert;
  - quota 8 GiB.

  None existed as of 2026-10-05 (see `CLAUDE.md`). The Ceph work above is a separate storage problem and does not cover etcd.
- **Ceph:** after §261 data was deleted and quotas/alerts added. Watch `ceph osd df`: the `full_ratio` must be back at 0.95 (RUNBOOK §261 recovery set it to 0.96 temporarily).
- **Root CA:** 11 app bundles and user bundles still carry only the old root (memory "Dex root-CA encoding").
- **Admin panel tables** must have filter/sort/pagination (project rule) — check new tables (HF token, storage) too.

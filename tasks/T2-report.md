# T2 Report — Dorj Agent rebrand

- Date: 2026-10-07
- Status: **READY — T2 checkpoint 2; items 1–11 complete; versions prepared; no deploy/tag/push**
- Scope completed: items 1–11 (T2)
- Rule compliance: no Keycloak/theme redeploy, no Keycloak restart, no GLM/vLLM restart, no PVC/data deletion, no git push, no deployment, no tag.
- `identity.spec.js` was not changed in this checkpoint.

## T1b carry-in

- Commit: `db921c3` — `test(e2e): accept Dorj Agent installer names`
- Files: `e2e/download.spec.js`, `e2e/run-desktop.sh`, `e2e/run-wayland.sh`, `tasks/T1-report.md`
- Result: `download.spec.js` PASS (`1 passed (5.9s)`), with both `Dorj-Agent-Client-*` and legacy `NovaAgent-*` accepted.

## Item 1 — Desktop app icon

- Status: **DONE**
- Commit: `b20b515` — `feat(desktop): apply platform Dorj Agent icon`
- Source: `/workdir/k8s-deploy/keycloak-theme/dorj-v3/login/resources/img/dorj-d.png`
- Generated assets:
  - `desktop/brand/desktop/dorj-agent.png` (512×512)
  - `desktop/brand/desktop/dorj-agent.ico` (16, 24, 32, 48, 64, 128, 256)
  - `desktop/brand/linux/<size>x<size>.png` for 16, 24, 32, 48, 64, 128, 256, and 512
  - `desktop/brand/dorj-d.png`, `desktop/brand/dorj-wordmark.png`, `desktop/brand/dorj-agent-favicon.png`
- Wiring: `desktop/overlay.py` copies the generated assets into the staged Electron
  `build-resources` tree and `electron/brand`, asserts the existing
  `icon: "icon.ico"`, `icon: "icons"`, and `Icon: "dorj-agent"` anchors, replaces legacy icon
  assets, and packages the three HTML brand assets through `electron-builder.thin.config.mjs`.
- Verification:
  - `python3 -m py_compile desktop/overlay.py`
  - Applied the overlay to a temporary staged copy of the current NovaAgent HEAD; it succeeded.
  - Staged tree contained only the new Linux icon sizes plus `icon.png`/`icon.ico`; no legacy icon size directory remained.
- Evidence: `tasks/T2-evidence/t2-item1-icon-512.png`

## Item 2 — Desktop login prompt and loading screen

- Status: **DONE**
- Commit: `eb26f56` — `feat(desktop): redesign login and splash with Dorj platform theme`
- Files:
  - `desktop/login-prompt.html`
  - `desktop/loading.html`
- Design implementation:
  - Split login: dark brand half with the platform D/wordmark and light `400px` form half.
  - Navy/lapis palette: `#0B1B3A`, `#1E3A8A`, `#16295e`.
  - Gold accent: `#E8A317` / `#F4C24B`.
  - Light form surface: `#f5f7fb`.
  - Local Vazirmatn fonts only.
  - Under `900px`, only the form remains.
  - Loading splash uses navy gradient, platform D, wordmark, and gold progress accent.
- Product text:
  - English: `Dorj Agent`
  - Farsi: `دستیار دُرج`
  - Titles are `Dorj Agent`; local rendered-text checks found no `NovaAgent`, `Nova`, or `OpenHands`.
- Contracts retained:
  - Login IPC: `thin-login-prompt` with `{action:"save", username, password}` or `{action:"quit"}`.
  - Login element IDs: `username`, `password`, `save`, `quit`, `lang`, `eye`, `error`, `msg`.
  - Loading globals/APIs: `window.__setLoadingStatus`, `window.desktopBoot`.
  - Loading IDs/classes: `status`, `log`, `copy-logs`, `quit-button`, `details`, `lang`, `body.failed`, `body.details`.
- Verification:
  - `git diff --check` clean.
  - Overlay applied successfully to a temporary staged copy of current NovaAgent HEAD.
  - Local Chrome render check: title `Dorj Agent`; Farsi heading `ورود به دستیار دُرج`; translated loading status; all brand images loaded.
- Evidence:
  - `tasks/T2-evidence/t2-item2-login-1x.png`
  - `tasks/T2-evidence/t2-item2-login-2x.png`
  - `tasks/T2-evidence/t2-item2-loading-1x.png`
  - `tasks/T2-evidence/t2-item2-loading-2x.png`
- CTO checkpoint-1 review fixes:
  - Login language switcher moved into a separate top bar, clear of the heading and subtitle.
  - Login geometry checked at 1x and 2x: no overlap, no clipped/overflowing content.
  - Loading screen now shows only the Dorj wordmark (no duplicate D-mark); vertical spacing and
    collapsed splash height reduced.
  - Desktop icons regenerated without the thin cream/gold border, using the navy rounded square.

## Item 3 — Web UI bundle

- Status: **DONE (build verified; not deployed)**
- Files:
  - `web/patch-ui.sh`
  - `web/finalize-ui.sh`
  - `web/brand/`
  - `k8s-deploy/scripts/140-deploy-nova.sh`
- Implementation:
  - Build-time patching now applies the platform D mark, favicon asset set, page title,
    meta description, and web manifest name.
  - Locale values and post-build JS assets are rewritten to **Dorj Agent**, while preserving
    required internal protocol/package references (`X-OpenHands-*`, GitHub/OpenHands package URLs).
  - Deploy script now runs `finalize-ui.sh` after the UI build, so generated deployments use the
    same customer-visible cleanup.
  - Temporary UI build succeeded, and the finalizer passed.
  - Built title/description/manifest verified as `Dorj Agent`; manifest theme is navy `#0B1B3A`.
  - All 15 built locale JSON files were checked and contained no visible `OpenHands`/`NovaAgent`.
- Evidence: build/finalizer verification (generated `control/web` is intentionally not committed).

## Item 4 — Admin UI

- Status: **DONE (render verified; not deployed)**
- Files:
  - `control/nova_control/adminpage.py`
- Implementation:
  - Page title is now `Dorj Agent | پنل مدیریت`.
  - Login and header use the platform Dorj favicon/logo and `Dorj Agent` / «دستیار دُرج».
  - Admin palette now uses platform navy/lapis, gold accent, and light surface-compatible text colors.
  - Chart/spark colors were moved off the old orange/purple theme to Dorj gold/lapis.
  - The rendered HTML is guarded against visible `NovaAgent`/`OpenHands` remnants.
- Verification:
  - `python3 -m py_compile control/nova_control/adminpage.py`
  - Rendered the generated admin source with `NOVA_ADMIN_PAGE`/`NOVA_WEB_ROOT`; title, logo data URI,
    navy/gold tokens, and Persian product name were present; old product names and old colors absent.
  - Existing relevant pytest modules require a control-plane dependency environment not installed on
    this host; runtime branding was therefore validated by direct render and compile checks.

## Item 5 — Download page

- Status: **DONE (render verified; not deployed)**
- Files:
  - `download/render.py`
  - `web/brand/dorj-wordmark.png`
- Implementation:
  - Download page now uses the platform D favicon, wordmark logo, navy/lapis palette, gold CTA,
    navy terminal panels, and matching theme color.
  - Rendered artifact names remain `Dorj-Agent-Client-*`.
  - Rendered HTML is guarded against `NovaAgent`, `OpenHands`, and old brand color remnants.
- Verification:
  - `python3 -m py_compile download/render.py`
  - Rendered from the current NovaAgent HEAD into `/tmp/t2-download` with `https://nova.isigpu.local`.
  - Verified 11 visible/code occurrences of `Dorj Agent`, no legacy product names/colors, and the
    copied wordmark/favicon assets.
- Evidence: `tasks/T2-evidence/t2-item5-download-index.html`

## Item 6 — Agent identity

- Status: **DONE (static verification; full identity e2e intentionally not run)**
- Files:
  - `agent-server/SOUL.md`
  - `agent-server/nova_server_patches/__init__.py`
  - `e2e/identity.spec.js`
  - `k8s-deploy/scripts/140-deploy-nova.sh`
- Implementation:
  - Product SOUL and runtime identity constants now define the English name **Dorj Agent** and the
    formal Persian name **«دستیار دُرج»**.
  - The runtime self-documentation and customer-machine prompt use the same product identity.
  - The identity e2e now checks the English reply for `Dorj Agent`, the Persian reply for
    «دستیار دُرج», and rejects `OpenHands`/`NovaAgent`.
  - Deployment no longer copies upstream's old `SOUL.md` over the staged product identity.
- Verification:
  - `python3 -m py_compile agent-server/nova_server_patches/__init__.py`
  - `bash -n k8s-deploy/scripts/140-deploy-nova.sh`
  - Static checks confirmed the new names in SOUL, runtime constants, and e2e; user-facing runtime
    identity strings contain no old product name.

## Item 7 — Keycloak realm `nova`

- Status: **DONE (idempotent realm setup executed; no restart/theme redeploy)**
- Files:
  - `k8s-deploy/scripts/141-setup-nova-realm.sh`
- Implementation:
  - Realm display name is now `Dorj Agent`; HTML display is `<b>D</b>orj Agent`.
  - Client display names are now `Dorj Agent Web`, `Dorj Agent CLI`, and `Dorj Agent sign-in`.
  - Internal IDs (`nova`, `nova-web`, `nova-cli`, `nova-desktop`) are intentionally unchanged.
  - The realm frame remains the existing `nova` theme. The platform `dorj-v3` theme was not switched
    because its documentation/contact panel is platform-specific rather than product-neutral.
- Verification:
  - `bash -n scripts/141-setup-nova-realm.sh`
  - Ran the idempotent script successfully; it reported a successful read-only LDAP sync and no
    Keycloak restart/theme redeploy. Credentials were not printed or stored in evidence.

## Item 8 — CLI

- Status: **DONE**
- Files:
  - `cli/pyproject.toml`
  - `cli/nova_cli/main.py`
  - `cli/nova_cli/auth.py`
- Implementation:
  - User-facing help, version, chat banner, and package description now say **Dorj Agent**.
  - Added the `dorj-agent` console command while retaining the existing `nova` command.
  - Internal package/module name (`nova-cli` / `nova_cli`) remains unchanged.
- Verification:
  - `python3 -m py_compile` on CLI modules.
  - Packaging check confirmed both console scripts point to the same entrypoint.
  - `PYTHONPATH=cli dorj-agent --help` rendered `Dorj Agent CLI` successfully.

## Item 9 — Grafana dashboard

- Status: **DONE (config verified; not deployed)**
- Files:
  - `deploy/grafana-dashboard-nova.json`
- Implementation:
  - Dashboard and panel titles now use `Dorj Agent`.
  - Dashboard UID remains `nova-agent`, so existing links and provisioning behavior are unchanged.
- Verification:
  - Parsed the JSON and asserted title/UID and panel titles.

## Item 10 — E2E brand checks

- Status: **DONE (item-local/static verification; full e2e intentionally not run)**
- Commit: `0d4a019` — `test(e2e): assert Dorj Agent branding`
- Files:
  - `e2e/brand.spec.js`
  - `e2e/download.spec.js`
  - `e2e/server.spec.js`
  - `e2e/admin.spec.js`
  - `e2e/desktop.spec.js`
  - `e2e/wayland.spec.js`
  - `e2e/playwright.config.js`
  - `e2e/load30.sh`
  - `e2e/run-desktop.sh`
  - `e2e/run-wayland.sh`
- Implementation:
  - Added `brand.spec.js` for customer-visible brand checks.
  - It verifies unauthenticated `/` lands on `/download/`, then checks the direct download page,
    `/admin` login, and the authenticated UI bundle loaded with `?t=<token>`.
  - For each page it checks `document.title` and `document.body.innerText` for `Dorj Agent` /
    «دستیار دُرج» and rejects visible `NovaAgent` / `OpenHands`.
  - The UI-brand test signs in only through `/api/auth/login` with `E2E_USER` / `E2E_PASS`; it does
    not print or persist credentials.
  - `download.spec.js` now requires `Dorj-Agent-Client-*` for the primary installer and checks the
    download title/visible text without old branding.
  - Desktop specs default to `/usr/bin/dorj-agent-client`, while the runner scripts retain explicit
    legacy-installer fallbacks for backward compatibility with previously installed clients.
- Verification:
  - `node --check` on all changed JavaScript specs.
  - `bash -n` on changed shell e2e helpers.
  - `git diff --check` clean.
  - Evidence: `tasks/T2-evidence/t2-item10-static-checks.txt`
  - Full e2e was intentionally not run before deployment, per checkpoint-2 instructions.

## Item 11 — Docs and prepared versions

- Status: **DONE (docs/version preparation only; no deploy)**
- Commits:
  - nova-platform: `99ac89c` — `docs: establish Dorj Agent product identity`
  - k8s-deploy: `d024126` — `build(nova): prepare Dorj Agent release versions`
  - k8s-deploy: `044c631` — `docs(runbook): document Dorj Agent product identity`
- Files:
  - `README.md`
  - `HANDOFF.md`
  - `/workdir/k8s-deploy/RUNBOOK.md`
  - `/workdir/k8s-deploy/config.env.example`
  - `/workdir/k8s-deploy/config.env` (working tree only; this local file is not tracked)
- Documentation:
  - Product name is now **Dorj Agent** / «دستیار دُرج».
  - README and HANDOFF identify the desktop/web product, CLI alias `dorj-agent`, and compatibility
    with the existing `nova` command.
  - HANDOFF records the prepared versions and the outstanding deployment/e2e work.
  - RUNBOOK §263 records the customer-facing identity and unchanged internal identifiers.
- Prepared versions (not deployed):
  - `NOVA_CLIENT_REV="2"` → client `1.6.6+nova.2`
  - `NOVA_CONTROL_TAG="0.5.0"`
  - `NOVA_AGENT_SERVER_TAG="1.38.0-nova12"`
  - `NOVA_DOWNLOADS_REV="2"`

## Deployment plan and rollback

- Pre-deploy etcd check: **PASS**. Cluster endpoint status showed DB sizes `646 MB`, `644 MB`, and
  `640 MB` (all below the `1.5 GiB` stop threshold); all three members were in sync with no errors.
- Owner-approved deployment tags:
  - nova-control: `0.4.4` → `0.5.0`
  - agent-server: `1.38.0-nova11` → `1.38.0-nova12`
  - nova-downloads: `1.6.6-nova.1-1` → `1.6.6-nova.2-2`
  - desktop client: `1.6.6+nova.1` → `1.6.6+nova.2`
- Deployment command: `cd /workdir/k8s-deploy && ./scripts/140-deploy-nova.sh`

### Exact rollback

Run these only if deployment, smoke, download e2e, wrong-password API check, or visible-brand scan
fails. Use the current live tag listed above.

```bash
K=/workdir/k8s-deploy/inventory/mycluster/artifacts/kubectl
C=/workdir/k8s-deploy/inventory/mycluster/artifacts/admin.conf

"$K" --kubeconfig="$C" -n nova set image deployment/nova-control control=172.16.50.89:5000/nova-control:0.4.4
"$K" --kubeconfig="$C" -n nova set image deployment/nova-downloads nginx=172.16.50.89:5000/nova-downloads:1.6.6-nova.1-1

for d in $("$K" --kubeconfig="$C" -n nova-ws get deploy -o name); do
  "$K" --kubeconfig="$C" -n nova-ws set image "$d" agent-server=172.16.50.89:5000/nova-agent-server:1.38.0-nova11
done

"$K" --kubeconfig="$C" -n nova rollout status deployment/nova-control deployment/nova-downloads --timeout=240s
"$K" --kubeconfig="$C" -n nova-ws rollout status deployment --timeout=240s
```

Note: Keycloak realm display names were already changed idempotently by script 141 and are not
image tags; rollback above intentionally restores the approved control-plane/download/agent-server
images.

## Final readiness

- Items 1–11 are complete.
- All T2 commits are local; there was **no deploy, no tag, and no git push**.
- Full post-deploy e2e has **not** run yet.
- The deployment prerequisite remains: check `etcdctl endpoint status` before script `140`.
- This is the requested **T2 checkpoint 2** state.

## Deployment attempt and rollback

- Result: **ROLLED BACK**.
- Timestamp: `2026-10-07 21:07–21:13 +0330`.
- The owner-approved deployment was started with `scripts/140-deploy-nova.sh`.
- The run failed during the NovaAgent web build before manifest apply:
  `TypeError [ERR_INVALID_ARG_TYPE]: The "path" argument must be of type string. Received undefined`
  in the web overlay Node script (`node:internal/errors:543`). No application smoke checks were run
  because the deployment itself failed.
- The documented rollback was executed immediately and completed successfully. Since script 140 had
  not reached manifest apply, the live image tags were already the rollback versions; the idempotent
  `set image` operations did not introduce a new version.
- Post-rollback live images:
  - nova-control: `172.16.50.89:5000/nova-control:0.4.4`
  - nova-downloads: `172.16.50.89:5000/nova-downloads:1.6.6-nova.1-1`
  - all `nova-ws` workspaces: `172.16.50.89:5000/nova-agent-server:1.38.0-nova11`
- Rollout checks passed for `nova-control`, `nova-downloads`, and every `nova-ws` deployment.
- Per the failure instruction, no further application tests or deployment retry was attempted after rollback. Only this rollback report was recorded.

## Final status

**T2 rolled back.**

## Deployment retry and final rollback

- Result: **ROLLED BACK**.
- Pre-deploy etcd retry check: **PASS** (`645.6 MB`, `644.4 MB`, `640.1 MB`; all below `1.5 GiB`).
- The `SCRIPT_DIR` build fix (`a729400`) allowed the web build and desktop/download build to complete.
- Deployment applied successfully:
  - nova-control: `0.5.0`
  - nova-downloads: `1.6.6-nova.2-2`
  - desktop client: `1.6.6+nova.2`
- Smoke and download e2e passed:
  - `/` → `302 https://nova.isigpu.local/download/`
  - `/v1/me` → `401`
  - invalid password `/api/auth/login` → `401`
  - `/download/` → `200`
  - `download.spec.js` → `1 passed`.
- Public-page visible-brand scan passed for `/`, `/download/`, and `/admin`.
- UI-bundle render scan failed: it exposed a visible old-brand string in the Slack-channel-monitor
  template: `Watch Slack channels for @openhands mentions`. This satisfies the requested immediate
  rollback condition.
- The documented rollback was executed immediately. All `nova-ws` deployments were checked again after
  the initial loop because `ws-e2e-dorjagent` was created during deployment; it was also rolled back.
- Final live images:
  - nova-control: `172.16.50.89:5000/nova-control:0.4.4`
  - nova-downloads: `172.16.50.89:5000/nova-downloads:1.6.6-nova.1-1`
  - all `nova-ws` workspaces: `172.16.50.89:5000/nova-agent-server:1.38.0-nova11`
- Rollout checks passed after rollback.
- No commit/tag for the failed release was created, no push was performed, and no further deployment
  retry was attempted.

## Final status

**T2 rolled back.**

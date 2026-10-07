# T2 Report — Dorj Agent rebrand

- Date: 2026-10-07
- Status: **CHECKPOINT 1 APPROVED — CTO fixes applied; proceeding to items 3–11 (no deploy yet)**
- Scope completed: item 1 (desktop icon) and item 2 (login prompt + loading)
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

## Remaining T2 items

Items 3–11 are intentionally not started until the CTO reviews checkpoint 1.

- Item 3: web UI bundle
- Item 4: admin UI
- Item 5: download page
- Item 6: agent identity + e2e
- Item 7: Keycloak realm display/theme
- Item 8: CLI name/alias
- Item 9: Grafana title
- Item 10: e2e brand assertions/no-old-brand scan
- Item 11: docs

No `NOVA_CLIENT_REV`, control, agent-server, or download revision bumps were made because this checkpoint did not build or deploy.

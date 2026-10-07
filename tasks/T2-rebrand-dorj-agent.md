# T2 — Full migration NovaAgent → "Dorj Agent", same look as platform.isigpu.local

Owner: mm_work_1 (GLM).  Reviewer: Claude (CTO).  Date: 2026-10-07.  Decision by owner: name = **Dorj Agent**.

## Goal
Everything a customer or admin SEES says "Dorj Agent" and looks like platform.isigpu.local
(dorjService UI + Keycloak theme dorj-v3). Same logo, colors, font.

## Design source of truth (copy from here, do not invent)
- Logo / icon: `k8s-deploy/keycloak-theme/dorj-v3/login/resources/img/` → `dorj-d.png` (the "D"),
  `dorj-wordmark.png`, `logo.png`, `favicon.png`; also `dorjService/public/img/{logo,favicon}.png`.
- Colors: navy `#0B1B3A`, brand blue `#1E3A8A`, gradient `linear-gradient(120deg,#1E3A8A 0%,#16295e 100%)`,
  gold accent `#E8A317`, light surface `#f5f7fb`.
- Font: Vazirmatn (already in `desktop/vazirmatn-*.woff2`). No external fonts/URLs (air-gapped).
- Layout reference: dorj-v3 login (`keycloak-theme/dorj-v3`: template.ftl, v3.css) = split page:
  dark brand half (3-D "D", tagline) + light form half 400px; <900px only the form card.
- Persian text formal, same tone as dorj-v3 (e.g. «ورود به دستیار دُرج»).

## Work items (internal names stay: namespace `nova`, images `nova-*`, host nova.isigpu.local, realm `nova`)
1. **Desktop app icon**: build .ico (16..256), .png (512) and Linux icons from `dorj-d.png`
   (navy rounded-square background, white D, same as platform favicon style). Wire through
   `desktop/overlay.py` (asserted anchors) — do NOT edit /workdir/NovaAgent in place.
2. **desktop/login-prompt.html + loading.html**: redesign to the dorj-v3 look (split layout,
   wordmark, navy gradient, gold accent), all "NovaAgent"/"Nova" text → "Dorj Agent" / «دستیار دُرج».
   Keep the same IPC contract and element ids the code/tests use.
3. **Web UI bundle served by nova-control** (`control/web`, built from NovaAgent src): rebuild from
   current NovaAgent HEAD (already Dorj themed), and via `web/patch-ui.sh` replace the in-app
   logo marks (`src/assets/branding/dorj-agent-logo*.svg`) with the platform D/wordmark. Title,
   favicon, manifest name = "Dorj Agent". No "NovaAgent"/"OpenHands" visible anywhere.
4. **Admin UI (/admin)** (`control/nova_control/adminpage.py` + nova-admin): name, logo, favicon, colors.
5. **Download page** (`download/render.py` / NovaAgent `scripts/download-site`): dorj look + logo,
   all text Dorj Agent; artifact names `Dorj-Agent-Client-*` (already).
6. **Agent identity**: `agent-server/SOUL.md` + `nova_server_patches` NOVA_SOUL → the agent says it
   is "Dorj Agent" / «دستیار دُرج» (en + fa), never NovaAgent/OpenHands. Update `e2e/identity.spec.js`.
7. **Keycloak realm `nova`**: displayName / displayNameHtml → "Dorj Agent" via scripts/141 (idempotent).
   Login theme for realm nova → existing `dorj-v3` ONLY if it works for this realm (check the
   email-suffix/docs-panel texts are not platform-specific); switching a realm's theme needs NO
   Keycloak restart. Do NOT redeploy theme files / restart Keycloak without asking CTO first.
8. **CLI** (`cli/`): user-facing name "Dorj Agent"; add command alias `dorj-agent`, keep `nova` working.
9. **Grafana dashboard** title → "Dorj Agent" (uid `nova-agent` unchanged).
10. **e2e**: update every name/brand assertion; add a check that no page served on nova.isigpu.local
    (/, /download/, /admin, UI bundle) contains "NovaAgent" or "OpenHands" in visible text/title.
11. Docs: README.md, HANDOFF.md, RUNBOOK section — product name.

## Build / deploy
- Bump: `NOVA_CLIENT_REV` → client `1.6.6+nova.2`; `NOVA_CONTROL_TAG` → `0.5.0` (includes the
  pending 0.4.5 token-revocation work, commit 2da16ed); `NOVA_AGENT_SERVER_TAG` → `1.38.0-nova12`;
  downloads rev bump. Use `k8s-deploy/scripts/140-deploy-nova.sh`.
- Before deploy: `etcdctl endpoint status` (DB size) — stop if > 1.5 GiB.
- Credentials: `E2E_USER`/`E2E_PASS` come from the owner's env file (path will be given); never print/log/commit.
- After deploy run the FULL e2e table (HANDOFF §5) except load30.sh. Screenshots of: download page,
  login prompt, splash, main UI, admin, Keycloak login (if realm theme changed) → `tasks/T2-evidence/`.

## Rules
- Forbidden without CTO OK: Keycloak restart/theme redeploy, GLM/vllm restart, deleting PVCs/data,
  git push, anything in namespaces other than `nova`/`nova-ws` (except realm `nova` via script 141).
- Commit per verified item in nova-platform / k8s-deploy, tag at the end (`nova-0.7.0`, next `v1.*`).
- Work in steps; after items 1–2 (desktop look) STOP and post screenshots to `tasks/T2-evidence/`
  and reply "T2 checkpoint 1" so CTO can review the design before building everything.

## Report → tasks/T2-report.md
Per item: done / not done, files, commit, evidence. Final: versions live, e2e table PASS/FAIL.

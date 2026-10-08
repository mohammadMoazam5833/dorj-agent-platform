# Dorj Agent roadmap T7–T11 (owner request 2026-10-08) — run in this order, one at a time

Owner: mm_work_1 (GLM). Reviewer: Claude (CTO). Product: Dorj Agent (desktop .deb/.exe/AppImage +
nova.isigpu.local). Repos: /workdir/NovaAgent (product source, OUR fork: commit on branch `dorj`,
small focused commits), /workdir/nova-platform (overlays, control, download, e2e, tasks),
/workdir/k8s-deploy (scripts/140, config.env NOVA_*). Design source of truth = the Dorj portal
`k8s-deploy/dorj-site/src/app/theme-v2.css` + `globals.css` (navy #081430/#0f2152, lapis #2747b8,
sky #5b8cff, gold #E8A317/#ffd36b, Vazirmatn, radius 10px buttons, no letter-spacing ever).
Process for EVERY task (lessons from T2): build with the real deploy path (140) before deploying,
run the full e2e table (creds `~/.config/nova-e2e.env`, never print them), screenshots to
`tasks/T<n>-evidence/`, rollback only for real breakage, commit + tag, reply `T<n> done`.
Stop at each **CHECKPOINT** and reply `T<n> checkpoint` with screenshots so CTO can review.

## T9 — CLI: CTO already tested it (2026-10-08 00:35) — findings, now FIX
Install works from the download page wheel with
`pip install --index-url http://172.16.50.89:8083/repository/pypi-proxy/simple --trusted-host 172.16.50.89 <wheel>`
(the customer needs that or a bundled-deps wheel). `dorj-agent` + `nova` entry points exist.
PASS: --version, login (NOVA_PASSWORD/getpass, NOVA_CA_BUNDLE, NOVA_RESOLVE=host=ip), whoami,
models, usage, conversations, ws, --json, logout, `run` for text-only answers.
**FAIL (real bug): every tool call in `run` fails with `customer '<user>' not connected`** — tools
are bridged to the customer PC through the desktop app's tunnel; the CLI never opens one, so
file/terminal work only happens if the desktop app is open on the same account. The agent then
"finishes" with nothing done. cli.spec.js missed it (it only checks a text reply).
Fix:
1. CLI must work on the **current directory** like Claude Code/Cursor CLI: `dorj-agent run` (and
   `chat`) start a local tools sidecar + reverse tunnel for the session (reuse the desktop's
   `scripts/local-tools-sidecar` + `customer-workspace` client protocol — either ship a small Node
   runtime path when the desktop app is installed, or implement the same ws protocol in Python;
   choose, justify in the report). Roots = cwd (not the whole home folder).
   If the desktop app already holds the tunnel, say so and use it (no 4001 ping-pong!).
2. If no workspace connection can be made, fail fast with a clear bilingual message instead of
   letting the agent retry and "finish".
3. User-facing CLI strings are Finglish today ("login nakardid", "vared shodid be onvan-e",
   "khorooj anjam shod", "gozarvazheh"): make them proper Persian script or English by locale
   (LANG starts with fa → Persian), and the help text too.
4. Rename the wheel to `dorj_agent_cli` (keep `nova` alias), version 0.4.0, download page shows
   the exact install command.
5. cli.spec.js: add a test that `run` creates a file in a temp cwd and the file exists locally.

## T9-old — CLI health check (original brief, done by CTO above)
- Fresh venv; install the CLI wheel exactly as a customer would from https://nova.isigpu.local/download/
  (note: it is still `nova_cli-0.3.0`; `httpx` was not found on the Nexus pypi proxy
  http://172.16.50.89:8081/... — find the right Nexus pypi URL the repo uses, or bundle deps).
- Verify: `dorj-agent` AND `nova` entry points exist; login (with org CA), whoami, models, usage,
  run a small task, logout; `--help` text says Dorj Agent. Fix anything broken: rename the wheel to
  `dorj_agent_cli` (keep `nova` alias), make install work offline for customers (document the
  exact pip command on the download page). Re-run cli.spec.js. Report table PASS/FAIL per command.

## T7 — STATUS from CTO (2026-10-08 01:50)
- DONE by CTO: NovaAgent commit `ffe78d2` adds the `dorj-dark` palette (portal navy surfaces,
  gold primary #E8A317, sky accent #5B8CFF) and makes **dark + dorj-dark the default**. Verified
  with the real deploy build (node:24-slim + patch-ui.sh + finalize-ui.sh): body rgb(15,30,64).
  Not deployed yet; ship it with the next 140 rebuild (bump NOVA_CONTROL_TAG).
- FINDING: the app has **0 / 2167 Persian strings** (`src/i18n/translation.json` has 15 languages,
  no `fa`). This is the main remaining T7 work:
  1. Add `fa` to `AvailableLanguages` with RTL (`dir="rtl"` on the app root when fa; check the
     sidebar/chat/composer/diff/terminal layouts mirror correctly; code/diff/terminal stay LTR).
  2. Translate all 2167 strings with a script that calls OUR LiteLLM (qwen3 for bulk, glm for
     review) in batches of ~50, with a fixed glossary (Dorj Agent = «دستیار دُرج», workspace =
     «فضای کار», conversation = «گفتگو», agent = «دستیار», commit/branch/terminal kept in English,
     keep {{placeholders}} and <tags> intact; house style: short lines, no «—»). Validate:
     placeholders identical, `npm run check-translation-completeness` passes. Save the script.
  3. Default language **fa** (fallback en); nova-control boot injection may also set it.
  4. CTO reviews 40 random strings at the CHECKPOINT before deploy.
- Download page: rewrite copy in house style (no «—» or comma chains, see
  `~/.claude`-independent rule in k8s-deploy/dorj-site/tasks/T3-content.md) and add a portal-style
  theme layer in `download/render.py` (aurora hero, glass cards, 10px buttons, portal header/footer
  with a link back to https://dorj.isigpu.local).

## T7 — Visual theme of the app + download page (original brief)
Goal: the desktop app and download page look like ONE product family with the portal.
- App (UI bundle via `nova-platform/web/patch-ui.sh` + NovaAgent `src/themes/*`, `index.css`,
  `tailwind.css`): **dark theme by default** (Cursor-like, navy surfaces, not grey/black), gold accent
  for primary actions/focus, sky for links/selection, Vazirmatn for all Persian, buttons 10px.
  Keep a light theme option that also uses the brand palette. Fix contrast (WCAG AA 4.5).
  **Persian (fa, RTL) as default UI language** — today the home says "Let's Start Building!"; translate
  the visible home/chat/sidebar/settings strings that are still English-only.
- Desktop login/splash already done in T2 — just align tokens.
- Download page (`download/render.py` + NovaAgent `scripts/download-site`): portal look (aurora hero,
  glass cards, gold CTA, Vazirmatn, same header/footer style, link back to dorj.isigpu.local).
- CHECKPOINT after screenshots (app home, conversation with diff/terminal open, settings, download
  page; fa + en; desktop + 390px mobile for download).

## T10 — Persistent memory
Facts: the SDK has `agent_context.load_memory` (default true) reading `.openhands/memory/MEMORY.md`
in the project (NovaAgent `src/api/project-rules-files.ts`). Our workspaces are bridged to the
customer PC (sidecar_bridge + nova_server_patches), so it may silently not work.
0. **CTO finding (2026-10-08, from the nova12 image):** `openhands/sdk/context/memory.py`
   `load_memory(working_dir)` reads `~/.openhands/memory/MEMORY.md` (user tier) and
   `<working_dir>/.openhands/memory/MEMORY.md` (project tier) with plain `Path.read_text` ON THE
   AGENT-SERVER POD, called from `conversation/impl/local_conversation.py` ~line 1066. But the agent
   WRITES memory with its file tools, which our bridge sends to the CUSTOMER PC. So both tiers are
   written on the PC and read (never found) on the pod → memory silently never loads.
   Fix in `nova_server_patches` (no SDK fork): read the project tier through the sidecar bridge for
   customer paths; for the user tier pick ONE home and make reads+writes agree — recommended:
   server-side per user (survives PC changes, like Cursor Memories) by routing the agent's
   `~/.openhands/memory/` file ops to the workspace PVC instead of the PC; document the choice.
1. Prove it end-to-end on the live system (e2e user): conversation 1 tells the agent a preference
   ("always answer in Persian, use pnpm"); conversation 2 (new) must follow it. Find where the file
   lands (customer PC project folder). Fix the bridge/patches if it doesn't load or write.
2. Add **user-level memory** (Cursor "Memories"): per-tenant memory stored server-side (nova-control,
   tenant registry or a per-user ConfigMap/PVC file — NOT one big shared ConfigMap; small, batched
   writes, see CLAUDE.md etcd rule), loaded into every conversation regardless of project.
3. UI: Settings → «حافظه» page listing project + user memories with view/edit/delete, bilingual,
   table rules (filter/sort/pagination). The agent must say when it saves a memory.
4. e2e: new `memory.spec.js` covering both levels. CHECKPOINT before deploy.

## T8 — Cursor parity (research → plan → implement in phases)
App ALREADY has (verify each works in our deployment, don't rebuild): agent mode, planner tab (plan
mode), task list (todos), changes tab (diffs), commits tab, terminal, browser tab, files tab, rules,
skills, MCP, sub-agents, automations (background agents), command menu, image input, model picker.
1. Research: write `tasks/T8-cursor-gap.md` — table Cursor feature | Dorj Agent today (works/
   broken/missing) | effort | value. Include at least: checkpoints/restore, @-mentions (files,
   folders, rules, docs) with autocomplete from the customer workspace, per-hunk accept/reject review,
   codebase indexing + semantic search (use our platform's Jina embeddings v3 + reranker v3.5 via
   LiteLLM — a selling point), command auto-run allowlist / ask-before-run on the customer PC,
   queued messages while the agent works, plan→execute flow, Tab/inline edit (likely out of scope:
   we are not an editor — say so), keyboard shortcuts (Ctrl+K/L/I), notifications when done.
2. CHECKPOINT with the table + a proposed Phase 1 (max 4 items, highest value/effort first).
3. Implement Phase 1 after CTO approval; UX should feel like Cursor (composer at bottom, context
   chips, diff review with accept/reject, clear agent status), in the Dorj theme from T7.

## T11 — Cross-product design consistency audit (LAST)
Pages: dorj.isigpu.local (portal), platform.isigpu.local (dorjService), Keycloak login (dorj-v3 +
nova realm), nova.isigpu.local/download, /admin (Dorj Agent admin), the desktop app.
- Screenshot matrix (fa/en, desktop/mobile) → `tasks/T11-evidence/`.
- Audit table: logo/wordmark, colours/tokens, font, button radius, header/footer, language switch,
  favicon/title format («X | دُرج»), copy style (no "." "," dashes inside Persian sentences).
- Fix the cheap mismatches directly (separate commits per product); list bigger ones for CTO.

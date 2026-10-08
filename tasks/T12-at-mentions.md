# T12 — @-mentions in the composer (Cursor phase 1, item 4) — owner approved 2026-10-08

Owner: mm_work_1 (GLM). Reviewer: Claude (CTO). Repo: /workdir/NovaAgent (branch as is, small commits).
CTO is in parallel finishing items 1-3 (agent-server + Changes tab: files `src/api/git-service/
workspace-git.api.ts`, `src/components/features/diff-viewer/*`, `src/routes/changes-tab.tsx`) —
do NOT touch those. **Do NOT deploy** (no scripts/140) — CTO deploys everything together.

## Feature
In the chat composer, typing `@` opens a popover listing files and folders of the conversation's
workspace (the customer's PC) filtered as the user types (fuzzy, case-insensitive, max 50 rows,
↑/↓/Enter/Tab/Esc keyboard support, mouse click). Choosing an item inserts `@relative/path` as a
chip-like token (plain text is acceptable if the editor is a textarea) and the message sent to the
agent contains the path, so the agent reads that file first. Also allow `@rules` → list of the
project rules files if the app already exposes them (skip if not trivial).

## Where things are
- Composer: find it under `src/components/features/chat/` (the input with placeholder
  «چه چیزی می‌خواهید بسازید؟» / CHAT_INTERFACE input). Check whether a mention/autocomplete
  mechanism already exists for skills or slash commands (`/`), and REUSE its popover pattern.
- File list: the Files tab (`src/routes/files-tab.tsx`, `components/features/files*`) already lists
  workspace files through the agent-server file router, which nova bridges to the PC
  (sidecar_bridge `try_install_file_router_proxy`). Reuse that service/hook; cache the list per
  conversation; lazy-load folders if the API is per-directory (depth ≤ 3, skip node_modules, .git,
  dist, build, .venv).

## Rules
- All UI text through i18n keys in `src/i18n/translation.json` with `fa` (real Persian) + `en`, copy
  `en` into the other 14 languages; then `npm run make-i18n`. The repo lint forbids literal strings.
- RTL: the popover aligns to the caret/composer start in fa; paths render LTR (`dir="ltr"`).
- Theme tokens only (`var(--oh-*)`), radius 10px, no letter-spacing.
- Unit test(s) for the filter/insert logic (vitest, existing style).
- Must pass: `npx tsc --noEmit -p .`, `npx eslint <changed files>`, `npm run check-translation-completeness`,
  and a real build with the deploy toolchain:
  `docker run ... node:24-slim ... npm ci && /overlay/patch-ui.sh /src && npm run build` (see
  k8s-deploy/scripts/140-deploy-nova.sh lines ~40-50 for the exact command).
- Commit with a clear message; reply in tmux `T12 ready` (CTO reviews and deploys).

# T19 — Windows: file upload into a workspace fails + "git not found" (owner bug report 2026-10-09)

Owner: on Windows, in the Dorj Agent app, inside a workspace, uploading files shows
`http request failed (400): {"detail":"Path must be absolute"}` and git was not found.

## Root cause (CTO)
- agent-server `/api/file/upload` and `/api/file/download` run on the SERVER (`Path(path).is_absolute()`
  on Linux → `C:\...` is "not absolute" → 400; on Linux PCs it silently writes to the server pod).
  Only `/file/home` and `/file/search_subdirs` are bridged (sidecar_bridge/tools_proxy.py).
- The sidecar protocol's `write_file`/`read_file` are UTF-8 text only (handlers.mjs `Buffer.from(content,"utf8")`),
  so binary uploads (images, PDF, zip) cannot go through it yet.

## Split
- CTO: server side — bridge upload/download to the PC (nova_server_patches), text via the current
  protocol, binary only when the client announces `file_base64`; clear bilingual error otherwise
  («برای بارگذاری این نوع فایل نسخه‌ی جدید دستیار دُرج را نصب کنید»).
- GLM (this task), protocol addition, backwards compatible:
  1. NovaAgent `scripts/local-tools-sidecar/handlers.mjs`: `write_file` accepts optional
     `encoding: "base64"` (decode to bytes); `read_file` accepts optional `encoding: "base64"` and
     returns `{path, encoding:"base64", content}` (raise the max to 25 MB for base64 reads/writes,
     keep 2 MB default for utf-8). Unknown encoding → `invalid_request`.
  2. `hello` result gains `capabilities: ["file_base64"]` (find where hello is built; keep protocol 1).
  3. Same in the CLI's Python bridge (`nova-platform/cli/dorj_agent_cli/bridge.py` / workspace.py) so
     `dorj-agent bridge` supports it too; bump CLI to 0.5.1.
  4. Changes tab UX (NovaAgent `src/routes/changes-tab.tsx` + i18n fa/en):
     - git missing on the PC (bridged git fails with spawn ENOENT / exec_failed; CTO will make the server
       return HTTP 400 detail starting `git_not_installed:`) → message «Git روی این رایانه نصب نیست»
       with a link https://git-scm.com/download/win (Windows) and a note that keep/undo/checkpoints need it.
     - folder not a repo (existing DIFF_VIEWER$NOT_A_GIT_REPO) → add a button «راه‌اندازی git در این پوشه»
       that calls `POST /api/git/init {path}` (CTO adds the route) then refetches.
  5. Unit tests for the new handler params; `npx tsc`, eslint, vitest green; node --check where relevant.
  6. Desktop client rebuild will be done by CTO (NOVA_CLIENT_REV). Do NOT deploy. Reply `T19 ready`.

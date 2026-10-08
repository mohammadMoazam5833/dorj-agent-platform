# T8 — Cursor parity: first-pass gap table (CTO, 2026-10-08, from NovaAgent source)

GLM: VERIFY every row on the running app (screenshots), correct the "today" column, then propose
Phase 1 (max 4 items) at the CHECKPOINT. Source probes were grep counts over NovaAgent/src
(excluding tests/mocks), so "present" means code exists, not that it works in our deployment.

| Cursor feature | Dorj Agent today (code) | Effort | Value | Note |
|---|---|---|---|---|
| Agent mode (multi-file edit, run, test) | present (core) | — | — | CLI broken for tools (T9) |
| Plan mode | present (`routes/planner-tab.tsx`) | verify | high | |
| Todo list | present (`task-list-tab.tsx`) | verify | med | |
| Diff view of changes | present (`changes-tab.tsx`, `diff-viewer`) | — | — | |
| **Per-hunk / per-file accept-reject** | **missing** (0 hits) | M | **high** | core Cursor review UX |
| **Checkpoints / restore to before a step** | **almost missing** (1 hit) | M | **high** | git stash/commit snapshots on the PC via sidecar |
| **@-mentions (files, folders, rules) in composer** | **missing** (0 hits) | M | **high** | autocomplete from customer workspace file list |
| Ask-before-run / auto-run allowlist | present (`confirmation_mode`, security analyzer: 14 files) | S (defaults + UI) | **high** | HANDOFF: no per-command confirmation on customer PC today — turn on, allowlist like Cursor |
| **Codebase indexing + semantic search** | **missing** (0 hits) | L | **high (sales)** | Jina embeddings v3 + reranker v3.5 already live via LiteLLM |
| Queued messages while agent runs | partial (1 hit) | S | med | |
| Rules (project/user) | present (`rules-settings.tsx`) | — | — | |
| Memories | present but BROKEN in our deployment | M | high | T10 |
| MCP | present | — | — | |
| Sub-agents | present | — | — | |
| Background agents / automations | present (`automations-*`) | verify | med | |
| Browser tool | present (`browser-tab.tsx`) | verify | med | |
| Model picker | present (composer) | — | — | Qwen / GLM |
| Keyboard shortcuts (Ctrl+K/L/I, command palette) | partial (3 files, command menu) | S | med | |
| Done notifications | present (11 files) | verify | low | desktop notification when a long task ends |
| Image input | present | — | — | |
| Tab completion / inline Ctrl+K edit in an editor | **out of scope** | — | — | we are not an editor; maybe a VS Code extension later |

Proposed Phase 1 (CTO view, GLM may challenge with evidence):
1. Ask-before-run + allowlist on the customer PC (safety, small, sells to IT).
2. Per-hunk/per-file accept-reject review of agent changes.
3. Checkpoints (restore the workspace to before any agent step).
4. @-mentions for files/folders/rules in the composer.
Phase 2: codebase indexing + semantic search on our Jina models.

## Phase 1 status (2026-10-08 04:40)
- #1 destructive-command gate: LIVE (agent-server nova14). Verified: `rm -rf build` paused, folder intact.
  Follow-up: the CLI waits until timeout on WAITING_FOR_CONFIRMATION - print a clear bilingual
  "needs your confirmation" message with an approve/reject prompt (or --yes flag) and exit cleanly.
- #2 keep/undo per file + #3 checkpoints: backend LIVE (auto checkpoint verified live before an edit);
  UI committed in NovaAgent 3bc0e11, ships with the next nova-control build together with T12.
- #4 @-mentions: GLM, tasks/T12-at-mentions.md.

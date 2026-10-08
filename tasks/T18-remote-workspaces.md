# T18 — Workspaces beyond the customer's PC (owner question 2026-10-08, CTO research)

Owner: «در Cursor workspace را علاوه بر OS کاربر می‌شود جاهای دیگر برد؛ بررسی کن».

## What Cursor offers → what Dorj Agent can do
| Cursor | How it works there | Dorj Agent today | Plan |
|---|---|---|---|
| Local folder | editor on the PC | ✅ desktop app sidecar + reverse tunnel | — |
| Remote-SSH | server component on the remote host over SSH | ⚠️ partly: the CLI (dorj_agent_cli ≥0.4.0) contains a pure-Python `WorkspaceBridge` that speaks the sidecar protocol, so `dorj-agent run` already works ON any Linux/Windows/macOS host with Python (CTO verified on the deploy host) | **v1 (this task): `dorj-agent bridge`** |
| Dev Containers / WSL | tools run inside a container / WSL | ❌ | v1 covers it: run `dorj-agent bridge` inside the container or WSL |
| Dorj Platform notebook / VM | (no Cursor equivalent — our differentiator) | ❌ | v1 covers it: `pip install` the CLI in a notebook terminal or VM, run `dorj-agent bridge`; GPU + data right there |
| Background / cloud agent | runs in a cloud VM, user leaves | ❌ (by design no server-side workspace) | v2: optional server-side workspace on the user's agent-server PVC with `git clone`, opt-in by admin |

## T18 v1 — `dorj-agent bridge` (GLM, after T17)
Goal: a long-running command that only opens the reverse workspace bridge (no task) for a chosen
folder, so the user keeps working in the **desktop app UI** while the agent's file/terminal tools
run on that remote machine.
- `dorj-agent bridge [--root PATH]` (default: cwd). Roots = that folder only. Prints a clear
  bilingual status line: «فضای کار روی <hostname>:<path> وصل است. این پنجره را باز نگه دارید»;
  reconnects with backoff; Ctrl+C closes cleanly. Exit codes documented.
- One tunnel per account exists today: starting a bridge replaces the desktop app's tunnel (4001
  `session_replaced`; the app shows its «account open elsewhere / use it here» dialog). That is the
  intended v1 switch — document it; make sure the CLI side stops on its own 4001 too (no ping-pong).
- The agent's `<USER_MACHINE>` context must describe the REMOTE host (hello payload: OS, home, shell,
  runtimes — reuse what bridge.py sends; add GPU info via `nvidia-smi -L` if present).
- `--detach` writes a pid file and logs to `~/.config/dorj-agent/bridge.log` (Linux/macOS), plus a
  documented systemd --user unit example.
- Tests: unit (bridge lifecycle with a fake gateway) + live e2e (CTO runs): start `bridge` in a temp
  dir, create a conversation through the API with working_dir = that path, ask the agent to create a
  file → file appears in the temp dir; desktop-style 4001 handover works both ways.
- Download page (NovaAgent scripts/download-site, CLI section): new step «کار روی سرور، WSL یا
  نوت‌بوک پلتفرم دُرج» with the 3 commands (ssh host; pip install …; dorj-agent bridge --root ~/project).
- Do NOT deploy; reply `T18 ready`.

## v2 (later, needs owner OK)
- Several named workspaces per account (gateway keyed by tenant+workspace) and a workspace picker in
  the app («این رایانه» / «سرور X» / «نوت‌بوک Y»), so the PC and a remote host can be used side by side.
- «Open in Dorj Agent» button on platform.isigpu.local notebooks/VMs that starts the bridge there.
- Opt-in server-side background workspace.

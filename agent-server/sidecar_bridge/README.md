# Sidecar bridge (Phase 2)

Python adapter that talks to the **Local Tools Sidecar** (`127.0.0.1:18765`)
using protocol v1 and implements OpenHands `LocalWorkspace` operations via HTTP.

## What this is

| Piece | Role |
|-------|------|
| `SidecarWorkspace` | `LocalWorkspace` subclass; `list_dir` / `read_text` / `write_text` / `execute_command` / `file_upload` / `file_download` go through the sidecar |
| `try_install_bridge()` | On import, monkeypatches `LocalWorkspace` methods when `NOVAAGENT_LOCAL_TOOLS_URL` + `TOKEN` are set (Canvas still sends `kind: LocalWorkspace`) |
| Smoke | `npm run sidecar:bridge-smoke` |

## Honest limits (SDK)

Stock agent-server **file_editor** and **terminal** are monkeypatched when the
bridge is enabled so they use sidecar `read_file` / `write_file` / `exec`
instead of company-local `Path` / tmux.

Folder Browser `GET /api/file/home` and `search_subdirs` are best-effort
proxied. Other `/api/file/*` routes still use the agent-server host FS.

## Enable with agent-server (Hybrid laptop demo)

```bash
export NOVAAGENT_LOCAL_TOOLS_TOKEN=dev-token
export NOVAAGENT_LOCAL_TOOLS_ROOTS=/path/to/project
export NOVAAGENT_LOCAL_TOOLS_SIDECAR=1   # also sets URL default :18765
# optional explicit URL:
# export NOVAAGENT_LOCAL_TOOLS_URL=http://127.0.0.1:18765

npm run sidecar:local-tools &

# Dev stack passes --import-modules sidecar_bridge when URL+token are set
NOVAAGENT_LOCAL_TOOLS_SIDECAR=1 NOVAAGENT_LOCAL_TOOLS_TOKEN=dev-token npm run dev
```

`scripts/dev-safe.mjs` already puts `tools/` on `OH_EXTRA_PYTHON_PATH`.

## Manual registration

```bash
agent-server \
  --extra-python-path /path/to/OpenHands/tools \
  --import-modules sidecar_bridge \
  --host 127.0.0.1 --port 18000
```

## Programmatic use

```python
from sidecar_bridge import SidecarWorkspace

with SidecarWorkspace(
    working_dir="/path/to/project",
    sidecar_url="http://127.0.0.1:18765",
    sidecar_token="dev-token",
    sidecar_roots=["/path/to/project"],
) as ws:
    print(ws.list_dir())
    print(ws.read_text("README.md"))
    print(ws.execute_command("ls -la"))
```

## Tests

```bash
npm run test:sidecar-bridge
npm run sidecar:bridge-smoke
```

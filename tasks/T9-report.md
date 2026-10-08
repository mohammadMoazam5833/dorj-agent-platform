# T9 — Dorj Agent CLI reverse workspace bridge

## Summary

The CLI is now `dorj-agent-cli` 0.4.0 with both `dorj-agent` and `nova` entry points. `run` and
`chat` operate on the CLI's current directory and establish a Local Tools Sidecar protocol-v1
bridge over an outbound WebSocket. The bridge implements `hello`, `list_dir`, `read_file`,
`write_file`, and `exec`; it opens no inbound port and allowlists only the resolved cwd. If a
desktop tunnel is already connected and its roots contain the CLI cwd, the CLI reuses that tunnel
without opening or evicting a second session. If no usable workspace connection exists, the command
fails fast with exit code 4 and a localized message instead of letting the agent claim success.

## Implementation choices

- **Python-side bridge:** implemented the customer-side protocol in Python instead of bundling or
  requiring Node. This keeps the wheel small and works on the same Linux/macOS/Windows Python
  environments the CLI already targets, while preserving the desktop protocol and its root checks.
  Windows bash invocations are translated to Git Bash when available, then PowerShell.
- **Desktop tunnel reuse:** added `/v1/workspace/tunnel` in nova-control. It reports only the current
  tenant's connected state, protocol, and roots (workspace metadata, not secrets). The gateway now
  requests `hello` after each desktop connection and exposes per-customer roots through `/health`.
- **Event-loop/lifetime fix:** the bridge context is kept open while `_send()` and `_follow()` run.
  `chat` uses an `AsyncExitStack`, so the reverse bridge remains connected for every interactive turn.
- **PWD fix:** child processes receive `PWD` matching their resolved cwd, preventing tools that infer
  project root from a stale inherited `PWD` from operating in the wrong directory.
- **Localization:** CLI messages and help text use Persian for `fa_*` locales and English otherwise.

## PASS/FAIL verification

| Check | Result | Evidence |
|---|---:|---|
| CLI unit suite (workspace protocol, root isolation, PWD, timeout, resolved WS host) | PASS (7 passed) | `tasks/T9-evidence/unit-tests.log` |
| Live CLI e2e: auth, models, usage, real reverse-bridge file creation, logout | PASS (1 passed in 8.3s) | `tasks/T9-evidence/e2e-cli.log` |
| Fresh customer-path wheel install and dependencies from Nexus | PASS | `tasks/T9-evidence/fresh-install.log` |
| `dorj-agent --version`, `nova --version`, `--help`, login, whoami, logout | PASS | `tasks/T9-evidence/cli-smoke.log` |
| `/v1/workspace/tunnel` returns tenant state without gateway secrets | PASS (`{"connected": false, "roots": [], "protocol": 1}`) | `tasks/T9-evidence/tunnel-endpoint.json` |
| Gateway health exposes desktop roots without evicting the session | PASS (observed root `C:\Users\USER`) | live gateway `0.1.5` |
| Download page shows exact install command and new wheel | PASS (`dorj_agent_cli-0.4.0-py3-none-any.whl`) | live download page |
| 140 deploy smoke (`/`, `/v1/me`, bad login, `/download/`) | PASS | `tasks/T9-evidence/deploy-140-final.log` |

## Deployed images

- `nova-control:0.5.2`
- `nova-gateway:0.1.5`
- `nova-downloads:1.6.6-nova.2-4`

## Customer install command

```bash
pip install --index-url http://172.16.50.89:8083/repository/pypi-proxy/simple --trusted-host 172.16.50.89 https://nova.isigpu.local/download/dorj_agent_cli-0.4.0-py3-none-any.whl
```

For TLS-verified internal access, also set `NOVA_CA_BUNDLE` to the organization root CA and, if DNS
is not available, `NOVA_RESOLVE=nova.isigpu.local=<server IP>`.

## Known limitations

- CLI bridge roots are intentionally limited to one resolved cwd per invocation.
- File reads and command output are capped at 2 MiB, matching the sidecar safety boundary.
- The CLI assumes bash/sh on POSIX; on Windows it prefers Git Bash and otherwise uses PowerShell.

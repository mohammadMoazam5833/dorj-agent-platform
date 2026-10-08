# Dorj Agent workspace bridge

`dorj-agent bridge` turns a folder on the current machine into a Dorj Agent workspace without
starting a task. Run it on the machine that owns the files (server, WSL, container, notebook/VM),
then keep working in the desktop app.

```bash
dorj-agent login -u USERNAME
cd ~/project
dorj-agent bridge
# or, explicitly:
dorj-agent bridge --root ~/project
```

While it is connected, file and terminal tools run under the selected root only. The status line
includes the remote hostname and absolute path. Keep the window open; Ctrl+C closes the bridge
cleanly.

- The CLI reconnects automatically after transient failures (1s → 2s → 4s … capped at 30s).
- One workspace tunnel is allowed per account. A new bridge intentionally replaces the desktop
  app's tunnel; the gateway closes the old side with 4001 (`session_replaced`). The CLI also stops
  when it receives 4001, so the two sides do not reconnect against each other.
- `--detach` writes `~/.config/dorj-agent/bridge.pid` and appends logs to
  `~/.config/dorj-agent/bridge.log`.
- Exit codes: `0` clean stop or Ctrl+C (including deliberate 4001 handover), `1` error,
  `2` bad arguments, `3` sign-in, `4` workspace connection.

## systemd --user example

```ini
# ~/.config/systemd/user/dorj-agent-bridge.service
[Unit]
Description=Dorj Agent workspace bridge

[Service]
Type=simple
ExecStart=%h/.local/bin/dorj-agent bridge --root %h/project
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
```

Adjust the executable and project path. Reload with
`systemctl --user daemon-reload`, then `systemctl --user enable --now dorj-agent-bridge.service`.

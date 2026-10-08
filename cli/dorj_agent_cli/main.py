"""Dorj Agent CLI.

  dorj-agent login | logout | whoami
  dorj-agent run "task" [--model M] [--json]     one-shot task in the current directory
  dorj-agent chat [--resume ID]                  interactive task in the current directory
  dorj-agent conversations [--limit N]
  dorj-agent ws status | start | stop
  dorj-agent models | usage
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from . import __version__, auth
from .bridge import WorkspaceBridge, WorkspaceConnectionError
from .i18n import is_fa, tr

EXIT_OK, EXIT_ERR, EXIT_QUOTA, EXIT_AUTH, EXIT_WORKSPACE = 0, 1, 2, 3, 4
EXIT_CONFIRMATION_REJECTED = 5


class Api:
    def __init__(self):
        self.server, token = auth.bearer()
        self.c, self.ext = auth.client_for(self.server, token)

    def req(self, method, path, **kw):
        r = self.c.request(method, path, extensions=self.ext, **kw)
        if r.status_code in (401, 403):
            raise auth.AuthError(tr("access denied — run `dorj-agent login`", "دسترسی رد شد — `dorj-agent login`"))
        if r.status_code == 429 or "budget" in r.text.lower() and r.status_code >= 400:
            raise QuotaError(r.text[:300])
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:300]}")
        return r.json() if r.content else {}


class QuotaError(RuntimeError):
    pass


class ConfirmationRejected(RuntimeError):
    """A non-interactive CLI session rejected a pending tool call."""


def _print(obj, as_json):
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        for k, v in obj.items():
            print(f"{k:>14}: {v}")


# ── agent conversations (through nova-control's proxy to your own agent-server) ──


def _start_conversation(api: Api, model: str | None, working_dir: Path) -> str:
    st = api.req("GET", "/api/settings", headers={"X-Expose-Secrets": "encrypted"})
    agent_settings = st["agent_settings"]
    if model:
        agent_settings["llm"]["model"] = f"litellm_proxy/{model}"
    body = {
        "workspace": {"working_dir": str(working_dir), "kind": "LocalWorkspace"},
        "agent_settings": agent_settings,
        "secrets_encrypted": True,
        "tags": {"source": "dorj-agent-cli"},
    }
    return api.req("POST", "/api/conversations", json=body)["id"]


def _send(api: Api, conv: str, text: str) -> None:
    api.req(
        "POST",
        f"/api/conversations/{conv}/events",
        json={"role": "user", "content": [{"type": "text", "text": text}], "run": True},
    )


def _confirmation_preview(ev: dict) -> str:
    action = ev.get("action") or {}
    value = (
        action.get("command")
        or action.get("path")
        or ev.get("summary")
        or action.get("kind")
        or "action"
    )
    value = str(value).strip()
    return value[:280] + ("…" if len(value) > 280 else "")


def _pending_confirmation_actions(items: list[dict]) -> list[dict]:
    """Return agent tool calls without an observation; newest action is first."""
    observed = {
        ev.get("tool_call_id")
        for ev in items
        if ev.get("kind") == "ObservationEvent" and ev.get("tool_call_id")
    }
    actions = [
        ev
        for ev in items
        if ev.get("source") == "agent"
        and ev.get("kind") == "ActionEvent"
        and ev.get("tool_call_id")
        and ev["tool_call_id"] not in observed
    ]
    return list(reversed(actions))


def _respond_to_confirmation(api: Api, conv: str, accept: bool) -> dict:
    body: dict[str, object] = {"accept": accept}
    if not accept:
        body["reason"] = "rejected_by_user"
    return api.req(
        "POST",
        f"/api/conversations/{conv}/events/respond_to_confirmation",
        json=body,
    )


def _handle_confirmation(
    api: Api, conv: str, items: list[dict], as_json: bool, auto_yes: bool
) -> None:
    pending = _pending_confirmation_actions(items)
    if not pending:
        return

    summaries = [
        {
            "tool": ev.get("tool_name") or (ev.get("action") or {}).get("kind") or "action",
            "command": _confirmation_preview(ev),
        }
        for ev in pending
    ]
    if as_json:
        print(
            json.dumps({"pending_confirmations": summaries}, ensure_ascii=False),
            flush=True,
        )
    else:
        print(
            tr("Pending confirmation:", "در انتظار تأیید:"),
            file=sys.stderr,
        )
        for summary in summaries:
            print(f"  ▸ {summary['tool']}: {summary['command']}", file=sys.stderr)

    if auto_yes:
        _respond_to_confirmation(api, conv, True)
        print(tr("Approved automatically.", "به‌طور خودکار تأیید شد."), file=sys.stderr)
        return

    print(
        tr(
            "This command needs your confirmation. Run it? [y/N]",
            "این دستور تأیید شما را لازم دارد. اجرا شود؟ [y/N]",
        ),
        end="",
        file=sys.stderr,
        flush=True,
    )
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt, OSError):
        answer = ""

    if answer in {"y", "yes"}:
        _respond_to_confirmation(api, conv, True)
        print(tr("Approved.", "تأیید شد."), file=sys.stderr)
        return

    _respond_to_confirmation(api, conv, False)
    print(tr("Rejected.", "رد شد."), file=sys.stderr)
    if sys.stdin is None or not sys.stdin.isatty():
        raise ConfirmationRejected(
            tr(
                "No interactive stdin: the pending command was rejected.",
                "ورودی تعاملی نیست؛ دستور در انتظار تأیید رد شد.",
            )
        )


def _render(ev: dict) -> str | None:
    kind, src = ev.get("kind", ""), ev.get("source", "")
    if kind == "MessageEvent" and src == "agent":
        msg = ev.get("llm_message", {}).get("content") or []
        return "\n".join(c.get("text", "") for c in msg if isinstance(c, dict))
    if kind == "ActionEvent":
        act = ev.get("action", {}) or {}
        cmd = act.get("command") or act.get("path") or act.get("kind", "")
        return f"  ▸ {ev.get('tool_name', 'tool')}: {str(cmd)[:160]}"
    if kind == "ObservationEvent":
        obs = ev.get("observation", {}) or {}
        code = obs.get("exit_code")
        return f"    ↳ exit {code}" if code not in (None, 0) else None
    if kind == "ConversationErrorEvent" or kind.endswith("ErrorEvent"):
        return f"  ✗ {ev.get('detail') or ev.get('code') or kind}"
    return None


def _follow(
    api: Api,
    conv: str,
    since_count: int,
    as_json: bool,
    timeout_s: int,
    yes: bool = False,
) -> str:
    """Poll events until the agent stops; returns the final execution status."""
    seen, deadline, status = since_count, time.time() + timeout_s, "running"
    while time.time() < deadline:
        page = api.req(
            "GET",
            f"/api/conversations/{conv}/events/search",
            params={"limit": 100, "sort_order": "TIMESTAMP"},
        )
        items = page.get("items", [])
        for ev in items[seen:]:
            if as_json:
                print(json.dumps(ev, ensure_ascii=False))
            else:
                line = _render(ev)
                if line:
                    print(line, flush=True)
        seen = max(seen, len(items))
        status = api.req("GET", f"/api/conversations/{conv}").get("execution_status", "")
        if status == "waiting_for_confirmation":
            _handle_confirmation(api, conv, items, as_json, yes)
            time.sleep(1.5)
            continue
        if status in ("finished", "error", "stuck", "paused", "idle") and seen >= len(items):
            return status
        time.sleep(1.5)
    return "timeout"


def _contains(root: str, paths: list[str]) -> bool:
    root_path = Path(root).resolve(strict=False)
    for candidate in paths:
        candidate_path = Path(candidate).resolve(strict=False)
        try:
            candidate_path.relative_to(root_path)
            return True
        except ValueError:
            continue
    return False


def _use_existing_tunnel(api: Api, working_dir: Path) -> bool:
    try:
        status = api.req("GET", "/v1/workspace/tunnel")
    except RuntimeError as exc:
        raise WorkspaceConnectionError(
            tr("could not check the workspace tunnel", "بررسی تونل فضای کاری ممکن نبود")
        ) from exc
    roots = status.get("roots") if isinstance(status.get("roots"), list) else []
    if not status.get("connected"):
        return False
    if not roots or not _contains(str(working_dir), [str(root) for root in roots]):
        allowed = ", ".join(str(root) for root in roots) or "unknown"
        raise WorkspaceConnectionError(
            tr(
                f"current directory {working_dir} is outside the connected Dorj Agent desktop roots ({allowed})",
                f"پوشه‌ی فعلی {working_dir} خارج از ریشه‌های تونل برنامه‌ی دسکتاپ است ({allowed})",
            )
        )
    print(
        tr(
            f"Using the existing Dorj Agent desktop workspace connection for {working_dir}.",
            f"از تونل فعال برنامه‌ی دسکتاپ Dorj Agent برای {working_dir} استفاده می‌شود.",
        ),
        file=sys.stderr,
    )
    return True


async def _run_with_workspace(
    api: Api, task: str, model: str | None, as_json: bool, timeout_s: int, yes: bool
) -> int:
    working_dir = Path.cwd().resolve(strict=False)
    def _print_result(conv: str, status: str) -> None:
        if not as_json:
            print(f"\n[{status}] {os.environ.get('NOVA_SERVER', api.server)}/conversations/{conv}", file=sys.stderr)

    if _use_existing_tunnel(api, working_dir):
        conv = await asyncio.to_thread(_start_conversation, api, model, working_dir)
        await asyncio.to_thread(_send, api, conv, task)
        status = await asyncio.to_thread(
            _follow, api, conv, 0, as_json, timeout_s, yes
        )
        _print_result(conv, status)
        return EXIT_OK if status in ("finished", "idle") else EXIT_ERR

    _, token = auth.bearer()
    async with WorkspaceBridge(api.server, token, working_dir) as bridge:
        roots = bridge.roots()
        if not _contains(str(working_dir), roots):
            raise WorkspaceConnectionError(
                tr("workspace bridge did not allow the current directory", "پل فضای کاری به پوشه‌ی فعلی اجازه ندارد")
            )
        if not as_json:
            print(
                tr(
                    f"Workspace tools connected to {working_dir}.",
                    f"ابزارهای فضای کاری به {working_dir} وصل شدند.",
                ),
                file=sys.stderr,
            )
        conv = await asyncio.to_thread(_start_conversation, api, model, working_dir)
        if not as_json:
            print(f"conversation {conv}", file=sys.stderr)
        # Send and poll while the reverse bridge is open. Long LLM calls still need its keepalive.
        await asyncio.to_thread(_send, api, conv, task)
        status = await asyncio.to_thread(
            _follow, api, conv, 0, as_json, timeout_s, yes
        )
    _print_result(conv, status)
    return EXIT_OK if status in ("finished", "idle") else EXIT_ERR


def cmd_run(a):
    api = Api()
    return asyncio.run(
        _run_with_workspace(api, a.task, a.model, a.json, a.timeout, a.yes)
    )


async def _chat_with_workspace(
    api: Api, resume: str | None, model: str | None, timeout_s: int, yes: bool
) -> int:
    working_dir = Path.cwd().resolve(strict=False)
    async with AsyncExitStack() as stack:
        if _use_existing_tunnel(api, working_dir):
            conv = resume or await asyncio.to_thread(_start_conversation, api, model, working_dir)
        else:
            _, token = auth.bearer()
            bridge = await stack.enter_async_context(WorkspaceBridge(api.server, token, working_dir))
            if not _contains(str(working_dir), bridge.roots()):
                raise WorkspaceConnectionError(
                    tr("workspace bridge did not allow the current directory", "پل فضای کاری به پوشه‌ی فعلی اجازه ندارد")
                )
            print(
                tr(
                    f"Workspace tools connected to {working_dir}.",
                    f"ابزارهای فضای کاری به {working_dir} وصل شدند.",
                ),
                file=sys.stderr,
            )
            conv = resume or await asyncio.to_thread(_start_conversation, api, model, working_dir)

        print(f"Dorj Agent chat — conversation {conv}. {tr('Ctrl+D to exit.', 'برای خروج Ctrl+D.')}")
        def count_events():
            return len(api.req("GET", f"/api/conversations/{conv}/events/search", params={"limit": 100}).get("items", []))

        seen = await asyncio.to_thread(count_events)
        while True:
            try:
                text = (await asyncio.to_thread(input, "\n› ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return EXIT_OK
            if not text:
                continue
            # Chat stays in this AsyncExitStack, so every turn keeps the reverse bridge open.
            await asyncio.to_thread(_send, api, conv, text)
            await asyncio.to_thread(
                _follow, api, conv, seen, False, timeout_s, yes
            )
            seen = await asyncio.to_thread(count_events)
    return EXIT_OK


def cmd_chat(a):
    api = Api()
    return asyncio.run(
        _chat_with_workspace(api, a.resume, a.model, a.timeout, a.yes)
    )


def _validate_bridge_root(path: str | None) -> Path:
    root = Path(path or Path.cwd()).expanduser().resolve(strict=False)
    if not root.exists():
        raise WorkspaceConnectionError(
            tr(f"workspace root does not exist: {root}", f"ریشه‌ی فضای کار وجود ندارد: {root}")
        )
    if not root.is_dir():
        raise WorkspaceConnectionError(
            tr(f"workspace root is not a directory: {root}", f"ریشه‌ی فضای کار یک پوشه نیست: {root}")
        )
    return root


def _print_bilingual(en: str, fa: str) -> None:
    print(en, file=sys.stderr, flush=True)
    print(fa, file=sys.stderr, flush=True)


def _bridge_hostname(bridge: WorkspaceBridge) -> str:
    hello = bridge.hello if isinstance(bridge.hello, dict) else {}
    system = hello.get("system") if isinstance(hello.get("system"), dict) else {}
    return str(system.get("hostname") or socket.gethostname())


def _session_replaced(detail: str, close_code: int | None) -> bool:
    return close_code == 4001 or "4001" in detail or "session_replaced" in detail


async def _bridge_forever(
    server: str,
    token: str,
    root: Path,
    *,
    bridge_factory: Callable[..., WorkspaceBridge] = WorkspaceBridge,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> int:
    """Keep a reverse workspace tunnel open; reconnect after transient failures."""
    do_sleep = sleep or asyncio.sleep
    attempts, was_connected, delay = 0, False, 1
    while True:
        bridge = bridge_factory(server, token, root)
        try:
            async with bridge:
                was_connected, attempts = True, 0
                hostname = _bridge_hostname(bridge)
                _print_bilingual(
                    f"Workspace connected on {hostname}:{root}. Keep this window open.",
                    f"فضای کار روی {hostname}:{root} وصل است. این پنجره را باز نگه دارید.",
                )
                await bridge.wait_closed()
                was_connected = False
                detail = bridge.close_reason or "connection closed"
                if _session_replaced(detail, bridge.close_code):
                    _print_bilingual(
                        "Workspace bridge stopped: session replaced on the gateway.",
                        "پل فضای کاری متوقف شد: نشست روی gateway جایگزین شد.",
                    )
                    return EXIT_OK
                _print_bilingual(
                    f"Workspace bridge disconnected ({detail or 'unknown reason'}).",
                    f"پل فضای کاری قطع شد ({detail or 'دلیل نامشخص'}).",
                )
        except WorkspaceConnectionError as exc:
            detail = str(exc)
            close_code = getattr(exc, "close_code", bridge.close_code)
            if _session_replaced(detail, close_code):
                _print_bilingual(
                    "Workspace bridge stopped: session replaced on the gateway.",
                    "پل فضای کاری متوقف شد: نشست روی gateway جایگزین شد.",
                )
                return EXIT_OK
            attempts += 1
            delay = 1 if was_connected else min(30, 2 ** (attempts - 1))
            was_connected = False
            _print_bilingual(
                f"Workspace bridge failed ({detail}); reconnecting in {delay}s.",
                f"پل فضای کاری ناموفق بود ({detail})؛ اتصال مجدد پس از {delay} ثانیه.",
            )
        except OSError as exc:
            attempts += 1
            delay = 1 if was_connected else min(30, 2 ** (attempts - 1))
            was_connected = False
            _print_bilingual(
                f"Workspace bridge failed ({exc}); reconnecting in {delay}s.",
                f"پل فضای کاری ناموفق بود ({exc})؛ اتصال مجدد پس از {delay} ثانیه.",
            )
        await do_sleep(delay)


def _bridge_config_dir() -> Path:
    # auth.CONFIG_DIR is normally ~/.config/nova; bridge files belong beside it.
    return auth.CONFIG_DIR.parent / "dorj-agent"


def _detach_bridge(root: Path) -> int:
    auth.bearer()  # Fail before daemonizing when the CLI is not signed in.
    config_dir = _bridge_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    log_path = config_dir / "bridge.log"
    pid_path = config_dir / "bridge.pid"
    argv = [sys.executable, "-m", "dorj_agent_cli.main", "bridge", "--root", str(root)]
    kwargs: dict[str, object] = {"stdin": subprocess.DEVNULL}
    if os.name == "nt":
        detached_process = getattr(subprocess, "DETACHED_PROCESS", 0)
        create_new_process_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        kwargs["creationflags"] = detached_process | create_new_process_group
    else:
        kwargs["start_new_session"] = True
    with log_path.open("a", encoding="utf-8", buffering=1) as log:
        process = subprocess.Popen(
            argv,
            stdout=log,
            stderr=log,
            close_fds=True,
            **kwargs,
        )
    pid_path.write_text(str(process.pid), encoding="utf-8")
    _print_bilingual(
        f"Workspace bridge started in the background (pid {process.pid}). Logs: {log_path}",
        f"پل فضای کاری در پس‌زمینه اجرا شد (pid {process.pid}). لاگ‌ها: {log_path}",
    )
    return EXIT_OK


async def _run_bridge(root: Path) -> int:
    server, token = auth.bearer()
    return await _bridge_forever(server, token, root)


def cmd_bridge(a):
    try:
        root = _validate_bridge_root(a.root)
        if a.detach:
            return _detach_bridge(root)
        return asyncio.run(_run_bridge(root))
    except KeyboardInterrupt:
        _print_bilingual(
            "Workspace bridge closed.",
            "پل فضای کاری بسته شد.",
        )
        return EXIT_OK


def cmd_conversations(a):
    api = Api()
    res = api.req("GET", "/api/conversations/search", params={"limit": a.limit})
    rows = [
        {
            "id": c["id"],
            "title": c.get("title") or "",
            "status": c.get("execution_status"),
            "created": c.get("created_at"),
        }
        for c in res.get("items", [])
    ]
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            created = r["created"][:19] if r["created"] else ""
            print(f"{r['id']}  {r['status']:<9} {created:<19}  {r['title']}")
    return EXIT_OK


def cmd_ws(a):
    api = Api()
    method = {"status": "GET", "start": "POST", "stop": "DELETE"}[a.action]
    _print(api.req(method, "/v1/workspace"), a.json)
    return EXIT_OK


def cmd_whoami(a):
    _print(Api().req("GET", "/v1/me"), a.json)
    return EXIT_OK


def cmd_models(a):
    res = Api().req("GET", "/v1/models")
    if a.json:
        print(json.dumps(res))
    else:
        for m in res.get("models", []):
            print(("* " if m == res.get("default") else "  ") + m)
    return EXIT_OK


def cmd_usage(a):
    _print(Api().req("GET", "/v1/usage"), a.json)
    return EXIT_OK


def cmd_login(a):
    username = a.username or input(tr("username: ", "نام کاربری: ")).strip()
    password = os.environ.get("NOVA_PASSWORD") or getpass.getpass(tr("password: ", "گذرواژه: "))
    creds = auth.login(a.server, username, password)
    me = Api().req("GET", "/v1/me")
    print(
        tr(
            f"Signed in as {me['email']} ({creds['customer']})",
            f"به عنوان {me['email']} ({creds['customer']}) وارد شدید",
        )
    )
    return EXIT_OK


def cmd_logout(a):
    auth.logout()
    print(tr("Signed out.", "خروج انجام شد."))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dorj-agent",
        description=tr("Dorj Agent command line", "خط فرمان Dorj Agent"),
    )
    p.add_argument("--version", action="version", version=f"Dorj Agent {__version__}")
    p.add_argument("--json", action="store_true", help=tr("JSON output for scripts and CI", "خروجی JSON برای اسکریپت و CI"))
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("login", help=tr("sign in", "ورود"))
    s.add_argument("--server", default=auth.DEFAULT_SERVER)
    s.add_argument("--username", "-u")
    s.set_defaults(fn=cmd_login)

    sub.add_parser("logout", help=tr("sign out", "خروج")).set_defaults(fn=cmd_logout)
    sub.add_parser("whoami", help=tr("show the signed-in user", "نمایش کاربر واردشده")).set_defaults(fn=cmd_whoami)

    s = sub.add_parser("run", help=tr("run one task in the current directory", "اجرای یک کار در پوشه‌ی فعلی"))
    s.add_argument("task", help=tr("task text", "متن کار"))
    s.add_argument("--model", help=tr("model name", "نام مدل"))
    s.add_argument("--timeout", type=int, default=1800, help=tr("timeout in seconds", "مهلت به ثانیه"))
    s.add_argument(
        "--yes",
        action="store_true",
        help=tr(
            "approve pending tool confirmations automatically",
            "تأیید خودکار درخواست‌های ابزار",
        ),
    )
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("chat", help=tr("interactive session in the current directory", "جلسه‌ی تعاملی در پوشه‌ی فعلی"))
    s.add_argument("--model", help=tr("model name", "نام مدل"))
    s.add_argument("--resume", help=tr("conversation id to resume", "شناسه گفتگو برای ادامه"))
    s.add_argument("--timeout", type=int, default=1800, help=tr("timeout in seconds", "مهلت به ثانیه"))
    s.add_argument(
        "--yes",
        action="store_true",
        help=tr(
            "approve pending tool confirmations automatically",
            "تأیید خودکار درخواست‌های ابزار",
        ),
    )
    s.set_defaults(fn=cmd_chat)

    s = sub.add_parser("conversations", help=tr("list conversations", "فهرست گفتگوها"))
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_conversations)

    s = sub.add_parser(
        "bridge",
        help=tr("keep a remote folder connected as the workspace", "اتصال دائمی یک پوشه‌ی راه دور به‌عنوان فضای کار"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=tr(
            "Works on a server, in WSL, or in a notebook/VM. It replaces the desktop app's "
            "workspace tunnel for the same account. Exit codes: 0 clean stop or Ctrl+C, "
            "1 error, 2 bad arguments, 3 sign-in, 4 workspace connection.",
            "روی سرور، WSL یا نوت‌بوک/VM کار می‌کند و تونل فضای کاری همان حساب در برنامه‌ی "
            "دسکتاپ را جایگزین می‌کند. کدهای خروج: 0 توقف تمیز یا Ctrl+C، 1 خطا، "
            "2 آرگومان نامعتبر، 3 ورود، 4 اتصال فضای کاری.",
        ),
    )
    s.add_argument("--root", help=tr("folder to expose (default: current directory)", "پوشه‌ی قابل‌دسترس (پیش‌فرض: پوشه‌ی فعلی)"))
    s.add_argument(
        "--detach",
        action="store_true",
        help=tr(
            "run in the background; logs to ~/.config/dorj-agent/bridge.log",
            "اجرا در پس‌زمینه؛ لاگ در ‎~/.config/dorj-agent/bridge.log",
        ),
    )
    s.set_defaults(fn=cmd_bridge)

    s = sub.add_parser("ws", help=tr("agent-server workspace", "فضای کاری agent-server"))
    s.add_argument("action", choices=["status", "start", "stop"])
    s.set_defaults(fn=cmd_ws)

    sub.add_parser("models", help=tr("list models", "فهرست مدل‌ها")).set_defaults(fn=cmd_models)
    sub.add_parser("usage", help=tr("show usage", "نمایش مصرف")).set_defaults(fn=cmd_usage)
    return p


def main(argv=None):
    p = build_parser()
    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except auth.AuthError as e:
        print(f"{tr('auth', 'احراز هویت')}: {e}", file=sys.stderr)
        return EXIT_AUTH
    except QuotaError as e:
        print(f"{tr('quota', 'سهمیه')}: {e}", file=sys.stderr)
        return EXIT_QUOTA
    except ConfirmationRejected as e:
        print(str(e), file=sys.stderr)
        return EXIT_CONFIRMATION_REJECTED
    except WorkspaceConnectionError as e:
        message = tr(
            "workspace connection failed: " + str(e),
            "اتصال فضای کاری ناموفق بود: " + str(e),
        )
        print(message, file=sys.stderr)
        return EXIT_WORKSPACE
    except (RuntimeError, httpx.HTTPError) as e:
        print(f"{tr('error', 'خطا')}: {e}", file=sys.stderr)
        return EXIT_ERR


if __name__ == "__main__":
    sys.exit(main())

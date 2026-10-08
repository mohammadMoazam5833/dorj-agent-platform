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
import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path

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

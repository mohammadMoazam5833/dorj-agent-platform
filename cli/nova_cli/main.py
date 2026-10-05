"""nova — NovaAgent CLI.

  nova login | logout | whoami
  nova run "task" [--model M] [--json]      one-shot task; exit 0 ok, 2 quota, 3 auth, 1 error
  nova chat [--resume ID]                   interactive session
  nova conversations [--limit N]
  nova ws status | start | stop
  nova models | usage
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time

import httpx

from . import __version__, auth

EXIT_OK, EXIT_ERR, EXIT_QUOTA, EXIT_AUTH = 0, 1, 2, 3


class Api:
    def __init__(self):
        self.server, token = auth.bearer()
        self.c, self.ext = auth.client_for(self.server, token)

    def req(self, method, path, **kw):
        r = self.c.request(method, path, extensions=self.ext, **kw)
        if r.status_code in (401, 403):
            raise auth.AuthError("dastresi rad shod — `nova login`")
        if r.status_code == 429 or "budget" in r.text.lower() and r.status_code >= 400:
            raise QuotaError(r.text[:300])
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path}: {r.status_code} {r.text[:300]}")
        return r.json() if r.content else {}


class QuotaError(RuntimeError):
    pass


def _print(obj, as_json):
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        for k, v in obj.items():
            print(f"{k:>14}: {v}")


# ── agent conversations (through nova-control's proxy to your own agent-server) ──

def _start_conversation(api: Api, model: str | None) -> str:
    st = api.req("GET", "/api/settings", headers={"X-Expose-Secrets": "encrypted"})
    agent_settings = st["agent_settings"]
    if model:
        agent_settings["llm"]["model"] = f"litellm_proxy/{model}"
    body = {"workspace": {"working_dir": "workspace/project", "kind": "LocalWorkspace"},
            "agent_settings": agent_settings, "secrets_encrypted": True, "tags": {"source": "nova-cli"}}
    return api.req("POST", "/api/conversations", json=body)["id"]


def _send(api: Api, conv: str, text: str) -> None:
    api.req("POST", f"/api/conversations/{conv}/events",
            json={"role": "user", "content": [{"type": "text", "text": text}], "run": True})


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


def _follow(api: Api, conv: str, since_count: int, as_json: bool, timeout_s: int) -> str:
    """Poll events until the agent stops; returns the final execution status."""
    seen, deadline, status = since_count, time.time() + timeout_s, "running"
    while time.time() < deadline:
        page = api.req("GET", f"/api/conversations/{conv}/events/search", params={"limit": 100, "sort_order": "TIMESTAMP"})
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
        if status in ("finished", "error", "stuck", "paused", "idle") and seen >= len(items):
            return status
        time.sleep(1.5)
    return "timeout"


def cmd_run(a):
    api = Api()
    api.req("POST", "/v1/workspace")
    conv = _start_conversation(api, a.model)
    if not a.json:
        print(f"conversation {conv}", file=sys.stderr)
    _send(api, conv, a.task)
    status = _follow(api, conv, 0, a.json, a.timeout)
    if not a.json:
        print(f"\n[{status}] {os.environ.get('NOVA_SERVER', api.server)}/conversations/{conv}", file=sys.stderr)
    return EXIT_OK if status in ("finished", "idle") else EXIT_ERR


def cmd_chat(a):
    api = Api()
    api.req("POST", "/v1/workspace")
    conv = a.resume or _start_conversation(api, a.model)
    print(f"NovaAgent chat — conversation {conv}. Ctrl+D baraye khorooj.")
    seen = len(api.req("GET", f"/api/conversations/{conv}/events/search", params={"limit": 100}).get("items", []))
    while True:
        try:
            text = input("\n› ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return EXIT_OK
        if not text:
            continue
        _send(api, conv, text)
        _follow(api, conv, seen, False, a.timeout)
        seen = len(api.req("GET", f"/api/conversations/{conv}/events/search", params={"limit": 100}).get("items", []))


def cmd_conversations(a):
    api = Api()
    res = api.req("GET", "/api/conversations/search", params={"limit": a.limit})
    rows = [{"id": c["id"], "title": c.get("title") or "", "status": c.get("execution_status"), "created": c.get("created_at")}
            for c in res.get("items", [])]
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            print(f"{r['id']}  {r['status']:<9} {r['created'][:19] if r['created'] else '':<19}  {r['title']}")
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
    username = a.username or input("nam-e karbari (username): ").strip()
    password = os.environ.get("NOVA_PASSWORD") or getpass.getpass("gozarvazheh (password): ")
    creds = auth.login(a.server, username, password)
    me = Api().req("GET", "/v1/me")
    print(f"vared shodid be onvan-e {me['email']} ({creds['customer']})")
    return EXIT_OK


def cmd_logout(a):
    auth.logout()
    print("khorooj anjam shod")
    return EXIT_OK


def main(argv=None):
    p = argparse.ArgumentParser(prog="nova", description="NovaAgent CLI")
    p.add_argument("--version", action="version", version=f"nova {__version__}")
    p.add_argument("--json", action="store_true", help="khoroji-e JSON baraye script va CI")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("login"); s.add_argument("--server", default=auth.DEFAULT_SERVER); s.add_argument("--username", "-u"); s.set_defaults(fn=cmd_login)
    sub.add_parser("logout").set_defaults(fn=cmd_logout)
    sub.add_parser("whoami").set_defaults(fn=cmd_whoami)
    s = sub.add_parser("run"); s.add_argument("task"); s.add_argument("--model"); s.add_argument("--timeout", type=int, default=1800); s.set_defaults(fn=cmd_run)
    s = sub.add_parser("chat"); s.add_argument("--model"); s.add_argument("--resume"); s.add_argument("--timeout", type=int, default=1800); s.set_defaults(fn=cmd_chat)
    s = sub.add_parser("conversations"); s.add_argument("--limit", type=int, default=20); s.set_defaults(fn=cmd_conversations)
    s = sub.add_parser("ws"); s.add_argument("action", choices=["status", "start", "stop"]); s.set_defaults(fn=cmd_ws)
    sub.add_parser("models").set_defaults(fn=cmd_models)
    sub.add_parser("usage").set_defaults(fn=cmd_usage)
    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except auth.AuthError as e:
        print(f"auth: {e}", file=sys.stderr)
        return EXIT_AUTH
    except QuotaError as e:
        print(f"quota: {e}", file=sys.stderr)
        return EXIT_QUOTA
    except (RuntimeError, httpx.HTTPError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERR


if __name__ == "__main__":
    sys.exit(main())

"""Sign-in for the nova CLI - the same contract as the Dorj Agent desktop app: POST /api/auth/login
{username, password} -> tenant token (checked against the organisation's accounts server-side).
Stored in ~/.config/nova/credentials.json (mode 0600); `nova logout` forgets it, and an
administrator's "sign out devices" (token rotation) invalidates it everywhere."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import httpx

CONFIG_DIR = Path(os.environ.get("NOVA_CONFIG_DIR", Path.home() / ".config" / "nova"))
CRED_FILE = CONFIG_DIR / "credentials.json"
DEFAULT_SERVER = os.environ.get("NOVA_SERVER", "https://nova.isigpu.local")


class AuthError(RuntimeError):
    pass


def _verify():
    # internal CA: honour NOVA_CA_BUNDLE / SSL_CERT_FILE, else system store
    return os.environ.get("NOVA_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE") or True


def client_for(server: str, token: str | None = None) -> tuple[httpx.Client, dict]:
    """httpx client for ``server`` (+ request extensions). NOVA_RESOLVE="host=ip" works like curl
    --resolve for hosts not in DNS yet; TLS is still verified against the real host name (SNI)."""
    base, headers, ext = server, {}, {}
    if token:
        headers["X-Session-API-Key"] = token
    host = httpx.URL(server).host
    for pair in filter(None, os.environ.get("NOVA_RESOLVE", "").split(",")):
        name, _, ip = pair.partition("=")
        if name.strip() == host and ip.strip():
            base = str(httpx.URL(server).copy_with(host=ip.strip()))
            headers["Host"] = host
            ext = {"sni_hostname": host}
    return httpx.Client(base_url=base, headers=headers, verify=_verify(), timeout=httpx.Timeout(30, read=300)), ext


def save(creds: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CRED_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(creds))
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(CRED_FILE)


def load() -> dict:
    try:
        return json.loads(CRED_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _forget_local_credential() -> None:
    try:
        CRED_FILE.unlink()
    except FileNotFoundError:
        pass


def logout() -> None:
    """Forget the local CLI credential and ask the server to revoke it everywhere."""
    creds = load()
    if not (creds.get("server") and creds.get("token")):
        _forget_local_credential()
        return

    c, ext = client_for(creds["server"], creds["token"])
    try:
        with c:
            r = c.post("/api/auth/logout", extensions=ext)
    except httpx.HTTPError as e:
        _forget_local_credential()
        raise AuthError("server-side sign-out failed: connection error") from e

    _forget_local_credential()
    # A stale/expired token is already useless, so treat 401 as signed out.
    if r.status_code not in (200, 401):
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        raise AuthError(body.get("error") or f"server-side sign-out failed (HTTP {r.status_code})")


def login(server: str, username: str, password: str) -> dict:
    c, ext = client_for(server)
    with c:
        r = c.post("/api/auth/login", json={"username": username, "password": password}, extensions=ext)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or not body.get("ok"):
        raise AuthError(body.get("error") or f"sign-in failed (HTTP {r.status_code})")
    creds = {"server": server, "token": body["token"], "customer": body.get("customer", "")}
    save(creds)
    return creds


def bearer() -> tuple:
    """(server, tenant token) of the signed-in user."""
    creds = load()
    if not creds.get("token"):
        raise AuthError("Not signed in: run `dorj-agent login`")
    return creds["server"], creds["token"]

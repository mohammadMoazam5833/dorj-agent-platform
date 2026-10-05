"""Desktop-client authentication (NovaAgent's contract, scripts/deploy/nova-ingress.mjs):

POST /api/auth/login {username, password} -> {ok, customer, token}. The credentials are checked
against Keycloak realm "nova" (AD users) server-side; the client only ever holds its tenant token.

Every later request carries that token the way NovaAgent's UI and client send it:
X-Session-API-Key header (UI REST), ``session_api_key`` query (UI WebSockets), ``?t=`` on the first
page load (Electron), the HttpOnly ``nova_t`` cookie set from it, or ``Authorization: Bearer``
(nova CLI). Unlike the VM ingress there is no cookie-by-name and no fallback tenant: no valid
token, no access.
"""
from __future__ import annotations

import base64
import json
import logging

import httpx
from fastapi import HTTPException, Request

from . import tenants
from .config import settings
from .identity import Identity

log = logging.getLogger("nova.auth")
COOKIE = "nova_t"


class LoginFailed(Exception):
    pass


def _claims(jwt: str) -> dict:
    payload = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


async def password_login(username: str, password: str) -> dict:
    """Resource-owner password grant against realm nova over the in-cluster Keycloak Service (the
    id_token arrives on this direct back channel, so its claims are trusted as-is)."""
    if not username or not password or len(username) > 256 or len(password) > 1024:
        raise LoginFailed("invalid username or password")
    url = f"{settings.keycloak_url}/realms/{settings.keycloak_realm}/protocol/openid-connect/token"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, data={"grant_type": "password", "client_id": settings.desktop_client_id,
                                    "client_secret": settings.desktop_client_secret, "username": username,
                                    "password": password, "scope": "openid email profile"})
    if r.status_code != 200:
        err = (r.json() if r.headers.get("content-type", "").startswith("application/json") else {}).get("error", "")
        log.info("login rejected", extra={"event": "login_failed", "user": username[:64], "reason": err or r.status_code})
        raise LoginFailed("invalid username or password" if r.status_code in (400, 401) else "sign-in service unavailable")
    claims = _claims(r.json()["id_token"])
    email = (claims.get("email") or "").strip().lower()
    if "@" not in email:
        raise LoginFailed("this account has no email address; ask an administrator")
    groups = tuple(g.strip("/") for g in claims.get("groups", []) if isinstance(g, str))
    return tenants.ensure_for(email, claims.get("name") or claims.get("preferred_username") or "", groups)


def token_from(request) -> str:
    h = request.headers
    auth = h.get("authorization", "")
    for cand in (h.get("x-session-api-key"), auth[7:] if auth.lower().startswith("bearer ") else None,
                 request.query_params.get("session_api_key"), request.query_params.get("t"),
                 request.cookies.get(COOKIE)):
        if cand and tenants.by_token(cand):
            return cand
    return ""


def identity_for(request) -> Identity | None:
    c = tenants.by_token(token_from(request))
    return tenants.identity_of(c) if c else None


def current_identity(request: Request) -> Identity:
    ident = identity_for(request)
    if ident is None:
        raise HTTPException(status_code=401, detail="sign in with the NovaAgent app")
    return ident


def is_admin(ident: Identity) -> bool:
    return settings.admin_group in ident.groups

"""Caller identity. nova-control is reachable only from nova-auth (oauth2-proxy on Keycloak realm "nova",
enforced by an Istio AuthorizationPolicy on the caller's mTLS principal). nova-auth authenticates every
request (browser session cookie, or the CLI's bearer id_token checked against the realm JWKS), strips any
client-sent X-Forwarded-* identity headers and writes the verified ones. Nothing else selects a workspace."""
import re
from dataclasses import dataclass

from fastapi import HTTPException, Request

_SLUG_BAD = re.compile(r"[^a-z0-9-]+")


@dataclass(frozen=True)
class Identity:
    email: str
    slug: str          # DNS-1123 label used to name the user's workspace objects
    groups: tuple


def slug_for(email: str) -> str:
    local = email.split("@", 1)[0].lower()
    s = _SLUG_BAD.sub("-", local).strip("-")[:40].strip("-")
    if not s:
        raise ValueError("email has no usable local part")
    return s


def identity_from_headers(headers) -> Identity:
    email = (headers.get("x-forwarded-email") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=401, detail="not signed in")
    groups = tuple(g.strip() for g in (headers.get("x-forwarded-groups") or "").split(",") if g.strip())
    return Identity(email=email, slug=slug_for(email), groups=groups)


def current_identity(request: Request) -> Identity:
    return identity_from_headers(request.headers)

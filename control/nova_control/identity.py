"""Caller identity. nova-control only listens on 127.0.0.1 behind the oauth2-proxy sidecar, so the
X-Auth-Request-* headers it receives were set by oauth2-proxy after a verified login (browser
cookie) or a verified Dex JWT (CLI bearer token). Nothing the client sends selects a workspace."""
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
    email = (headers.get("x-auth-request-email") or headers.get("x-forwarded-email") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=401, detail="not signed in")
    groups = tuple(g for g in (headers.get("x-auth-request-groups") or "").split(",") if g)
    return Identity(email=email, slug=slug_for(email), groups=groups)


def current_identity(request: Request) -> Identity:
    return identity_from_headers(request.headers)

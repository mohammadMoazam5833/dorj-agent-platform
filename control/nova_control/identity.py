"""Caller identity value type. Requests are authenticated by tenant token (auth.py); the registry
entry (tenants.py) maps the token to the Keycloak realm "nova" user it was issued to."""
import re
from dataclasses import dataclass


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

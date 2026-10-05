"""Customer registry: one entry per signed-in user, NovaAgent's ~/.nova-customers.json format
({"customers": [{"name", "token", "roots", ...}]}), stored in the Secret ``nova-customers``.

``token`` is the tenant ("workspace") token NovaAgent's desktop client receives from
POST /api/auth/login: it is the UI session key, the reverse-workspace tunnel credential, and what
the user's agent-server presents to the gateway (/c/<name>/v1). Rotating it signs every device of
that user out. Writes use optimistic concurrency (resourceVersion) since nova-control has replicas.
"""
from __future__ import annotations

import base64
import hmac
import json
import logging
import secrets
import threading
import time

from kubernetes import client
from kubernetes.client.rest import ApiException

from .config import settings
from .identity import Identity, slug_for

log = logging.getLogger("nova.tenants")
SECRET = "nova-customers"
KEY = "customers.json"
_CACHE_TTL_S = 3.0
_lock = threading.Lock()
_cache: tuple[float, list[dict], str | None] = (0.0, [], None)


def _core() -> client.CoreV1Api:
    from .workspaces import _apis

    return _apis()[0]


def _read() -> tuple[list[dict], str | None]:
    try:
        s = _core().read_namespaced_secret(SECRET, settings.control_namespace)
    except ApiException as e:
        if e.status == 404:
            return [], None
        raise
    raw = base64.b64decode((s.data or {}).get(KEY, "") or b"e30=").decode() or "{}"
    return list(json.loads(raw).get("customers", [])), s.metadata.resource_version


def all_customers(fresh: bool = False) -> list[dict]:
    global _cache
    with _lock:
        if fresh or time.monotonic() - _cache[0] > _CACHE_TTL_S:
            customers, rv = _read()
            _cache = (time.monotonic(), customers, rv)
        return [dict(c) for c in _cache[1]]


def _write(mutate) -> list[dict]:
    """Apply ``mutate(customers) -> customers`` with retries on concurrent updates."""
    global _cache
    core = _core()
    for _ in range(8):
        customers, rv = _read()
        new = mutate([dict(c) for c in customers])
        body = {KEY: base64.b64encode(json.dumps({"customers": new}, indent=1).encode()).decode()}
        try:
            if rv is None:
                core.create_namespaced_secret(settings.control_namespace, client.V1Secret(
                    metadata=client.V1ObjectMeta(name=SECRET, labels={"app.kubernetes.io/part-of": "nova"}), data=body))
            else:
                core.replace_namespaced_secret(SECRET, settings.control_namespace, client.V1Secret(
                    metadata=client.V1ObjectMeta(name=SECRET, resource_version=rv,
                                                 labels={"app.kubernetes.io/part-of": "nova"}), data=body))
            with _lock:
                _cache = (time.monotonic(), new, None)
            return new
        except ApiException as e:
            if e.status not in (409, 422):
                raise
            time.sleep(0.2)
    raise RuntimeError("customer registry update kept conflicting")


def by_token(token: str | None) -> dict | None:
    tok = (token or "").strip()
    if len(tok) < 16:
        return None
    for c in all_customers():
        if not c.get("disabled") and hmac.compare_digest(str(c.get("token", "")), tok):
            return c
    return None


def by_name(name: str) -> dict | None:
    return next((c for c in all_customers() if c.get("name") == name), None)


def ensure_for(email: str, display_name: str = "", groups: tuple = ()) -> dict:
    """The user's registry entry, created on first sign-in (token minted then)."""
    email = email.strip().lower()
    name = slug_for(email)
    cur = by_name(name)
    if cur and cur.get("email") == email and set(cur.get("groups", [])) == set(groups):
        return cur

    def mutate(customers):
        for c in customers:
            if c.get("name") == name:
                c.update(email=email, groups=list(groups), display_name=display_name or c.get("display_name", ""))
                return customers
        customers.append({"name": name, "email": email, "display_name": display_name, "groups": list(groups),
                          "token": secrets.token_hex(24), "roots": [], "created": int(time.time()), "disabled": False})
        return customers

    return next(c for c in _write(mutate) if c["name"] == name)


def rotate_token(name: str) -> str:
    tok = secrets.token_hex(24)

    def mutate(customers):
        for c in customers:
            if c.get("name") == name:
                c["token"] = tok
        return customers

    _write(mutate)
    return tok


def set_disabled(name: str, disabled: bool) -> None:
    def mutate(customers):
        for c in customers:
            if c.get("name") == name:
                c["disabled"] = disabled
        return customers

    _write(mutate)


def remove(name: str) -> None:
    _write(lambda customers: [c for c in customers if c.get("name") != name])


def identity_of(c: dict) -> Identity:
    return Identity(email=c["email"], slug=c["name"], groups=tuple(c.get("groups", [])))


def gateway_view() -> dict:
    """What NovaAgent's gateway reads: active customers only, no personal data beyond the name."""
    return {"customers": [{"name": c["name"], "token": c["token"], "roots": c.get("roots", [])}
                          for c in all_customers() if not c.get("disabled")]}

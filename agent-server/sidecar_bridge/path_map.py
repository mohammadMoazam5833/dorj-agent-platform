"""Path helpers for mapping agent working_dir paths onto sidecar roots.

Phase 2 uses **identity mapping**: the path the agent-server sees must already
be an absolute path under a sidecar allowlisted root. Relative paths resolve
against ``working_dir``.
"""

from __future__ import annotations

import os
from pathlib import PurePosixPath, PureWindowsPath


def _pure_path(path: str):
    if os.name == "nt" or (len(path) >= 2 and path[1] == ":"):
        return PureWindowsPath(path)
    return PurePosixPath(path)


def is_absolute_path(path: str) -> bool:
    return _pure_path(path).is_absolute()


def join_under_working_dir(working_dir: str, path: str | os.PathLike[str]) -> str:
    """Resolve ``path`` against ``working_dir`` when relative; normalize ``..``."""
    raw = os.fspath(path)
    if not raw:
        raise ValueError("path is required")
    if is_absolute_path(raw):
        # os.path.normpath keeps drive letters / POSIX correctly enough for bridge
        return os.path.normpath(raw)
    return os.path.normpath(os.path.join(working_dir, raw))


def assert_under_roots(absolute_path: str, roots: list[str]) -> str:
    """Return ``absolute_path`` if it sits under one of ``roots``; else raise."""
    if not roots:
        raise PermissionError("no allowlisted roots configured")
    cand_n = os.path.normpath(absolute_path).replace("\\", "/").rstrip("/")
    for root in roots:
        root_n = os.path.normpath(root).replace("\\", "/").rstrip("/")
        if cand_n == root_n or cand_n.startswith(root_n + "/"):
            return absolute_path
    raise PermissionError(f"path denied (outside sidecar roots): {absolute_path}")


def map_agent_path_to_sidecar(
    path: str | os.PathLike[str],
    *,
    working_dir: str,
    roots: list[str] | None = None,
) -> str:
    """Map an agent-facing path to the absolute path the sidecar should see.

    Phase 2: resolve relative paths under ``working_dir``, optionally assert
    allowlist membership when ``roots`` is provided.
    """
    absolute = join_under_working_dir(working_dir, path)
    if roots is not None:
        return assert_under_roots(absolute, roots)
    return absolute


_ROOTS_TTL_CACHE: dict[str, tuple[float, list[str]]] = {}


def get_client_roots(client, ttl_s: float = 60.0) -> list[str]:
    """The CLIENT's real host-OS roots via a hello round-trip (TTL-cached)."""
    import time as _time

    key = str(getattr(client, "base_url", ""))
    hit = _ROOTS_TTL_CACHE.get(key)
    now = _time.monotonic()
    if hit and now - hit[0] < ttl_s:
        return hit[1]
    try:
        hello = client.hello()
    except Exception:
        return []
    roots = hello.get("roots") if isinstance(hello, dict) else None
    resolved = [str(r) for r in roots] if isinstance(roots, list) else []
    if resolved:
        _ROOTS_TTL_CACHE[key] = (now, resolved)
    return resolved


def map_to_client_roots(absolute: str, working_dir: str, roots: list[str]) -> str:
    """Alias server-side working_dir paths onto the customer's host-OS root."""
    if not roots:
        return absolute

    def _n(p: str) -> str:
        return str(p).replace("\\", "/").rstrip("/").lower()

    an, base0 = _n(absolute), _n(roots[0])
    if an == base0 or an.startswith(base0 + "/"):
        return absolute
    a_s = str(absolute).replace("\\", "/")
    wd_s = str(working_dir).replace("\\", "/").rstrip("/")
    if wd_s and (a_s.lower() == wd_s.lower() or a_s.lower().startswith(wd_s.lower() + "/")):
        rel = a_s[len(wd_s):].lstrip("/")
    else:
        rel = a_s.rstrip("/").rsplit("/", 1)[-1]
    base = roots[0]
    if len(base) >= 2 and base[1] == ":":
        from pathlib import PureWindowsPath

        return str(PureWindowsPath(base, rel)) if rel else base
    return os.path.join(base, rel) if rel else base

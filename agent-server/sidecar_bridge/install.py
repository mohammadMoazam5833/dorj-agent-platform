"""Install LocalWorkspace → sidecar monkeypatch when env flags are set."""

from __future__ import annotations

import logging
import os

from .client import client_from_env

logger = logging.getLogger(__name__)

_INSTALLED = False


def is_bridge_enabled(env: dict[str, str] | None = None) -> bool:
    e = env if env is not None else os.environ
    return client_from_env(e) is not None


def try_install_bridge(env: dict[str, str] | None = None) -> bool:
    """Import-time hook: patch LocalWorkspace when sidecar URL+token are present."""
    global _INSTALLED
    if _INSTALLED:
        return True

    client = client_from_env(env)
    if client is None:
        logger.debug(
            "sidecar_bridge: not enabled "
            "(need NOVAAGENT_LOCAL_TOOLS_TOKEN and URL or SIDECAR=1)"
        )
        return False

    try:
        health = client.health()
        if not health.get("ok"):
            logger.warning("sidecar_bridge: health check returned not-ok: %s", health)
    except Exception as exc:  # noqa: BLE001
        # Still install the proxy — ops will fail loudly when used.
        logger.warning("sidecar_bridge: health check failed (%s); installing anyway", exc)

    from .workspace import proxy_local_workspace_methods
    from .tools_proxy import try_install_file_router_proxy, try_install_tool_proxies

    proxy_local_workspace_methods(client)
    try_install_tool_proxies(client)
    try_install_file_router_proxy(client)
    _INSTALLED = True
    return True

"""Agent-server bridge: route LocalWorkspace ops through the Local Tools Sidecar.

Importing this package (via agent-server ``--import-modules sidecar_bridge``)
installs an opt-in monkeypatch when ``NOVAAGENT_LOCAL_TOOLS_URL`` and
``NOVAAGENT_LOCAL_TOOLS_TOKEN`` are set (or ``NOVAAGENT_LOCAL_TOOLS_SIDECAR=1``
with a token — URL defaults to ``http://127.0.0.1:18765``).

``client`` / ``path_map`` are stdlib-only and importable without openhands-sdk.
``SidecarWorkspace`` and the monkeypatch require openhands-sdk (agent-server).

See ``docs/LOCAL_TOOLS_SIDECAR.md`` Phase 2.
"""

from __future__ import annotations

from .client import SidecarClient, SidecarClientError, client_from_env

try:
    from .install import is_bridge_enabled, try_install_bridge
    from .workspace import SidecarWorkspace

    # Side effect: enable LocalWorkspace → sidecar proxy when env is configured.
    _INSTALLED = try_install_bridge()
except ImportError:  # pragma: no cover - unit tests without SDK
    def is_bridge_enabled(env=None):  # type: ignore[misc]
        return client_from_env(env) is not None

    def try_install_bridge(env=None):  # type: ignore[misc]
        return False

    SidecarWorkspace = None  # type: ignore[misc, assignment]
    _INSTALLED = False

__all__ = [
    "SidecarClient",
    "SidecarClientError",
    "SidecarWorkspace",
    "client_from_env",
    "is_bridge_enabled",
    "try_install_bridge",
]

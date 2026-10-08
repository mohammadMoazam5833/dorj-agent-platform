"""Outbound reverse workspace connection for the Dorj Agent CLI.

The CLI is the customer side of the Local Tools Sidecar protocol: it opens an
outbound WebSocket (like the desktop app), receives protocol-v1 requests from
the company gateway, and executes file/terminal operations in the CLI's cwd.
No inbound port is opened and only this directory is allowlisted.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import ssl
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import websockets

from .workspace import PROTOCOL_VERSION, handle_request


class WorkspaceConnectionError(RuntimeError):
    pass


class WorkspaceBridge:
    """Async context manager for a CLI-hosted reverse workspace session."""

    def __init__(self, server: str, token: str, root: Path):
        self.server = server.rstrip("/")
        self.token = token
        self.root = root.expanduser().resolve(strict=False)
        self._connection: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.connected = False
        self.hello: dict[str, Any] | None = None
        self.close_code: int | None = None
        self.close_reason = ""

    @staticmethod
    def _resolve_target(server: str) -> tuple[str, str | None, int]:
        """Return the connection target, TLS hostname, and port for NOVA_RESOLVE."""
        url = httpx.URL(server)
        hostname = url.host
        if not hostname:
            raise WorkspaceConnectionError("NOVA_SERVER has no hostname")
        target_host = hostname
        server_hostname = None
        for pair in filter(None, os.environ.get("NOVA_RESOLVE", "").split(",")):
            name, _, ip = pair.partition("=")
            if name.strip() == hostname and ip.strip():
                target_host = ip.strip()
                server_hostname = hostname
                break
        return target_host, server_hostname, url.port or (443 if url.scheme == "https" else 80)

    def _ssl_context(self) -> ssl.SSLContext | None:
        if not self.server.startswith("https://"):
            return None
        context = ssl.create_default_context()
        ca_bundle = os.environ.get("NOVA_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
        if ca_bundle:
            context.load_verify_locations(ca_bundle)
        return context

    def _endpoint(self) -> tuple[str, str | None, str, int]:
        """Return a public-host WebSocket URL and its optional resolved target."""
        target_host, server_hostname, port = self._resolve_target(self.server)
        url = httpx.URL(self.server)
        scheme = "wss" if url.scheme == "https" else "ws"
        default_port = 443 if url.scheme == "https" else 80
        host_part = f"[{url.host}]" if ":" in (url.host or "") and not (url.host or "").startswith("[") else url.host
        base = f"{scheme}://{host_part}" + (f":{port}" if port != default_port else "")
        query = urlencode({"token": self.token})
        return f"{base}/customer-workspace?{query}", server_hostname, target_host, port

    async def __aenter__(self) -> "WorkspaceBridge":
        endpoint, server_hostname, target_host, port = self._endpoint()
        connect_kwargs: dict[str, Any] = {
            "open_timeout": 10,
            "ping_interval": 20,
            "ping_timeout": 20,
            "max_size": 8 * 1024 * 1024,
            "proxy": None,
        }
        ssl_context = self._ssl_context()
        if ssl_context is not None:
            connect_kwargs["ssl"] = ssl_context
            if server_hostname:
                connect_kwargs["server_hostname"] = server_hostname
        try:
            if server_hostname:
                # websockets cannot replace its Host header safely; connect a pre-resolved socket
                # while using the public hostname in both SNI and Host.
                connect_kwargs["sock"] = await asyncio.to_thread(
                    socket.create_connection, (target_host, port), 10
                )
            self._connection = await websockets.connect(endpoint, **connect_kwargs)
        except Exception as exc:
            detail = str(exc)
            if "certificate verify failed" in detail.lower():
                detail += " (set NOVA_CA_BUNDLE to the organisation root CA)"
            raise WorkspaceConnectionError(detail) from exc
        self._reader = asyncio.create_task(self._read_loop())
        try:
            # The gateway treats a customer hello as connection metadata, not a round trip. Send
            # the sidecar-shaped result so it records the CLI's allowlist before the first request.
            self.hello = handle_request(
                self.root,
                {"v": PROTOCOL_VERSION, "id": f"cli-hello-{uuid.uuid4().hex}", "type": "hello", "params": {}},
            )["result"]
            await self._connection.send(
                json.dumps(
                    {
                        "v": PROTOCOL_VERSION,
                        "id": f"cli-hello-{uuid.uuid4().hex}",
                        "type": "result",
                        "ok": True,
                        "result": self.hello,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        except Exception:
            await self.close()
            raise
        self.connected = True
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def _read_loop(self) -> None:
        try:
            assert self._connection is not None
            async for raw in self._connection:
                try:
                    message = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if not isinstance(message, dict):
                    continue
                request_id = message.get("id")
                if isinstance(request_id, str) and request_id in self._pending:
                    future = self._pending.pop(request_id)
                    if not future.done():
                        future.set_result(message)
                    continue
                if message.get("type") in {"hello", "list_dir", "read_file", "write_file", "exec"}:
                    response = await asyncio.to_thread(handle_request, self.root, message)
                    await self._connection.send(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
        except websockets.ConnectionClosed as exc:
            self.close_code = exc.rcvd.code if exc.rcvd else exc.sent.code
            self.close_reason = (exc.rcvd.reason if exc.rcvd else exc.sent.reason) or ""
        except Exception as exc:  # noqa: BLE001
            self.close_reason = str(exc)
        finally:
            self.connected = False
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(WorkspaceConnectionError("workspace connection closed"))
            self._pending.clear()

    async def _request(self, request_type: str, params: dict[str, Any] | None = None, timeout_s: float = 60) -> dict[str, Any]:
        if self._connection is None or self._reader is None or self._reader.done():
            detail = self.close_reason or "workspace connection closed"
            if self.close_code == 4001:
                detail = "session replaced on the gateway"
            raise WorkspaceConnectionError(detail)
        request_id = f"cli-{uuid.uuid4().hex}"
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._connection.send(
            json.dumps(
                {
                    "v": PROTOCOL_VERSION,
                    "id": request_id,
                    "type": request_type,
                    "params": params or {},
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        try:
            response = await asyncio.wait_for(future, timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise WorkspaceConnectionError("workspace connection request timed out") from exc
        if not response.get("ok"):
            error = response.get("error") or {}
            raise WorkspaceConnectionError(str(error.get("message") or "workspace request failed"))
        return response.get("result") or {}

    def roots(self) -> list[str]:
        if not self.hello:
            return []
        roots = self.hello.get("roots")
        return [str(root) for root in roots] if isinstance(roots, list) else []

    async def wait_closed(self) -> None:
        """Wait until the gateway closes the workspace connection."""
        reader = self._reader
        if reader is None or reader.done():
            return
        try:
            await reader
        except asyncio.CancelledError:
            # close() cancels the reader as part of normal cleanup. Preserve a real
            # cancellation of the task that is waiting for the bridge to stop.
            if not reader.cancelled():
                raise

    async def close(self) -> None:
        if self._reader and not self._reader.done():
            self._reader.cancel()
        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception:  # noqa: BLE001
                pass
        self._connection = None
        self.connected = False

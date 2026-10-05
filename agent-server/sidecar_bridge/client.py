"""Minimal HTTP client for Local Tools Sidecar protocol v1 (stdlib only)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin


PROTOCOL_VERSION = 1
DEFAULT_TIMEOUT_S = 60.0


class SidecarClientError(RuntimeError):
    def __init__(self, code: str, message: str, *, response: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.response = response


@dataclass(frozen=True)
class SidecarClient:
    """Talk to ``POST {base_url}/v1`` with Bearer token auth."""

    base_url: str
    token: str
    timeout_s: float = DEFAULT_TIMEOUT_S

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", self.base_url.rstrip("/") + "/")

    def health(self) -> dict[str, Any]:
        url = urljoin(self.base_url, "health")
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def request(self, type_: str, params: dict[str, Any] | None = None, *, id_: str = "1") -> Any:
        body = {
            "v": PROTOCOL_VERSION,
            "id": id_,
            "type": type_,
            "params": params or {},
        }
        data = json.dumps(body).encode("utf-8")
        url = urljoin(self.base_url, "v1")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                parsed = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                parsed = json.loads(exc.read().decode("utf-8"))
            except Exception as decode_err:  # noqa: BLE001
                raise SidecarClientError(
                    "exec_failed",
                    f"HTTP {exc.code}: {exc.reason}",
                ) from decode_err
            if isinstance(parsed, dict) and parsed.get("ok") is False:
                err = parsed.get("error") or {}
                raise SidecarClientError(
                    str(err.get("code") or "exec_failed"),
                    str(err.get("message") or "sidecar error"),
                    response=parsed,
                ) from exc
            raise SidecarClientError("exec_failed", f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise SidecarClientError("exec_failed", f"sidecar unreachable: {exc}") from exc

        if not isinstance(parsed, dict) or not parsed.get("ok"):
            err = (parsed or {}).get("error") if isinstance(parsed, dict) else None
            if isinstance(err, dict):
                raise SidecarClientError(
                    str(err.get("code") or "exec_failed"),
                    str(err.get("message") or "sidecar error"),
                    response=parsed if isinstance(parsed, dict) else None,
                )
            raise SidecarClientError("exec_failed", "unexpected sidecar response")

        return parsed.get("result")

    def hello(self) -> dict[str, Any]:
        return self.request("hello")

    def list_dir(self, path: str) -> dict[str, Any]:
        return self.request("list_dir", {"path": path})

    def read_file(self, path: str) -> dict[str, Any]:
        return self.request("read_file", {"path": path})

    def write_file(
        self,
        path: str,
        content: str,
        *,
        create_parents: bool = True,
    ) -> dict[str, Any]:
        return self.request(
            "write_file",
            {
                "path": path,
                "content": content,
                "create_parents": create_parents,
            },
        )

    def exec(
        self,
        argv: list[str],
        *,
        cwd: str | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"argv": argv}
        if cwd is not None:
            params["cwd"] = cwd
        if timeout_ms is not None:
            params["timeout_ms"] = timeout_ms
        return self.request("exec", params)


def client_from_env(env: dict[str, str] | None = None) -> SidecarClient | None:
    """Build a client from NovaAgent env vars, or ``None`` if not configured."""
    import os

    e = env if env is not None else os.environ
    token = (
        (e.get("NOVAAGENT_CUSTOMER_WORKSPACE_TOKEN") or "").strip()
        or (e.get("NOVAAGENT_LOCAL_TOOLS_TOKEN") or "").strip()
    )
    url = (e.get("NOVAAGENT_LOCAL_TOOLS_URL") or "").strip()
    if not url and e.get("NOVAAGENT_CUSTOMER_WORKSPACE_GATEWAY") == "1":
        host = (e.get("NOVAAGENT_CUSTOMER_WORKSPACE_HOST") or "127.0.0.1").strip()
        port = (e.get("NOVAAGENT_CUSTOMER_WORKSPACE_PORT") or "18766").strip()
        url = f"http://{host}:{port}"
    elif not url and e.get("NOVAAGENT_LOCAL_TOOLS_SIDECAR") == "1":
        host = (e.get("NOVAAGENT_LOCAL_TOOLS_HOST") or "127.0.0.1").strip()
        port = (e.get("NOVAAGENT_LOCAL_TOOLS_PORT") or "18765").strip()
        url = f"http://{host}:{port}"
    if not url or not token:
        return None
    return SidecarClient(base_url=url, token=token)

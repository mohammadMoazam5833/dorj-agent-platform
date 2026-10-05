"""``SidecarWorkspace`` — BaseWorkspace/LocalWorkspace ops via Local Tools Sidecar."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from openhands.sdk.git.models import GitChange, GitDiff
from openhands.sdk.workspace.local import LocalWorkspace
from openhands.sdk.workspace.models import CommandResult, FileOperationResult

from .client import SidecarClient, SidecarClientError, client_from_env
from .path_map import (
    assert_under_roots,
    get_client_roots,
    join_under_working_dir,
    map_to_client_roots,
)

logger = logging.getLogger(__name__)


def _shell_argv(command: str) -> list[str]:
    """Sidecar exec is argv-only (no shell string); wrap a bash/cmd invocation."""
    if os.name == "nt":
        return ["cmd.exe", "/c", command]
    return ["/bin/bash", "-lc", command]


class SidecarWorkspace(LocalWorkspace):
    """LocalWorkspace subclass that proxies command/file ops to the sidecar.

    Subclassing ``LocalWorkspace`` keeps ``isinstance(..., LocalWorkspace)`` true
    so agent-server conversation start / workspace HTTP checks still pass when
    this kind is used explicitly.

    Sidecar URL/token are **not** pydantic fields (avoid persisting secrets in
    conversation state). Re-hydrate from env or pass ``client=`` / URL+token
    at construction time.

    Note: stock ``file_editor`` / ``terminal`` tools still open local ``Path``s
    and do **not** call these methods — see Phase 2 docs / SDK follow-up.
    """

    def __init__(
        self,
        *,
        working_dir: str | Path,
        sidecar_url: str | None = None,
        sidecar_token: str | None = None,
        sidecar_roots: list[str] | None = None,
        client: SidecarClient | None = None,
        **kwargs: Any,
    ):
        super().__init__(working_dir=working_dir, **kwargs)
        env_client = client_from_env()
        url = sidecar_url or (
            env_client.base_url.rstrip("/") if env_client else None
        )
        token = sidecar_token or (env_client.token if env_client else None)
        if client is not None:
            resolved = client
        elif url and token:
            resolved = SidecarClient(base_url=url, token=token)
        else:
            raise ValueError(
                "SidecarWorkspace requires sidecar_url+sidecar_token, a client=, "
                "or NOVAAGENT_LOCAL_TOOLS_URL + NOVAAGENT_LOCAL_TOOLS_TOKEN"
            )
        # Not model fields — keep tokens out of conversation serialization.
        object.__setattr__(self, "_client", resolved)
        object.__setattr__(self, "_sidecar_roots", sidecar_roots)

    def _map(self, path: str | Path) -> str:
        absolute = join_under_working_dir(self.working_dir, path)
        roots = (
            list(self._sidecar_roots)
            if self._sidecar_roots
            else get_client_roots(self._client)
        )
        if not roots:
            return absolute
        return map_to_client_roots(absolute, str(self.working_dir), roots)

    def list_dir(self, path: str | Path | None = None) -> dict[str, Any]:
        target = self._map(path if path is not None else self.working_dir)
        return self._client.list_dir(target)

    def read_text(self, path: str | Path) -> str:
        result = self._client.read_file(self._map(path))
        content = result.get("content")
        if not isinstance(content, str):
            raise SidecarClientError("exec_failed", "read_file missing content")
        return content

    def write_text(self, path: str | Path, content: str, *, create_parents: bool = True) -> int:
        result = self._client.write_file(
            self._map(path),
            content,
            create_parents=create_parents,
        )
        written = result.get("bytes_written")
        return int(written) if isinstance(written, int) else len(content.encode("utf-8"))

    def execute_command(
        self,
        command: str,
        cwd: str | Path | None = None,
        timeout: float = 30.0,
    ) -> CommandResult:
        exec_cwd = self._map(cwd if cwd is not None else self.working_dir)
        timeout_ms = max(1, int(timeout * 1000))
        try:
            result = self._client.exec(
                _shell_argv(command),
                cwd=exec_cwd,
                timeout_ms=timeout_ms,
            )
        except SidecarClientError as exc:
            logger.error("sidecar execute_command failed: %s", exc)
            return CommandResult(
                command=command,
                exit_code=1,
                stdout="",
                stderr=str(exc),
                timeout_occurred=exc.code == "timeout",
            )
        return CommandResult(
            command=command,
            exit_code=int(result.get("exit_code") or 0),
            stdout=str(result.get("stdout") or ""),
            stderr=str(result.get("stderr") or ""),
            timeout_occurred=bool(result.get("timed_out")),
        )

    def file_upload(
        self,
        source_path: str | Path,
        destination_path: str | Path,
    ) -> FileOperationResult:
        source = Path(source_path)
        dest = self._map(destination_path)
        try:
            content = source.read_text(encoding="utf-8")
            result = self._client.write_file(dest, content, create_parents=True)
            return FileOperationResult(
                success=True,
                source_path=str(source),
                destination_path=dest,
                file_size=int(result.get("bytes_written") or source.stat().st_size),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("sidecar file_upload failed: %s", exc)
            return FileOperationResult(
                success=False,
                source_path=str(source),
                destination_path=str(destination_path),
                error=str(exc),
            )

    def file_download(
        self,
        source_path: str | Path,
        destination_path: str | Path,
    ) -> FileOperationResult:
        src = self._map(source_path)
        destination = Path(destination_path)
        try:
            result = self._client.read_file(src)
            content = result.get("content")
            if not isinstance(content, str):
                raise SidecarClientError("exec_failed", "read_file missing content")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
            return FileOperationResult(
                success=True,
                source_path=src,
                destination_path=str(destination),
                file_size=destination.stat().st_size,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("sidecar file_download failed: %s", exc)
            return FileOperationResult(
                success=False,
                source_path=str(source_path),
                destination_path=str(destination),
                error=str(exc),
            )

    def git_changes(self, path: str | Path) -> list[GitChange]:
        """Best-effort: materialize via sidecar read is not enough for git.

        Fall back to local git helpers when the working_dir is co-located
        (Hybrid laptop demo). Remote-only FS without a local checkout needs
        an SDK change.
        """
        return super().git_changes(path)

    def git_diff(self, path: str | Path) -> GitDiff:
        return super().git_diff(path)


def proxy_local_workspace_methods(client: SidecarClient) -> None:
    """Monkeypatch ``LocalWorkspace`` command/file methods to use ``client``.

    Used when conversations still send ``kind: LocalWorkspace`` (Canvas default)
    but env opts into the sidecar bridge.
    """

    def execute_command(self, command, cwd=None, timeout=30.0):  # noqa: ANN001
        ws = SidecarWorkspace(
            working_dir=self.working_dir,
            client=client,
        )
        return ws.execute_command(command, cwd=cwd, timeout=timeout)

    def file_upload(self, source_path, destination_path):  # noqa: ANN001
        ws = SidecarWorkspace(working_dir=self.working_dir, client=client)
        return ws.file_upload(source_path, destination_path)

    def file_download(self, source_path, destination_path):  # noqa: ANN001
        ws = SidecarWorkspace(working_dir=self.working_dir, client=client)
        return ws.file_download(source_path, destination_path)

    def list_dir(self, path=None):  # noqa: ANN001
        ws = SidecarWorkspace(working_dir=self.working_dir, client=client)
        return ws.list_dir(path)

    def read_text(self, path):  # noqa: ANN001
        ws = SidecarWorkspace(working_dir=self.working_dir, client=client)
        return ws.read_text(path)

    def write_text(self, path, content, create_parents=True):  # noqa: ANN001
        ws = SidecarWorkspace(working_dir=self.working_dir, client=client)
        return ws.write_text(path, content, create_parents=create_parents)

    LocalWorkspace.execute_command = execute_command  # type: ignore[method-assign]
    LocalWorkspace.file_upload = file_upload  # type: ignore[method-assign]
    LocalWorkspace.file_download = file_download  # type: ignore[method-assign]
    LocalWorkspace.list_dir = list_dir  # type: ignore[method-assign]
    LocalWorkspace.read_text = read_text  # type: ignore[method-assign]
    LocalWorkspace.write_text = write_text  # type: ignore[method-assign]
    logger.info(
        "Installed LocalWorkspace → sidecar proxy (%s)",
        client.base_url.rstrip("/"),
    )

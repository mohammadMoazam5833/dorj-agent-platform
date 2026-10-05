"""Monkeypatch stock file_editor / terminal tools to use the sidecar protocol.

Stock SDK tools open local Path / tmux on the agent-server host. For reverse
hybrid (brain on company, FS/shell on the customer laptop) those calls must
go through SidecarClient instead.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

from .client import SidecarClient, SidecarClientError
from .workspace import _shell_argv

logger = logging.getLogger(__name__)

_TOOLS_INSTALLED = False
_FILE_ROUTER_INSTALLED = False


def _is_customer_absolute(path: str) -> bool:
    value = str(path).strip()
    if not value:
        return False
    if value.startswith("/"):
        return True
    if value.startswith("\\\\"):
        return True
    if len(value) >= 3 and value[1] == ":" and value[0].isalpha():
        return True
    return False


def sidecar_path_kind(client: SidecarClient, path: str) -> str:
    """Return ``dir``, ``file``, or ``missing`` using protocol v1 only."""
    try:
        client.list_dir(path)
        return "dir"
    except SidecarClientError as exc:
        if exc.code == "not_found":
            return "missing"
        if exc.code == "invalid_request":
            return "file"
        if exc.code == "path_denied":
            return "missing"
        raise


def try_install_tool_proxies(client: SidecarClient) -> bool:
    """Replace FileEditorExecutor + TerminalExecutor with sidecar-backed impls."""
    global _TOOLS_INSTALLED
    if _TOOLS_INSTALLED:
        return True

    installed_any = False
    installed_any = _install_file_editor(client) or installed_any
    installed_any = _install_terminal(client) or installed_any
    _TOOLS_INSTALLED = installed_any
    return installed_any


def _install_file_editor(client: SidecarClient) -> bool:
    try:
        from openhands.tools.file_editor.definition import FileEditorObservation
        from openhands.tools.file_editor.editor import FileEditor
        from openhands.tools.file_editor.exceptions import (
            EditorToolParameterInvalidError,
            ToolError,
        )
        from openhands.tools.file_editor.impl import FileEditorExecutor
    except ImportError as exc:  # pragma: no cover
        logger.debug("sidecar_bridge: file_editor not available (%s)", exc)
        return False

    class SidecarBackedFileEditor(FileEditor):
        def __init__(self, workspace_root: str | None = None):
            from openhands.tools.file_editor.utils.encoding import EncodingManager
            from openhands.tools.file_editor.utils.history import FileHistoryManager

            self._history_manager = FileHistoryManager(max_history_per_file=10)
            self._max_file_size = self.MAX_FILE_SIZE_MB * 1024 * 1024
            self._encoding_manager = EncodingManager()
            self._cwd = str(workspace_root) if workspace_root else "/"
            self._client = client

        def _map(self, path):
            from .path_map import get_client_roots, map_to_client_roots

            try:
                roots = get_client_roots(self._client)
                if roots:
                    return map_to_client_roots(str(path), self._cwd or str(path), roots)
            except Exception:
                pass
            return str(path)

        def validate_path(self, command, path):  # noqa: ANN001
            path_str = self._map(path)
            if not _is_customer_absolute(path_str):
                suggestion = "The path should be an absolute path."
                if self._cwd:
                    suggestion += f" Maybe you meant {self._cwd.rstrip('/')}/{path_str}?"
                raise EditorToolParameterInvalidError("path", path_str, suggestion)

            kind = sidecar_path_kind(self._client, path_str)
            if command == "create" and kind != "missing":
                raise EditorToolParameterInvalidError(
                    "path",
                    path_str,
                    f"File already exists at: {path}. Cannot overwrite files using "
                    "command `create`.",
                )
            if command != "create" and kind == "missing":
                raise EditorToolParameterInvalidError(
                    "path",
                    path_str,
                    f"The path {path} does not exist. Please provide a valid path.",
                )
            if command != "view" and kind == "dir":
                raise EditorToolParameterInvalidError(
                    "path",
                    path_str,
                    f"The path {path} is a directory and only the `view` command can "
                    "be used on directories.",
                )

        def validate_file(self, path):  # noqa: ANN001
            return

        def read_file(  # type: ignore[override]
            self,
            path: Path,
            start_line: int | None = None,
            end_line: int | None = None,
            encoding: str = "utf-8",
        ) -> str:
            try:
                result = self._client.read_file(self._map(path))
            except SidecarClientError as exc:
                raise ToolError(f"Ran into {exc} while trying to read {path}") from None
            content = result.get("content")
            if not isinstance(content, str):
                raise ToolError(f"read_file missing content for {path}")
            if start_line is not None and end_line is not None:
                lines = content.splitlines(True)
                return "".join(lines[start_line - 1 : end_line])
            return content

        def write_file(  # type: ignore[override]
            self,
            path: Path,
            file_text: str,
            encoding: str = "utf-8",
        ) -> None:
            try:
                self._client.write_file(self._map(path), file_text, create_parents=True)
            except SidecarClientError as exc:
                raise ToolError(
                    f"Ran into {exc} while trying to write to {path}"
                ) from None

        def view(self, path: Path, view_range: list[int] | None = None):  # noqa: ANN001
            mapped = self._map(path)
            kind = sidecar_path_kind(self._client, mapped)
            if kind == "dir":
                listing = self._client.list_dir(mapped)
                names = [
                    e.get("name", "")
                    for e in listing.get("entries") or []
                    if not str(e.get("name", "")).startswith(".")
                ]
                formatted = "\n".join(f"- {name}" for name in names)
                text = (
                    f"Here's the files and directories in {path}, "
                    f"excluding hidden items:\n{formatted}"
                )
                return FileEditorObservation.from_text(
                    text=text,
                    command="view",
                    path=str(path),
                    prev_exist=True,
                )
            content = self.read_file(
                mapped,
                start_line=view_range[0] if view_range else None,
                end_line=(
                    None
                    if not view_range or view_range[1] < 0
                    else view_range[1]
                ),
            )
            return FileEditorObservation.from_text(
                text=content,
                command="view",
                path=str(path),
                prev_exist=True,
            )

    _orig_init = FileEditorExecutor.__init__

    def _patched_init(
        self,
        workspace_root: str | None = None,
        allowed_edits_files: list[str] | None = None,
    ) -> None:
        _orig_init(self, workspace_root=workspace_root, allowed_edits_files=allowed_edits_files)
        self.editor = SidecarBackedFileEditor(workspace_root=workspace_root)

    FileEditorExecutor.__init__ = _patched_init  # type: ignore[method-assign]
    logger.info("Installed FileEditorExecutor → sidecar proxy")
    return True


def _install_terminal(client: SidecarClient) -> bool:
    try:
        from openhands.tools.terminal.definition import (
            TerminalObservation,
            looks_like_python_literal_argument,
            _LITERAL_ARG_HINT_TEMPLATE,
        )
        from openhands.tools.terminal.impl import TerminalExecutor
        from openhands.tools.terminal.metadata import CmdOutputMetadata
    except ImportError as exc:  # pragma: no cover
        logger.debug("sidecar_bridge: terminal tool not available (%s)", exc)
        return False

    def _sidecar_init(
        self,
        working_dir: str,
        username: str | None = None,
        no_change_timeout_seconds: int | None = None,
        terminal_type: str | None = None,
        shell_path: str | None = None,
        env: Any = None,
        full_output_save_dir: str | None = None,
        max_panes: int = 1,
    ) -> None:
        self.shell_path = shell_path
        self._working_dir = working_dir
        self._username = username
        self._no_change_timeout_seconds = no_change_timeout_seconds
        self._terminal_type = "sidecar"
        self._env = env or {}
        self._max_panes = max_panes
        self.full_output_save_dir = full_output_save_dir
        self._pool = None
        self._session = None
        self._sessions: dict[int, Any] = {}
        self._sessions_lock = threading.Lock()
        self._pool_recovery_lock = threading.Lock()
        self._client = client
        logger.info("TerminalExecutor using sidecar at %s (cwd=%s)", client.base_url, working_dir)

    def _sidecar_call(self, action, conversation=None):  # noqa: ANN001
        if action.reset and action.is_input:
            raise ValueError("Cannot use reset=True with is_input=True")
        if not action.is_input:
            literal_kind = looks_like_python_literal_argument(action.command)
            if literal_kind is not None:
                head = action.command.lstrip()[:60]
                return TerminalObservation.from_text(
                    _LITERAL_ARG_HINT_TEMPLATE.format(
                        literal_kind=literal_kind,
                        head=head,
                    ),
                    is_error=True,
                    command=action.command,
                    exit_code=None,
                )

        timeout_s = action.timeout if action.timeout else 30.0
        timeout_ms = max(1, int(float(timeout_s) * 1000))
        cwd = self._working_dir
        try:
            from .path_map import get_client_roots, map_to_client_roots

            _roots = get_client_roots(self._client)
            if _roots:
                cwd = map_to_client_roots(str(cwd), str(self._working_dir), _roots)
        except Exception:
            cwd = self._working_dir
        try:
            result = self._client.exec(
                _shell_argv(action.command or "true"),
                cwd=cwd,
                timeout_ms=timeout_ms,
            )
        except SidecarClientError as exc:
            return TerminalObservation.from_text(
                text=str(exc),
                is_error=True,
                command=action.command,
                exit_code=1,
                timeout=exc.code == "timeout",
                metadata=CmdOutputMetadata(exit_code=1, working_dir=cwd),
            )

        stdout = str(result.get("stdout") or "")
        stderr = str(result.get("stderr") or "")
        output = stdout if not stderr else f"{stdout}{stderr}"
        exit_code = int(result.get("exit_code") or 0)
        timed_out = bool(result.get("timed_out"))
        return TerminalObservation.from_text(
            text=output,
            command=action.command,
            exit_code=exit_code,
            timeout=timed_out,
            metadata=CmdOutputMetadata(exit_code=exit_code, working_dir=cwd),
        )

    def _sidecar_interrupt(self) -> None:
        return None

    def _sidecar_close(self) -> None:
        return None

    TerminalExecutor.__init__ = _sidecar_init  # type: ignore[method-assign]
    TerminalExecutor.__call__ = _sidecar_call  # type: ignore[method-assign]
    TerminalExecutor.interrupt = _sidecar_interrupt  # type: ignore[method-assign]
    TerminalExecutor.close = _sidecar_close  # type: ignore[method-assign]
    logger.info("Installed TerminalExecutor → sidecar proxy")
    return True


def try_install_file_router_proxy(client: SidecarClient) -> bool:
    """Best-effort: Folder Browser `/api/file/home` + `/search_subdirs` → sidecar."""
    global _FILE_ROUTER_INSTALLED
    if _FILE_ROUTER_INSTALLED:
        return True

    try:
        from fastapi import HTTPException, status
        from fastapi.routing import APIRoute
        from openhands.agent_server.file_router import (
            FileBrowserEntry,
            HomeResponse,
            SubdirectoryEntry,
            SubdirectoryPage,
            file_router,
        )
    except ImportError as exc:  # pragma: no cover
        logger.debug("sidecar_bridge: file_router not available (%s)", exc)
        return False

    def _customer_home() -> str:
        hello = client.hello()
        roots = hello.get("roots") or []
        if roots:
            return str(roots[0])
        return os.path.expanduser("~")

    def _join(parent: str, name: str) -> str:
        if parent.endswith(("/", "\\")):
            return f"{parent}{name}"
        sep = "\\" if "\\" in parent and "/" not in parent else "/"
        return f"{parent}{sep}{name}"

    async def sidecar_get_home(
        include_hidden: bool = False,
    ) -> HomeResponse:
        home = _customer_home()
        try:
            listing = client.list_dir(home)
        except SidecarClientError as exc:
            logger.warning("sidecar file/home list_dir failed: %s", exc)
            return HomeResponse(
                home=home,
                favorites=[],
                locations=[FileBrowserEntry(label=home, path=home)],
            )
        favorites: list[FileBrowserEntry] = []
        for entry in listing.get("entries") or []:
            name = str(entry.get("name") or "")
            if not name:
                continue
            if not include_hidden and name.startswith("."):
                continue
            if entry.get("type") != "dir":
                continue
            favorites.append(FileBrowserEntry(label=name, path=_join(home, name)))
        favorites.sort(key=lambda e: e.label.lower())
        return HomeResponse(
            home=home,
            favorites=favorites[:50],
            locations=[FileBrowserEntry(label=home, path=home)],
        )

    async def sidecar_search_subdirs(
        path: str,
        page_id: str | None = None,
        limit: int = 100,
        include_hidden: bool = False,
    ) -> SubdirectoryPage:
        if not _is_customer_absolute(path):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Path must be absolute",
            )
        try:
            listing = client.list_dir(path)
        except SidecarClientError as exc:
            if exc.code == "path_denied":
                # Above/below the customer's allowlisted roots: the folder
                # browser must dead-end gracefully (empty page) instead of
                # surfacing a 403 - the sidebar locations stay inside roots.
                return SubdirectoryPage(items=[], next_page_id=None)
            if exc.code == "not_found":
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Directory not found",
                ) from exc
            if exc.code == "invalid_request":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Path is not a directory",
                ) from exc
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
            ) from exc

        entries: list[SubdirectoryEntry] = []
        for entry in listing.get("entries") or []:
            name = str(entry.get("name") or "")
            if not name:
                continue
            if not include_hidden and name.startswith("."):
                continue
            if entry.get("type") != "dir":
                continue
            entries.append(SubdirectoryEntry(name=name, path=_join(path, name)))
        entries.sort(key=lambda e: e.name.lower())

        start_index = 0
        if page_id:
            for i, entry in enumerate(entries):
                if entry.name.lower() == page_id:
                    start_index = i
                    break
        page = entries[start_index : start_index + limit]
        next_page_id = None
        if start_index + limit < len(entries):
            next_page_id = entries[start_index + limit].name.lower()
        return SubdirectoryPage(items=page, next_page_id=next_page_id)

    replaced = 0
    new_routes = []
    for route in list(file_router.routes):
        path = getattr(route, "path", "")
        methods = set(getattr(route, "methods", None) or [])
        if path in ("/home", "/file/home") and "GET" in methods:
            new_routes.append(
                APIRoute(
                    path=route.path,
                    endpoint=sidecar_get_home,
                    methods=["GET"],
                    response_model=getattr(route, "response_model", None),
                    tags=getattr(route, "tags", None),
                )
            )
            replaced += 1
        elif path in ("/search_subdirs", "/file/search_subdirs") and "GET" in methods:
            new_routes.append(
                APIRoute(
                    path=route.path,
                    endpoint=sidecar_search_subdirs,
                    methods=["GET"],
                    response_model=getattr(route, "response_model", None),
                    tags=getattr(route, "tags", None),
                )
            )
            replaced += 1
        else:
            new_routes.append(route)
    file_router.routes[:] = new_routes
    _FILE_ROUTER_INSTALLED = replaced > 0
    if replaced:
        logger.info("Installed file_router home/search_subdirs → sidecar proxy")
    return _FILE_ROUTER_INSTALLED

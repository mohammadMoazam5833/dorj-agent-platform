"""Customer-side Local Tools Sidecar protocol v1 operations.

The CLI runs these operations in its own process and exposes them over an
outbound WebSocket to nova-control.  This removes the need to bundle Node while
keeping exactly the same wire protocol used by the desktop app's sidecar.
"""

from __future__ import annotations

import base64
import binascii
import getpass
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1
READ_FILE_MAX_BYTES = 2 * 1024 * 1024
BASE64_FILE_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_EXEC_TIMEOUT_MS = 30_000
MAX_CAPTURE_BYTES = READ_FILE_MAX_BYTES


class WorkspaceOperationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _error(code: str, message: str) -> WorkspaceOperationError:
    return WorkspaceOperationError(code, message)


def resolve_under_root(path: Any, root: Path, *, must_exist: bool = False) -> Path:
    if not isinstance(path, str) or not path.strip():
        raise _error("invalid_request", "path is required")
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    root_resolved = root.resolve(strict=False)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise _error("path_denied", "path escapes the CLI workspace root") from exc
    if must_exist and not resolved.exists():
        raise _error("not_found", f"path not found: {resolved}")
    return resolved


def _error_from_exception(exc: BaseException) -> WorkspaceOperationError:
    if isinstance(exc, WorkspaceOperationError):
        return exc
    code = getattr(exc, "code", "")
    if code in {"ENOENT", "FileNotFoundError"} or isinstance(exc, FileNotFoundError):
        return _error("not_found", str(exc))
    if code == "path_denied" or isinstance(exc, PermissionError):
        return _error("path_denied", str(exc))
    return _error("exec_failed", str(exc))


def _tool_output(argv: list[str], timeout_s: float) -> str:
    """Best-effort output from a tool; never fails host-info collection."""
    executable = shutil.which(argv[0])
    if executable is None:
        return ""
    try:
        completed = subprocess.run(
            [executable, *argv[1:]],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (completed.stdout or completed.stderr).strip()


def _first_output_line(argv: list[str], timeout_s: float = 1.0) -> str | None:
    output = _tool_output(argv, timeout_s)
    return output.splitlines()[0].strip() if output else None


def _runtime_versions() -> dict[str, str]:
    """Common runtimes useful to the agent; values are short version strings."""
    versions = {"Python": ".".join(map(str, sys.version_info[:3]))}
    for name, argv in (
        ("Node", ["node", "--version"]),
        ("npm", ["npm", "--version"]),
        ("pnpm", ["pnpm", "--version"]),
        ("yarn", ["yarn", "--version"]),
        ("Bun", ["bun", "--version"]),
        ("Deno", ["deno", "--version"]),
        ("Java", ["java", "-version"]),
        ("Go", ["go", "version"]),
        ("Git", ["git", "--version"]),
        ("Docker", ["docker", "--version"]),
    ):
        line = _first_output_line(argv)
        if line:
            versions[name] = line
    return versions


def _gpu_lines() -> list[str]:
    """GPU identity from nvidia-smi, when available (best effort only)."""
    output = _tool_output(["nvidia-smi", "-L"], 2.0)
    return [line.strip() for line in output.splitlines() if line.strip().startswith("GPU ")]


def _shell_path() -> str:
    if os.name == "nt":
        return os.environ.get("COMSPEC", "")
    return os.environ.get("SHELL") or ""


def _current_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - system info must never break the bridge
        return os.environ.get("USER") or os.environ.get("USERNAME") or ""


def host_system_info() -> dict[str, Any]:
    """Describe the machine that will execute file/terminal operations."""
    return {
        "platform": sys.platform,
        "os": platform.system(),
        "release": platform.release(),
        "arch": platform.machine(),
        "hostname": socket.gethostname(),
        "user": _current_user(),
        "home": str(Path.home()),
        "shell": _shell_path(),
        "runtimes": _runtime_versions(),
        "gpus": _gpu_lines(),
    }


def handle_hello(root: Path) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL_VERSION,
        "capabilities": ["file_base64"],
        "roots": [str(root.resolve(strict=False))],
        "host": "dorj-agent-cli",
        "port": 0,
        "system": host_system_info(),
    }


def handle_list_dir(root: Path, params: dict[str, Any]) -> dict[str, Any]:
    try:
        absolute = resolve_under_root(params.get("path"), root, must_exist=True)
        if not absolute.is_dir():
            raise _error("invalid_request", "path is not a directory")
        entries: list[dict[str, Any]] = []
        for item in sorted(absolute.iterdir(), key=lambda p: p.name):
            entry: dict[str, Any] = {
                "name": item.name,
                "type": "dir" if item.is_dir() else "file" if item.is_file() else "other",
            }
            if item.is_file():
                entry["size"] = item.stat().st_size
            entries.append(entry)
        return {"path": str(absolute), "entries": entries}
    except BaseException as exc:  # noqa: BLE001
        raise _error_from_exception(exc) from exc


def handle_read_file(root: Path, params: dict[str, Any]) -> dict[str, Any]:
    try:
        absolute = resolve_under_root(params.get("path"), root, must_exist=True)
        if not absolute.is_file():
            raise _error("invalid_request", "path is not a file")
        encoding = params.get("encoding", "utf-8")
        if encoding not in {"utf-8", "base64"}:
            raise _error("invalid_request", "params.encoding must be utf-8 or base64")
        max_bytes = BASE64_FILE_MAX_BYTES if encoding == "base64" else READ_FILE_MAX_BYTES
        size = absolute.stat().st_size
        if size > max_bytes:
            raise _error("too_large", f"file exceeds max size ({size} > {max_bytes} bytes)")
        if encoding == "base64":
            return {
                "path": str(absolute),
                "encoding": encoding,
                "content": base64.b64encode(absolute.read_bytes()).decode("ascii"),
            }
        return {
            "path": str(absolute),
            "encoding": encoding,
            "content": absolute.read_text(encoding="utf-8"),
        }
    except UnicodeDecodeError as exc:
        raise _error("exec_failed", f"file is not valid UTF-8: {exc}") from exc
    except BaseException as exc:  # noqa: BLE001
        raise _error_from_exception(exc) from exc


def handle_write_file(root: Path, params: dict[str, Any]) -> dict[str, Any]:
    try:
        absolute = resolve_under_root(params.get("path"), root)
        content = params.get("content")
        if not isinstance(content, str):
            raise _error("invalid_request", "params.content must be a string")
        encoding = params.get("encoding", "utf-8")
        if encoding not in {"utf-8", "base64"}:
            raise _error("invalid_request", "params.encoding must be utf-8 or base64")
        if encoding == "base64":
            try:
                data = base64.b64decode(content, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise _error("invalid_request", "params.content is not valid base64") from exc
            if len(data) > BASE64_FILE_MAX_BYTES:
                raise _error(
                    "too_large",
                    f"file exceeds max base64 size ({len(data)} > {BASE64_FILE_MAX_BYTES} bytes)",
                )
        else:
            data = content.encode("utf-8")
        if params.get("create_parents"):
            absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_bytes(data)
        return {"path": str(absolute), "bytes_written": len(data)}
    except BaseException as exc:  # noqa: BLE001
        raise _error_from_exception(exc) from exc


def _windows_exec_argv(argv: list[str]) -> list[str]:
    first = argv[0].replace("\\", "/").lower()
    if first not in {"/bin/bash", "/bin/sh", "bash", "sh"} and not first.endswith("/bash"):
        return argv
    if len(argv) >= 3 and argv[1] in {"-lc", "-c"}:
        command = " ".join(argv[2:])
    elif len(argv) >= 4 and argv[1] == "-l" and argv[2] == "-c":
        command = " ".join(argv[3:])
    else:
        command = " ".join(argv[1:])
    for variable, suffix in (
        ("ProgramFiles", r"Git\bin\bash.exe"),
        ("ProgramFiles(x86)", r"Git\bin\bash.exe"),
        ("LOCALAPPDATA", r"Programs\Git\bin\bash.exe"),
    ):
        base = os.environ.get(variable)
        if base and Path(base, suffix).exists():
            return [str(Path(base, suffix)), "-lc", command]
    return ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]


def handle_exec(root: Path, params: dict[str, Any]) -> dict[str, Any]:
    try:
        argv = params.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(v, str) and v for v in argv):
            raise _error("invalid_request", "params.argv must be a non-empty string array")
        if os.name == "nt":
            argv = _windows_exec_argv(argv)
        cwd_value = params.get("cwd") or str(root)
        cwd = resolve_under_root(cwd_value, root, must_exist=True)
        if not cwd.is_dir():
            raise _error("invalid_request", "cwd must be a directory")
        raw_timeout = params.get("timeout_ms", DEFAULT_EXEC_TIMEOUT_MS)
        try:
            timeout_ms = max(1, int(float(raw_timeout)))
        except (TypeError, ValueError) as exc:
            raise _error("invalid_request", "params.timeout_ms must be a number") from exc
        # Keep PWD consistent with the child's actual cwd. A stale inherited PWD confuses shells
        # and tools that infer the project root from the environment.
        env = os.environ.copy()
        env["PWD"] = str(cwd)
        popen_kwargs: dict[str, Any] = {
            "cwd": str(cwd),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
            "env": env,
        }
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True
        started = time.monotonic()
        child = subprocess.Popen(argv, **popen_kwargs)  # noqa: S603
        try:
            stdout, stderr = child.communicate(timeout=timeout_ms / 1000)
        except subprocess.TimeoutExpired as exc:
            if os.name != "nt":
                import signal
                os.killpg(child.pid, signal.SIGKILL)
            else:
                child.kill()
            child.communicate()
            return {
                "exit_code": 1,
                "stdout": (exc.stdout or b"").decode("utf-8", "replace")[:MAX_CAPTURE_BYTES],
                "stderr": (exc.stderr or b"").decode("utf-8", "replace")[:MAX_CAPTURE_BYTES],
                "timed_out": True,
            }
        return {
            "exit_code": int(child.returncode or 0),
            "stdout": stdout.decode("utf-8", "replace")[:MAX_CAPTURE_BYTES],
            "stderr": stderr.decode("utf-8", "replace")[:MAX_CAPTURE_BYTES],
            "timed_out": False,
        }
    except BaseException as exc:  # noqa: BLE001
        raise _error_from_exception(exc) from exc


def handle_request(root: Path, message: dict[str, Any]) -> dict[str, Any]:
    """Return a protocol-v1 response envelope for a validated request."""
    request_id = message.get("id")
    if not isinstance(request_id, str) or not request_id:
        return {
            "v": PROTOCOL_VERSION,
            "id": "0",
            "type": "error",
            "ok": False,
            "error": {"code": "invalid_request", "message": "id must be a non-empty string"},
        }
    message_type = message.get("type")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    handler = {
        "hello": lambda: handle_hello(root),
        "list_dir": lambda: handle_list_dir(root, params),
        "read_file": lambda: handle_read_file(root, params),
        "write_file": lambda: handle_write_file(root, params),
        "exec": lambda: handle_exec(root, params),
    }.get(str(message_type))
    if handler is None:
        return {
            "v": PROTOCOL_VERSION,
            "id": request_id,
            "type": "error",
            "ok": False,
            "error": {"code": "invalid_request", "message": f"unknown type: {message_type}"},
        }
    try:
        return {
            "v": PROTOCOL_VERSION,
            "id": request_id,
            "type": "result",
            "ok": True,
            "result": handler(),
        }
    except WorkspaceOperationError as exc:
        return {
            "v": PROTOCOL_VERSION,
            "id": request_id,
            "type": "error",
            "ok": False,
            "error": {"code": exc.code, "message": str(exc)},
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "v": PROTOCOL_VERSION,
            "id": request_id,
            "type": "error",
            "ok": False,
            "error": {"code": "exec_failed", "message": str(exc)},
        }

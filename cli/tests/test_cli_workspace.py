from __future__ import annotations

import base64
import json
from pathlib import Path

from dorj_agent_cli.bridge import WorkspaceBridge
from dorj_agent_cli.workspace import (
    BASE64_FILE_MAX_BYTES,
    READ_FILE_MAX_BYTES,
    handle_request,
)


def request(root: Path, type_: str, params: dict | None = None, id_: str = "req-1"):
    return handle_request(root, {"v": 1, "id": id_, "type": type_, "params": params or {}})


def test_hello_reports_only_cwd(tmp_path: Path):
    result = request(tmp_path, "hello")["result"]
    assert result["protocol"] == 1
    assert result["capabilities"] == ["file_base64"]
    assert result["roots"] == [str(tmp_path)]


def test_write_read_list_and_exec(tmp_path: Path):
    path = str(tmp_path / "nested" / "pong.txt")
    response = request(tmp_path, "write_file", {"path": path, "content": "pong", "create_parents": True})
    assert response["ok"] is True
    assert response["result"]["bytes_written"] == 4

    response = request(tmp_path, "read_file", {"path": path})
    assert response["result"]["content"] == "pong"

    response = request(tmp_path, "list_dir", {"path": str(tmp_path / "nested")})
    assert response["result"]["entries"][0]["name"] == "pong.txt"

    response = request(tmp_path, "exec", {"argv": ["/bin/bash", "-lc", "printf local"], "cwd": str(tmp_path)})
    assert response["result"] == {"exit_code": 0, "stdout": "local", "stderr": "", "timed_out": False}


def test_base64_write_and_read_round_trip(tmp_path: Path):
    data = bytes([0, 1, 2, 253, 254, 255])
    path = str(tmp_path / "binary.bin")
    encoded = base64.b64encode(data).decode("ascii")

    response = request(tmp_path, "write_file", {"path": path, "content": encoded, "encoding": "base64"})
    assert response["ok"] is True
    assert response["result"] == {"path": path, "bytes_written": len(data)}

    response = request(tmp_path, "read_file", {"path": path, "encoding": "base64"})
    assert response["result"] == {"path": path, "encoding": "base64", "content": encoded}


def test_default_encoding_remains_utf8_with_2_mib_limit(tmp_path: Path):
    text_path = tmp_path / "text.txt"
    text_path.write_text("سلام", encoding="utf-8")
    response = request(tmp_path, "read_file", {"path": str(text_path)})
    assert response["result"] == {"path": str(text_path), "encoding": "utf-8", "content": "سلام"}

    large_path = tmp_path / "large.txt"
    large_path.write_bytes(b"x" * (READ_FILE_MAX_BYTES + 1))
    response = request(tmp_path, "read_file", {"path": str(large_path)})
    assert response["ok"] is False
    assert response["error"]["code"] == "too_large"


def test_base64_limit_is_25_mib(tmp_path: Path):
    path = str(tmp_path / "at-limit.bin")
    encoded = base64.b64encode(bytes(BASE64_FILE_MAX_BYTES)).decode("ascii")
    response = request(tmp_path, "write_file", {"path": path, "content": encoded, "encoding": "base64"})
    assert response["ok"] is True
    response = request(tmp_path, "read_file", {"path": path, "encoding": "base64"})
    assert response["ok"] is True
    assert response["result"]["encoding"] == "base64"

    too_large = base64.b64encode(bytes(BASE64_FILE_MAX_BYTES + 1)).decode("ascii")
    response = request(tmp_path, "write_file", {"path": path, "content": too_large, "encoding": "base64"})
    assert response["ok"] is False
    assert response["error"]["code"] == "too_large"


def test_invalid_encoding_is_rejected(tmp_path: Path):
    path = str(tmp_path / "binary.bin")
    (tmp_path / "binary.bin").write_bytes(b"abc")
    response = request(tmp_path, "read_file", {"path": path, "encoding": "hex"})
    assert response["error"]["code"] == "invalid_request"
    response = request(tmp_path, "write_file", {"path": path, "content": "abc", "encoding": "hex"})
    assert response["error"]["code"] == "invalid_request"


def test_exec_sets_pwd_to_requested_cwd(tmp_path: Path):
    response = request(tmp_path, "exec", {"argv": ["/bin/bash", "-lc", "printf $PWD"], "cwd": str(tmp_path)})
    assert response["ok"] is True
    assert response["result"]["stdout"] == str(tmp_path)


def test_path_escape_is_denied(tmp_path: Path):
    response = request(tmp_path, "read_file", {"path": str(tmp_path.parent / "outside.txt")})
    assert response["ok"] is False
    assert response["error"]["code"] == "path_denied"


def test_exec_timeout(tmp_path: Path):
    response = request(tmp_path, "exec", {"argv": ["/bin/bash", "-lc", "sleep 2"], "timeout_ms": 50})
    assert response["result"]["timed_out"] is True


def test_invalid_message_shape(tmp_path: Path):
    response = handle_request(tmp_path, {"v": 1, "id": "x", "type": "unknown"})
    assert response["error"]["code"] == "invalid_request"
    assert json.dumps(response)


def test_resolved_websocket_keeps_public_host(monkeypatch):
    monkeypatch.setenv("NOVA_RESOLVE", "nova.example=127.0.0.1")
    bridge = WorkspaceBridge("https://nova.example", "token", Path("/tmp"))
    endpoint, server_hostname, target_host, port = bridge._endpoint()
    assert endpoint.startswith("wss://nova.example/customer-workspace?token=")
    assert server_hostname == "nova.example"
    assert (target_host, port) == ("127.0.0.1", 443)

from __future__ import annotations

import json
from pathlib import Path

from dorj_agent_cli.bridge import WorkspaceBridge
from dorj_agent_cli.workspace import handle_request


def request(root: Path, type_: str, params: dict | None = None, id_: str = "req-1"):
    return handle_request(root, {"v": 1, "id": id_, "type": type_, "params": params or {}})


def test_hello_reports_only_cwd(tmp_path: Path):
    result = request(tmp_path, "hello")["result"]
    assert result["protocol"] == 1
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

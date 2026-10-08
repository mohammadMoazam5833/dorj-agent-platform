from __future__ import annotations

import asyncio
import json
from pathlib import Path

from dorj_agent_cli import main
from dorj_agent_cli.bridge import WorkspaceConnectionError
from dorj_agent_cli.workspace import handle_request


class FakeBridge:
    def __init__(
        self,
        server: str,
        token: str,
        root: Path,
        *,
        fail: str | None = None,
        close_code: int | None = None,
        close_reason: str = "",
    ):
        self.server = server
        self.token = token
        self.root = root
        self.fail = fail
        self.close_code = close_code
        self.close_reason = close_reason
        self.hello = {"system": {"hostname": "remote-host"}}
        self.connected = False

    async def __aenter__(self):
        if self.fail:
            raise WorkspaceConnectionError(self.fail)
        self.connected = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.connected = False

    async def wait_closed(self):
        return


async def no_sleep(delay: float) -> None:
    return


def test_bridge_parser_defaults_to_cwd_and_supports_detach(tmp_path: Path):
    parser = main.build_parser()

    a = parser.parse_args(["bridge"])
    assert a.root is None
    assert a.detach is False

    a = parser.parse_args(["bridge", "--root", str(tmp_path), "--detach"])
    assert a.root == str(tmp_path)
    assert a.detach is True
    assert main.EXIT_WORKSPACE == 4


def test_bridge_stops_on_4001_without_reconnecting(tmp_path: Path, capsys):
    replaced = FakeBridge("server", "token", tmp_path, close_code=4001)
    created: list[tuple[str, str, Path]] = []

    def factory(server: str, token: str, root: Path):
        created.append((server, token, root))
        return replaced

    code = asyncio.run(
        main._bridge_forever(
            "server",
            "token",
            tmp_path,
            bridge_factory=factory,
            sleep=no_sleep,
        )
    )

    assert code == main.EXIT_OK
    assert created == [("server", "token", tmp_path)]
    out = capsys.readouterr().err
    assert "Workspace connected on remote-host:" in out
    assert "فضای کار روی remote-host:" in out
    assert "session replaced" in out


def test_bridge_reconnects_with_backoff_until_replaced(tmp_path: Path):
    first = FakeBridge("server", "token", tmp_path, fail="socket closed")
    second = FakeBridge("server", "token", tmp_path, close_code=4001)
    bridges = [first, second]
    created: list[tuple[str, str, Path]] = []
    sleeps: list[float] = []

    def factory(server: str, token: str, root: Path):
        created.append((server, token, root))
        return bridges.pop(0)

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    code = asyncio.run(
        main._bridge_forever(
            "server",
            "token",
            tmp_path,
            bridge_factory=factory,
            sleep=sleep,
        )
    )

    assert code == main.EXIT_OK
    assert len(created) == 2
    assert sleeps == [1.0]


def test_bridge_reconnects_once_after_a_normal_disconnect(tmp_path: Path):
    first = FakeBridge("server", "token", tmp_path, close_reason="connection closed")
    second = FakeBridge("server", "token", tmp_path, close_code=4001)
    bridges = [first, second]
    created: list[tuple[str, str, Path]] = []
    sleeps: list[float] = []

    def factory(server: str, token: str, root: Path):
        created.append((server, token, root))
        return bridges.pop(0)

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    code = asyncio.run(
        main._bridge_forever(
            "server",
            "token",
            tmp_path,
            bridge_factory=factory,
            sleep=sleep,
        )
    )

    assert code == main.EXIT_OK
    assert len(created) == 2
    assert sleeps == [1.0]


def test_hello_includes_remote_host_system_and_gpus(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "dorj_agent_cli.workspace._gpu_lines",
        lambda: ["GPU 0: NVIDIA Test GPU", "GPU 1: NVIDIA Second GPU"],
    )
    monkeypatch.setattr(
        "dorj_agent_cli.workspace._runtime_versions",
        lambda: {"Python": "3.9.0", "Node": "20.0.0"},
    )

    result = handle_request(tmp_path, {"v": 1, "id": "hello-1", "type": "hello", "params": {}})

    assert result["ok"] is True
    system = result["result"]["system"]
    assert result["result"]["roots"] == [str(tmp_path)]
    assert system["platform"]
    assert system["hostname"]
    assert system["home"] == str(Path.home())
    assert system["runtimes"] == {"Python": "3.9.0", "Node": "20.0.0"}
    assert system["gpus"] == ["GPU 0: NVIDIA Test GPU", "GPU 1: NVIDIA Second GPU"]


def test_validate_bridge_root_requires_a_directory(tmp_path: Path):
    missing = tmp_path / "missing"
    assert main._validate_bridge_root(None) == Path.cwd().resolve()
    assert main._validate_bridge_root(str(tmp_path)) == tmp_path.resolve()

    try:
        main._validate_bridge_root(str(missing))
    except WorkspaceConnectionError as exc:
        assert str(missing) in str(exc)
    else:
        raise AssertionError("missing root was accepted")


def test_workspace_bridge_lifecycle_with_a_fake_gateway(tmp_path: Path, monkeypatch):
    class FakeWebSocket:
        def __init__(self):
            self.sent: list[dict] = []
            self.closed = False
            self._index = 0

        async def send(self, raw: str):
            self.sent.append(json.loads(raw))

        def __aiter__(self):
            return self

        async def __anext__(self):
            self._index += 1
            if self._index == 1:
                return json.dumps(
                    {
                        "v": 1,
                        "id": "req-1",
                        "type": "write_file",
                        "params": {
                            "path": str(tmp_path / "made.txt"),
                            "content": "remote",
                            "create_parents": True,
                        },
                    }
                )
            raise StopAsyncIteration

        async def close(self):
            self.closed = True

    socket = FakeWebSocket()
    endpoints: list[str] = []

    async def fake_connect(endpoint: str, **kwargs):
        endpoints.append(endpoint)
        return socket

    monkeypatch.setattr("dorj_agent_cli.bridge.websockets.connect", fake_connect)
    monkeypatch.setattr(
        "dorj_agent_cli.workspace.host_system_info",
        lambda: {"hostname": "remote-host", "gpus": ["GPU 0: Test GPU"]},
    )

    async def exercise():
        async with main.WorkspaceBridge("http://gateway.example", "token", tmp_path) as bridge:
            assert bridge.connected
            assert bridge.hello["system"]["hostname"] == "remote-host"
            await bridge.wait_closed()

    asyncio.run(exercise())

    assert endpoints == ["ws://gateway.example/customer-workspace?token=token"]
    assert socket.closed
    assert socket.sent[0]["result"]["roots"] == [str(tmp_path)]
    assert socket.sent[0]["result"]["system"]["hostname"] == "remote-host"
    assert socket.sent[1] == {
        "v": 1,
        "id": "req-1",
        "type": "result",
        "ok": True,
        "result": {
            "path": str(tmp_path / "made.txt"),
            "bytes_written": 6,
        },
    }
    assert (tmp_path / "made.txt").read_text() == "remote"

# Run inside the agent-server image:
# docker run --rm -v $PWD/nova_server_patches:/patches/nova_server_patches:ro -v $PWD/tests/file_bridge_check.py:/t.py:ro --entrypoint python3 <image> /t.py
import asyncio, base64, io, os, sys, tempfile, types
root = tempfile.mkdtemp(prefix="pc-")
CAPS = {"v": []}
GIT_MISSING = {"v": False}

class SidecarClientError(RuntimeError):
    def __init__(self, code, msg): super().__init__(msg); self.code = code

class FakePC:  # the customer's sidecar, but local; root plays "C:\proj"
    def hello(self): return {"roots": [root], "capabilities": CAPS["v"]}
    def request(self, t, p):
        if t == "write_file":
            os.makedirs(os.path.dirname(p["path"]), exist_ok=True)
            data = base64.b64decode(p["content"]) if p.get("encoding") == "base64" else p["content"].encode()
            open(p["path"], "wb").write(data); return {"ok": True}
        if t == "read_file":
            raw = open(p["path"], "rb").read()
            if p.get("encoding") == "base64": return {"content": base64.b64encode(raw).decode()}
            return {"content": raw.decode()}
        raise AssertionError(t)
    def read_file(self, p): return self.request("read_file", {"path": p})
    def exec(self, argv, cwd=None, timeout_ms=None):
        if GIT_MISSING["v"]: raise SidecarClientError("exec_failed", "spawn git ENOENT")
        import subprocess
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
        return {"exit_code": r.returncode, "stdout": r.stdout, "stderr": r.stderr}
sb = types.ModuleType("sidecar_bridge"); sbc = types.ModuleType("sidecar_bridge.client")
sbc.client_from_env = lambda: FakePC(); sbc.SidecarClientError = SidecarClientError; sb.client = sbc
sys.modules["sidecar_bridge"] = sb; sys.modules["sidecar_bridge.client"] = sbc
sys.path.insert(0, "/patches")
import nova_server_patches as nsp
from nova_server_patches import workspace_git as wg
from fastapi import HTTPException, UploadFile
from openhands.agent_server import file_router as fr

def up(path, data): return asyncio.run(fr._upload_file(path, UploadFile(io.BytesIO(data), filename="x")))
def reset(caps): CAPS["v"] = caps; nsp._hello_cache = (0.0, {})

reset([])
up(f"{root}/sub/a.txt", "سلام\n".encode()); assert open(f"{root}/sub/a.txt", encoding="utf-8").read() == "سلام\n"
png = b"\x89PNG\r\n\x1a\n\x00\xff"
try: up(f"{root}/i.png", png); raise SystemExit("binary without capability must fail")
except HTTPException as e: assert e.status_code == 400 and "دستیار دُرج" in e.detail, e.detail
reset(["file_base64"])
up(f"{root}/i.png", png); assert open(f"{root}/i.png", "rb").read() == png
r = asyncio.run(fr._download_file(f"{root}/i.png")); assert r.body == png and "i.png" in r.headers["content-disposition"]
# a path outside the PC roots still uses the server's own code (non-absolute -> original 400)
try: up("relative.txt", b"x"); raise SystemExit("expected 400")
except HTTPException as e: assert e.detail == "Path must be absolute"
# git: init, then "git not installed" is a clear 400 detail
assert wg.init_repo(root)["ok"] and os.path.isdir(f"{root}/.git")
GIT_MISSING["v"] = True
try: wg.git(root, "status"); raise SystemExit("expected GitNotInstalled")
except wg.GitNotInstalled as e: assert str(e).startswith("git_not_installed:")
from openhands.agent_server import git_router as gr
assert any(r.path == "/git/init" for r in gr.git_router.routes)
print("OK: text upload, binary gate, base64 upload/download, non-PC passthrough, git init, git_not_installed")

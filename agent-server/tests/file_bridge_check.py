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
    def list_dir(self, p):
        if not os.path.isdir(p): raise RuntimeError("not a dir")
        return {"entries": os.listdir(p)}
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
GIT_MISSING["v"] = False
# file viewer panel: /api/conversations/{id}/workspace/{file} reads the PC
from openhands.agent_server import workspace_router as wr
class Svc:
    async def get_event_service(self, cid):
        return types.SimpleNamespace(stored=types.SimpleNamespace(workspace=types.SimpleNamespace(working_dir=root)))
d = asyncio.run(wr._resolve_workspace_dir("x", Svc()))
r = wr._serve_path(d, "sub/a.txt"); assert r.body == "سلام\n".encode() and "charset=utf-8" in r.media_type, r.media_type
try: wr._serve_path(d, "../etc/passwd"); raise SystemExit("traversal must fail")
except HTTPException as e: assert e.status_code == 400
try: wr._serve_path(d, "missing.py"); raise SystemExit("expected 404")
except HTTPException as e: assert e.status_code == 404
try: wr._serve_path(d, "C:\\Windows\\win.ini"); raise SystemExit("PC path outside roots must fail")
except HTTPException as e: assert e.status_code == 400, e.detail
# panel bash commands (Files tab listing, Terminal tab) run on the PC
from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.models import ExecuteBashRequest
from pathlib import Path as _P; bsvc = BashEventService(bash_events_dir=_P(tempfile.mkdtemp()))
async def run_bash(cmd, cwd):
    c, t = await bsvc.start_bash_command(ExecuteBashRequest(command=cmd, cwd=cwd, timeout=20)); await t
    return (await bsvc.search_bash_events(command_id__eq=c.id)).items[-1]
o = asyncio.run(run_bash("find . -type f -name '*.txt' | sort", root))
assert o.exit_code == 0 and "./sub/a.txt" in o.stdout, o
o = asyncio.run(run_bash("echo server", "/tmp")); assert o.stdout.strip() == "server"  # non-PC unchanged
# commits section on the PC
import subprocess
def sh(*a): subprocess.run(a, cwd=root, check=True, capture_output=True)
sh("git", "config", "user.email", "t@t"); sh("git", "config", "user.name", "t")
from openhands.agent_server import git_router as gr
assert gr.get_git_commits(root, limit=50).commits == []  # unborn HEAD
sh("git", "add", "sub/a.txt"); sh("git", "commit", "-qm", "first")
open(f"{root}/sub/a.txt", "w").write("دو\n"); sh("git", "commit", "-qam", "second")
page = gr.get_git_commits(root, limit=1); assert [c.subject for c in page.commits] == ["second"] and page.has_more, page
sha = page.commits[0].sha
assert [(c.status.value, str(c.path)) for c in gr.get_commit_changes(root, sha)] == [("UPDATED", "sub/a.txt")]
fd = gr.get_commit_file_diff(f"{root}/sub/a.txt", sha); assert fd.original == "سلام\n" and fd.modified == "دو\n", fd
first = gr.get_git_commits(root, limit=5).commits[-1].sha
assert gr.get_commit_file_diff(f"{root}/sub/a.txt", first).original == ""
_ch = [str(c.path) for c in gr.get_git_changes(root, ref=None)]; assert "sub/a.txt" not in _ch and "i.png" in _ch, _ch
# conversation start: default app folder is git-inited on the PC, junk relative dir removed
import threading, time
proj = f"{root}/workspace/project/" + "ab" * 16
nsp._pc_workspace_started(proj)
for _ in range(50):
    if os.path.isdir(proj + "/.git"): break
    time.sleep(0.1)
assert os.path.isdir(proj + "/.git")
os.makedirs("C:\\U/workspace/project/x", exist_ok=True); nsp._pc_workspace_started("C:\\U/workspace/project/x")
assert not os.path.exists("C:\\U")
print("OK: workspace panel (text/traversal/404), commits/commit changes/commit diff, auto git init, junk dir cleanup")
print("OK: text upload, binary gate, base64 upload/download, non-PC passthrough, git init, git_not_installed")

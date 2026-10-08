# Run inside the agent-server image (needs git):
# docker run --rm -v $PWD/nova_server_patches:/patches/nova_server_patches:ro -v $PWD/tests/workspace_git_check.py:/t.py:ro --entrypoint python3 <image> /t.py
import os, subprocess, sys, tempfile, types
repo = tempfile.mkdtemp(prefix="pc-")
def sh(*a): subprocess.run(a, cwd=repo, check=True, capture_output=True)
sh("git", "init", "-q"); sh("git", "config", "user.email", "t@t"); sh("git", "config", "user.name", "t")
open(f"{repo}/a.txt", "w").write("one\n"); open(f"{repo}/keep.log", "w").write("user file\n")
open(f"{repo}/.gitignore", "w").write("")
sh("git", "add", "a.txt", ".gitignore"); sh("git", "commit", "-qm", "init")

class FakePC:  # the customer's sidecar, but local
    def hello(self): return {"roots": [repo], "system": {"home": "/root"}}
    def list_dir(self, p):
        if not os.path.isdir(p): raise RuntimeError("not a dir")
        return {"entries": os.listdir(p)}
    def read_file(self, p): return {"content": open(p).read()}
    def exec(self, argv, cwd=None, timeout_ms=None):
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
        return {"exit_code": r.returncode, "stdout": r.stdout, "stderr": r.stderr}
sb = types.ModuleType("sidecar_bridge"); sbc = types.ModuleType("sidecar_bridge.client")
sbc.client_from_env = lambda: FakePC(); sb.client = sbc
sys.modules["sidecar_bridge"] = sb; sys.modules["sidecar_bridge.client"] = sbc
sys.path.insert(0, "/patches")
import nova_server_patches as nsp
from nova_server_patches import workspace_git as wg
from openhands.agent_server import git_router as gr

cp = wg.create_checkpoint(repo, "before agent")
open(f"{repo}/a.txt", "w").write("two\n"); open(f"{repo}/new.py", "w").write("print(1)\n")
ch = {str(c.path): c.status.value for c in gr.get_git_changes(repo)}
assert ch == {"a.txt": "UPDATED", "new.py": "ADDED", "keep.log": "ADDED"}, ch
d = gr.get_git_diff(f"{repo}/a.txt"); assert d.original == "one\n" and d.modified == "two\n", d
wg.accept(f"{repo}/a.txt"); assert "M  a.txt" in subprocess.run(["git","status","--porcelain"],cwd=repo,capture_output=True,text=True).stdout
wg.revert(f"{repo}/new.py"); assert not os.path.exists(f"{repo}/new.py")
open(f"{repo}/new2.py", "w").write("x\n")
r = wg.restore_checkpoint(repo, cp["id"])
assert open(f"{repo}/a.txt").read() == "one\n" and not os.path.exists(f"{repo}/new2.py") and os.path.exists(f"{repo}/keep.log"), r
assert len(wg.list_checkpoints(repo)) == 2  # the original + the automatic "before restore"
paths = {r.path for r in gr.git_router.routes}
assert {"/git/accept", "/git/revert", "/git/checkpoints", "/git/checkpoints/restore"} <= paths, paths
print("OK: changes, diff, accept, revert, checkpoint create/list/restore (+undo), routes")

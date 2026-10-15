# Run inside the agent-server image:
# docker run --rm -e OPENHANDS_SUPPRESS_BANNER=1 -v $PWD/sidecar_bridge:/opt/nova/tools/sidecar_bridge:ro \
#   -v $PWD/tests/image_view_check.py:/t.py:ro --entrypoint python3 <image> /t.py
# file_editor "view" of an image on the customer's PC returns the picture (ImageContent) like the stock editor;
# other binaries are refused instead of being sent to the model as raw bytes; text files still read normally.
import base64, os, sys, tempfile, zlib, struct
from pathlib import Path
sys.path.insert(0, "/opt/nova/tools")
from sidecar_bridge import tools_proxy
from sidecar_bridge.client import SidecarClientError

root = tempfile.mkdtemp(prefix="pc-")
def png(w=2, h=2):
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
open(os.path.join(root, "photo ۱.png"), "wb").write(png())
open(os.path.join(root, "blob.bin"), "wb").write(b"\x00\x01\x02binary")
open(os.path.join(root, "notes.txt"), "w").write("line one\nline two\n")

class FakePC:
    seen = []
    def hello(self): return {"roots": [root], "capabilities": ["file_base64"]}
    def request(self, t, p):
        FakePC.seen.append((t, p.get("encoding")))
        if t == "read_file":
            raw = open(p["path"], "rb").read()
            if p.get("encoding") == "base64": return {"content": base64.b64encode(raw).decode()}
            return {"content": raw.decode("utf-8", "replace") if b"\x00" not in raw else raw.decode("latin-1")}
        if t == "list_dir":
            if os.path.isdir(p["path"]): return {"entries": [{"name": n} for n in os.listdir(p["path"])]}
            raise SidecarClientError("invalid_request", "not a dir") if os.path.exists(p["path"]) else SidecarClientError("not_found", "missing")
        raise AssertionError(t)
    def read_file(self, p): return self.request("read_file", {"path": p})
    def list_dir(self, p): return self.request("list_dir", {"path": p})

pc = FakePC()
assert tools_proxy._install_file_editor(pc)
from openhands.tools.file_editor.impl import FileEditorExecutor
from openhands.tools.file_editor.exceptions import ToolError
ed = FileEditorExecutor(workspace_root=root).editor

obs = ed.view(Path(root) / "photo ۱.png")
kinds = [type(c).__name__ for c in obs.content]
assert kinds == ["TextContent", "ImageContent"], kinds
url = obs.content[1].image_urls[0]
assert url.startswith("data:image/png;base64,") and base64.b64decode(url.split(",", 1)[1]).startswith(b"\x89PNG"), url[:40]
assert ("read_file", "base64") in FakePC.seen
print("image ok:", kinds)

try:
    ed.view(Path(root) / "blob.bin"); raise SystemExit("binary was not refused")
except ToolError as e:
    assert "binary" in str(e); print("binary refused ok")

t = ed.view(Path(root) / "notes.txt")
assert "line two" in str(t.content[0].text), t
print("text ok")
print("ALL OK")

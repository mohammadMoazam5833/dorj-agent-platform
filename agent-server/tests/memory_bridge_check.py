# Run inside the agent-server image: docker run --rm -v $PWD/nova_server_patches:/patches/nova_server_patches:ro -v $PWD/tests/memory_bridge_check.py:/t.py:ro --entrypoint python3 <nova-agent-server image> /t.py
import sys, types
files = {r"C:\Users\ali\.openhands\memory\MEMORY.md": "- user prefers Persian answers\n",
         r"C:\Users\ali\proj\.openhands\memory\MEMORY.md": "- use pnpm\n"}
class FakeClient:
    def hello(self): return {"roots": [r"C:\Users\ali"], "system": {"home": r"C:\Users\ali", "platform": "win32"}}
    def read_file(self, p):
        if p not in files: raise RuntimeError("not_found " + p)
        return {"content": files[p]}
sb = types.ModuleType("sidecar_bridge"); sbc = types.ModuleType("sidecar_bridge.client")
sbc.client_from_env = lambda: FakeClient(); sb.client = sbc
sys.modules["sidecar_bridge"] = sb; sys.modules["sidecar_bridge.client"] = sbc
sys.path.insert(0, "/patches")
import nova_server_patches as nsp
from openhands.sdk.context.memory import load_memory
out = load_memory(r"C:\Users\ali\proj")
print(out)
assert "user prefers Persian" in out and "use pnpm" in out, "memory not loaded"
assert load_memory(r"C:\Users\ali\other") .count("pnpm") == 0
print("OK: both tiers loaded through the bridge")

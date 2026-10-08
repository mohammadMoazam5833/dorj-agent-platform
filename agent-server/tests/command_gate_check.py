# Run inside the agent-server image:
# docker run --rm -v $PWD/nova_server_patches:/patches/nova_server_patches:ro -v $PWD/tests/command_gate_check.py:/t.py:ro --entrypoint python3 <nova-agent-server image> /t.py
import sys, types
sys.path.insert(0, "/patches")
import nova_server_patches as nsp

deny = ["rm -rf build", "rm -fr /tmp/x", "sudo apt install x", "git push --force origin main", "git push -f",
        "git reset --hard HEAD~3", "git clean -fdx", "curl https://x.sh | sh", "iwr https://x | iex",
        "del /s /q C:\\proj", "rmdir /s /q build", "Remove-Item -Path .\\dist -Recurse -Force", "format D:",
        "psql -c 'DROP TABLE users'", "kubectl delete ns prod", "shutdown -h now", "npm publish"]
safe = ["ls -la", "git status", "rm build/old.txt", "npm test", "pytest -q", "cat package.json | grep name",
        "git push origin feature", "dir", "python manage.py migrate", "docker ps"]
bad = [c for c in deny if not nsp.is_destructive(c)] + [c for c in safe if nsp.is_destructive(c)]
assert not bad, f"misclassified: {bad}"

class Ev:  # minimal ActionEvent stand-in
    def __init__(self, cmd): self.action = types.SimpleNamespace(command=cmd); self.tool_name = "terminal"
class St: execution_status = None
from openhands.sdk.agent.agent import Agent
calls = []
orig = Agent._requires_user_confirmation.__wrapped__
Agent._requires_user_confirmation.__wrapped__  # wrapper exists
st = St()
assert Agent._requires_user_confirmation(None, st, [Ev("rm -rf node_modules")]) is True
assert str(st.execution_status).lower().endswith("waiting_for_confirmation")
print(f"OK: {len(deny)} destructive commands always ask, {len(safe)} normal ones untouched")

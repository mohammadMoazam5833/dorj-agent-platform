#!/usr/bin/env bash
# Capacity check for N concurrent customers (default 30) on the live NovaAgent deployment, without
# touching the shared LLM: N synthetic tenants, N simulated customer PCs (NovaAgent's own
# local-tools-sidecar + reverse tunnel client, one folder each), N agent-server workspaces cold-started
# at once, then for every tenant a conversation start that must be bridged to *that* tenant's PC
# (working_dir remapped to its own folder) - proving routing and isolation per customer.
# Cleans up everything it created (tenants, workspaces, PVCs, LiteLLM keys) unless KEEP=1.
#   e2e/load30.sh [N]
set -euo pipefail
N=${1:-30}
HERE=$(cd "$(dirname "$0")" && pwd)
NOVA_UI_SRC=${NOVA_UI_SRC:-/workdir/NovaAgent}
K="/workdir/k8s-deploy/inventory/mycluster/artifacts/kubectl --kubeconfig=/workdir/k8s-deploy/inventory/mycluster/artifacts/admin.conf"
S=$(mktemp -d)
IP=${NOVA_INGRESS_IP:-172.16.50.202}
CA=${NOVA_CA_BUNDLE:?org root CA}
POD=$($K -n nova get pod -l app=nova-control -o name | head -1)
export -n HTTP_PROXY HTTPS_PROXY http_proxy https_proxy 2>/dev/null || true
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy

cleanup() {
  [[ "${KEEP:-0}" == 1 ]] && { echo "KEEP=1: leaving load-test tenants"; return; }
  docker rm -f nova-load-pcs >/dev/null 2>&1 || true
  $K -n nova exec -i "$POD" -c control -- python - "$N" <<'PY' || true
import asyncio, sys
from nova_control import tenants, workspaces, llmkeys
from kubernetes.client.rest import ApiException
core, apps = workspaces._apis()
ns = workspaces.settings.workspace_namespace
for i in range(1, int(sys.argv[1]) + 1):
    name = f"loadtest{i:02d}"
    for fn in (apps.delete_namespaced_deployment, core.delete_namespaced_service, core.delete_namespaced_secret,
               core.delete_namespaced_persistent_volume_claim):
        try:
            fn(f"ws-{name}", ns)
        except ApiException:
            pass
    asyncio.run(llmkeys.delete_user_key(name))
    tenants.remove(name)
print("cleaned up")
PY
  rm -rf "$S"
}
trap cleanup EXIT

echo "== 1. $N synthetic tenants"
$K -n nova exec -i "$POD" -c control -- python - "$N" > "$S/tokens" <<'PY'
import sys
from nova_control import tenants
for i in range(1, int(sys.argv[1]) + 1):
    c = tenants.ensure_for(f"loadtest{i:02d}@loadtest.local")
    print(c["name"], c["token"])
PY
wc -l < "$S/tokens"

echo "== 2. $N customer PCs (sidecar + tunnel each) in one container"
awk '{print $1, $2}' "$S/tokens" > "$S/pcs.txt"
docker rm -f nova-load-pcs >/dev/null 2>&1 || true
docker run -d --name nova-load-pcs --add-host "nova.isigpu.local:$IP" -v "$NOVA_UI_SRC/scripts:/app/scripts:ro" \
  -v "$CA:/ca.crt:ro" -v "$S/pcs.txt:/pcs.txt:ro" -w /app 172.16.50.89:5000/library/node:24-slim bash -c '
i=0; while read -r name tok; do i=$((i+1)); mkdir -p /pcs/$name; echo "$name" > /pcs/$name/owner.txt
  port=$((19000+i))
  NOVAAGENT_LOCAL_TOOLS_TOKEN=$tok NOVAAGENT_LOCAL_TOOLS_ROOTS=/pcs/$name NOVAAGENT_LOCAL_TOOLS_PORT=$port \
    node scripts/local-tools-sidecar.mjs > /tmp/sc-$name.log 2>&1 &
  sleep 0.2
  NODE_EXTRA_CA_CERTS=/ca.crt NOVAAGENT_REMOTE_URL=https://nova.isigpu.local NOVAAGENT_LOCAL_TOOLS_URL=http://127.0.0.1:$port \
    NOVAAGENT_CUSTOMER_WORKSPACE_TOKEN=$tok NOVAAGENT_LOCAL_TOOLS_TOKEN=$tok node scripts/customer-workspace-client.mjs > /tmp/cw-$name.log 2>&1 &
done < /pcs.txt; wait' >/dev/null
for t in $(seq 1 60); do
  up=$($K -n nova exec deploy/nova-gateway -- node -e 'fetch("http://127.0.0.1:18766/health").then(r=>r.json()).then(j=>console.log(j.customers.filter(c=>c.name.startsWith("loadtest")&&c.connected).length))')
  [[ "$up" -ge "$N" ]] && break; sleep 2
done
echo "tunnels connected: $up / $N"

echo "== 3. cold-start $N workspaces concurrently, then a bridged conversation start per tenant"
python3 - "$S/tokens" "$CA" "$IP" <<'PY'
import concurrent.futures as cf, json, socket, ssl, sys, time, urllib.error, urllib.request
toks = [l.split() for l in open(sys.argv[1])]
ctx = ssl.create_default_context(cafile=sys.argv[2])
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda h, *a, **k: _orig(sys.argv[3] if h == "nova.isigpu.local" else h, *a, **k)
def call(tok, method, path, body=None, timeout=400, headers=None):
    r = urllib.request.Request("https://nova.isigpu.local" + path, method=method,
                               data=json.dumps(body).encode() if body is not None else None,
                               headers={"X-Session-API-Key": tok, "Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, context=ctx, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read()[:300].decode(errors="replace")}
def one(nt):
    name, tok = nt
    t0 = time.time()
    ws = call(tok, "POST", "/v1/workspace")
    t_ready = time.time() - t0
    st = call(tok, "GET", "/api/settings", headers={"X-Expose-Secrets": "encrypted"})
    conv = call(tok, "POST", "/api/conversations", {"workspace": {"kind": "LocalWorkspace", "working_dir": "/somewhere/else"},
                                                    "agent_settings": st["agent_settings"], "secrets_encrypted": True})
    wd = conv.get("workspace", {}).get("working_dir") or conv
    return name, ws.get("state"), round(t_ready, 1), wd
t0 = time.time()
with cf.ThreadPoolExecutor(max_workers=len(toks)) as ex:
    res = list(ex.map(one, toks))
wall = time.time() - t0
ok = [r for r in res if r[1] == "ready" and r[3] == f"/pcs/{r[0]}"]
starts = sorted(r[2] for r in res)
print(f"workspaces ready + conversation bridged to its own PC: {len(ok)}/{len(res)}")
print(f"cold start: p50 {starts[len(starts)//2]}s  p95 {starts[int(len(starts)*0.95)-1]}s  max {starts[-1]}s  (wall {wall:.0f}s)")
for r in res:
    if r not in ok:
        print("  NOT OK:", r)
sys.exit(0 if len(ok) == len(res) else 1)
PY
echo "== 4. control-plane footprint under $N tunnels + workspaces"
$K -n nova top pods 2>/dev/null || true

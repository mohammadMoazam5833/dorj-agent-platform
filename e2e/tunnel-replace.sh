#!/usr/bin/env bash
# One account opened on two computers (customer report 2026-10-05: ~80k reconnects in 2 days).
# Two reverse-tunnel clients - the patched scripts shipped in the desktop package
# (desktop/.stage/scripts after desktop/build-client.sh) - connect with the same tenant token.
# Expect: the first is closed with 4001 session_replaced, logs it, stops (exit 3, the app's
# "account open on another computer" signal) and never reconnects; the second stays connected; the
# gateway replaces exactly once.
#   E2E_USER=... E2E_PASS=... NOVA_CA_BUNDLE=... e2e/tunnel-replace.sh
set -euo pipefail
cd "$(dirname "$0")"
IP=${NOVA_INGRESS_IP:-172.16.50.202}
SCRIPTS=$(realpath ../desktop/.stage/scripts)
K="/workdir/k8s-deploy/inventory/mycluster/artifacts/kubectl --kubeconfig=/workdir/k8s-deploy/inventory/mycluster/artifacts/admin.conf"
TOK=$(curl -sk --noproxy '*' --resolve "nova.isigpu.local:443:$IP" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$E2E_USER\",\"password\":\"$E2E_PASS\"}" https://nova.isigpu.local/api/auth/login \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
out=$(docker run --rm --add-host "nova.isigpu.local:$IP" -v "$SCRIPTS:/app/scripts:ro" -v "$NOVA_CA_BUNDLE:/ca.crt:ro" \
  -e TOK="$TOK" -w /app 172.16.50.89:5000/library/node:24-slim bash -c '
mkdir -p /pc && NOVAAGENT_LOCAL_TOOLS_TOKEN=$TOK NOVAAGENT_LOCAL_TOOLS_ROOTS=/pc node scripts/local-tools-sidecar.mjs >/dev/null 2>&1 &
sleep 1
run() { NODE_EXTRA_CA_CERTS=/ca.crt NOVAAGENT_REMOTE_URL=https://nova.isigpu.local NOVAAGENT_CUSTOMER_WORKSPACE_TOKEN=$TOK \
        NOVAAGENT_LOCAL_TOOLS_TOKEN=$TOK node scripts/customer-workspace-client.mjs; }
run > /tmp/a.log 2>&1 & A=$!
sleep 4
run > /tmp/b.log 2>&1 & B=$!
wait $A; echo "A_EXIT=$?"
sleep 20                                   # a reconnect loop would show up here
echo "B_ALIVE=$(kill -0 $B 2>/dev/null && echo yes || echo no)"
echo "--- A"; cat /tmp/a.log; echo "--- B"; cat /tmp/b.log; kill $B 2>/dev/null || true')
echo "$out"
ME=$(echo "$E2E_USER" | cut -d@ -f1 | tr "[:upper:]" "[:lower:]")
replaced=$($K -n nova logs deploy/nova-gateway --since-time="$since" 2>/dev/null | grep -c "customer '$ME' connected again" || true)
echo "gateway replacements: $replaced"
fail=0
grep -q "A_EXIT=3" <<<"$out" || { echo "FAIL: first client did not stop with exit 3"; fail=1; }
grep -q "closed: code=4001 reason=session_replaced" <<<"$out" || { echo "FAIL: close code/reason not logged"; fail=1; }
grep -q "B_ALIVE=yes" <<<"$out" || { echo "FAIL: second client not alive"; fail=1; }
[[ $(sed -n '/--- A/,/--- B/p' <<<"$out" | grep -c "connected wss") -eq 1 ]] || { echo "FAIL: first client reconnected"; fail=1; }
[[ $(sed -n '/--- B/,$p' <<<"$out" | grep -c "connected wss") -eq 1 ]] || { echo "FAIL: second client reconnected"; fail=1; }
[[ "$replaced" -eq 1 ]] || { echo "FAIL: expected exactly one replacement, got $replaced"; fail=1; }
[[ $fail == 0 ]] && echo "PASS: session replacement is explicit and final (no reconnect loop)"
exit $fail

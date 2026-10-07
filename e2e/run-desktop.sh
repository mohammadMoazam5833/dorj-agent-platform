#!/usr/bin/env bash
# Run desktop.spec.js as a customer would: install the built .deb in a clean Ubuntu 22.04 (the
# electron-builder image), resolve the company hosts like the customer's DNS would, run under Xvfb.
#   E2E_USER=... E2E_PASS=... e2e/run-desktop.sh [shots dir]
set -euo pipefail
cd "$(dirname "$0")"
SHOTS=$(realpath "${1:-.}")
DEB=$(ls ../desktop/dist/Dorj-Agent-Client-*-amd64.deb ../desktop/dist/NovaAgent-*-amd64.deb 2>/dev/null | tail -1 || true)
if [[ -z "$DEB" ]]; then
  echo "No Dorj-Agent-Client-*-amd64.deb or NovaAgent-*-amd64.deb found in ../desktop/dist" >&2
  exit 1
fi
IP=${NOVA_INGRESS_IP:-172.16.50.202}
# /dev/shm read-only: emulates guests where Chromium cannot create shared memory there (VMware Ubuntu
# report 2026-10-05) - the packaged app must start anyway (--disable-dev-shm-usage)
docker run --rm --tmpfs /dev/shm:ro,size=1m -e HTTPS_PROXY -e HTTP_PROXY -e E2E_USER -e E2E_PASS -e HOST_UID="$(id -u):$(id -g)" \
  --add-host "nova.isigpu.local:$IP" --add-host "identity.isigpu.local:$IP" \
  -v /workdir:/workdir -v "$SHOTS:/shots" \
  -e NOVA_SHOTS=/shots -w "$PWD" 172.16.50.89:8085/electronuserland/builder:wine bash -c '
set -e
export DEBIAN_FRONTEND=noninteractive
echo "Acquire::http::Proxy \"$HTTP_PROXY\"; Acquire::https::Proxy \"$HTTPS_PROXY\";" > /etc/apt/apt.conf.d/90proxy
apt-get update -qq >/dev/null && apt-get install -y -qq xvfb '"$(realpath ../desktop/dist)"'/'"$(basename "$DEB")"' >/tmp/apt.log 2>&1 || { tail -20 /tmp/apt.log; exit 1; }
unset HTTPS_PROXY HTTP_PROXY
set +e
xvfb-run -a -s "-screen 0 1440x900x24" npx playwright test -c playwright.config.js desktop.spec.js --reporter=line > /tmp/pw.log 2>&1; c=$?
grep -vE "angle|EGL|egl|gl_display|GLDisplay|dbus" /tmp/pw.log | tail -30
chown -R "$HOST_UID" /shots test-results 2>/dev/null; exit $c'

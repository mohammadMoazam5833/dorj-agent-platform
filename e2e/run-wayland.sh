#!/usr/bin/env bash
# Run wayland.spec.js against an installed .deb under weston (headless backend) in Ubuntu 22.04.
#   E2E_USER=... E2E_PASS=... e2e/run-wayland.sh [deb] [shots dir]     (extra env passes through:
#   NOVA_EXPECT=stuck, NOVAAGENT_TEST_IGNORE_READY_TO_SHOW=1)
set -euo pipefail
cd "$(dirname "$0")"
DEB=$(realpath "${1:-$(ls ../desktop/dist/NovaAgent-*-amd64.deb | tail -1)}")
SHOTS=$(realpath "${2:-.}")
IP=${NOVA_INGRESS_IP:-172.16.50.202}
docker run --rm --tmpfs /dev/shm:ro,size=1m -e HTTPS_PROXY -e HTTP_PROXY -e E2E_USER -e E2E_PASS \
  -e NOVA_EXPECT -e NOVAAGENT_TEST_IGNORE_READY_TO_SHOW -e HOST_UID="$(id -u):$(id -g)" \
  --add-host "nova.isigpu.local:$IP" --add-host "identity.isigpu.local:$IP" \
  -v /workdir:/workdir -v "$DEB:/client.deb:ro" -v "$SHOTS:/shots" -e NOVA_SHOTS=/shots -w "$PWD" \
  172.16.50.89:8085/electronuserland/builder:wine bash -c '
set -e
export DEBIAN_FRONTEND=noninteractive
echo "Acquire::http::Proxy \"$HTTP_PROXY\"; Acquire::https::Proxy \"$HTTPS_PROXY\";" > /etc/apt/apt.conf.d/90proxy
apt-get update -qq >/dev/null && apt-get install -y -qq weston /client.deb >/tmp/apt.log 2>&1 || { tail -20 /tmp/apt.log; exit 1; }
unset HTTPS_PROXY HTTP_PROXY DISPLAY
export XDG_RUNTIME_DIR=/tmp/xdg && mkdir -p $XDG_RUNTIME_DIR && chmod 700 $XDG_RUNTIME_DIR
weston --backend=headless-backend.so --socket=wayland-1 --idle-time=0 >/tmp/weston.log 2>&1 &
for i in $(seq 1 20); do [ -S $XDG_RUNTIME_DIR/wayland-1 ] && break; sleep 0.5; done
export WAYLAND_DISPLAY=wayland-1
echo "weston: $(ls $XDG_RUNTIME_DIR)  client: $(dpkg-query -W -f="\${Version}" novaagent-client)"
set +e
npx playwright test -c playwright.config.js wayland.spec.js --reporter=line > /tmp/pw.log 2>&1; c=$?
grep -vE "angle|EGL|egl|gl_display|GLDisplay|dbus" /tmp/pw.log | tail -25
chown -R "$HOST_UID" /shots test-results 2>/dev/null; exit $c'

#!/usr/bin/env bash
# Build the NovaAgent desktop client (NovaAgent's own thin Electron client, electron-builder.thin.config.mjs)
# from the NovaAgent source + overlay.py, with the company server URL baked in:
#   desktop/build-client.sh <NovaAgent src> <server URL>
#   -> desktop/dist/{NovaAgent-Setup-<v>-x64.exe, NovaAgent-<v>-amd64.deb, NovaAgent-<v>-x86_64.AppImage, SHA256SUMS}
# Runs in the electron-builder wine image (Windows icon/version editing on a Linux host).
set -euo pipefail
cd "$(dirname "$0")"
SRC=$(realpath "$1") URL=$2
IMAGE=${NOVA_BUILDER_IMAGE:-172.16.50.89:8085/electronuserland/builder:wine}
NPM_REGISTRY=${NOVA_NPM_REGISTRY:-http://172.16.50.89:8083/repository/npm-proxy/}
rm -rf .stage dist && mkdir -p .stage/scripts
cp -r "$SRC/electron" .stage/
cp "$SRC"/electron-builder.config.mjs "$SRC"/electron-builder.thin.config.mjs .stage/
for f in desktop-remote-url.mjs desktop-company-llm-url.mjs local-tools-sidecar.mjs customer-workspace-client.mjs \
         prepare-thin-remote-client.mjs local-tools-sidecar customer-workspace; do cp -r "$SRC/scripts/$f" .stage/scripts/; done
python3 - "$SRC/package.json" .stage/package.json <<'PY'
import json, sys
up = json.load(open(sys.argv[1]))
pick = lambda k: {n: (up.get("dependencies", {}) | up.get("devDependencies", {}))[n] for n in k}
json.dump({"name": "novaagent", "version": up["version"], "private": True,
           "description": "NovaAgent desktop client", "author": {"name": "NovaAgent", "email": "nova@isigpu.local"},
           "homepage": "https://nova.isigpu.local", "license": "MIT",
           "dependencies": pick(["sirv", "httpxy"]), "devDependencies": pick(["electron", "electron-builder"])},
          open(sys.argv[2], "w"), indent=2)
PY
python3 overlay.py .stage .
mkdir -p ~/.cache/electron ~/.cache/electron-builder
docker run --rm -e HTTPS_PROXY -e HTTP_PROXY -e https_proxy="${HTTPS_PROXY:-}" -e http_proxy="${HTTP_PROXY:-}" -e NO_PROXY=172.16.50.89 \
  -e NOVAAGENT_REMOTE_URL="$URL" -e npm_config_registry="$NPM_REGISTRY" \
  -e ELECTRON_CACHE=/cache/electron -e ELECTRON_BUILDER_CACHE=/cache/eb \
  -v ~/.cache/electron:/cache/electron -v ~/.cache/electron-builder:/cache/eb -v "$PWD/.stage:/project" -w /project "$IMAGE" \
  bash -c "set -e; npm install --no-audit --no-fund --loglevel=error
           node scripts/prepare-thin-remote-client.mjs
           npx electron-builder --config electron-builder.thin.config.mjs --linux deb AppImage --win nsis
           chown -R $(id -u):$(id -g) /project /cache"
mkdir -p dist && cp .stage/dist-electron/NovaAgent-*.{exe,deb,AppImage} dist/
(cd dist && sha256sum NovaAgent-* > SHA256SUMS)
ls -la dist

#!/usr/bin/env bash
# Build the Dorj Agent desktop client (Dorj Agent thin Electron client, electron-builder.thin.config.mjs)
# from the Dorj Agent source + overlay.py, with the company server URL baked in:
#   desktop/build-client.sh <Dorj-Agent-Client src> <server URL>
#   -> desktop/dist/{Dorj-Agent-Client-Setup-<v>-x64.exe, Dorj-Agent-Client-<v>-amd64.deb, Dorj-Agent-Client-<v>-x86_64.AppImage, SHA256SUMS, VERSION}
#   <v> = Dorj-Agent-Client's package.json version + "+nova.$NOVA_CLIENT_REV" (default 1)
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
REV=${NOVA_CLIENT_REV:-1}   # our package revision on top of the Dorj Agent version: 1.6.6+nova.N
# ("+", not "-": electron-builder turns a semver pre-release "-" into a Debian "~", which sorts
#  *below* 1.6.5 - apt would treat the fixed package as a downgrade)
python3 - "$SRC/package.json" .stage/package.json "$REV" <<'PY'
import json, sys
up = json.load(open(sys.argv[1]))
up["version"] = f"{up['version']}+nova.{sys.argv[3]}"
pick = lambda k: {n: (up.get("dependencies", {}) | up.get("devDependencies", {}))[n] for n in k}
json.dump({"name": "dorj-agent-client", "version": up["version"], "private": True,
           "description": "Dorj Agent desktop client", "author": {"name": "Dorj Agent", "email": "client@isigpu.local"},
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
mkdir -p dist && cp .stage/dist-electron/Dorj-Agent-Client-*.{exe,deb,AppImage} dist/
python3 -c 'import json; print(json.load(open(".stage/package.json"))["version"])' > dist/VERSION
(cd dist && sha256sum Dorj-Agent-Client-* > SHA256SUMS)
ls -la dist

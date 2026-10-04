#!/usr/bin/env bash
# Build NovaAgent desktop installers (Linux .deb + AppImage, Windows NSIS .exe) in the pinned
# electron-builder wine image, so Windows icon/version editing works on a Linux build host.
# Usage: desktop/build.sh   -> desktop/dist/{NovaAgent-<v>-amd64.deb, NovaAgent-<v>-x86_64.AppImage, NovaAgent-Setup-<v>-x64.exe}
set -euo pipefail
cd "$(dirname "$0")"
IMAGE=${NOVA_BUILDER_IMAGE:-172.16.50.89:8085/electronuserland/builder:wine}
mkdir -p ~/.cache/electron ~/.cache/electron-builder
rm -rf dist
docker run --rm -e HTTPS_PROXY -e HTTP_PROXY -e https_proxy="${HTTPS_PROXY:-}" -e http_proxy="${HTTP_PROXY:-}" \
  -e ELECTRON_CACHE=/cache/electron -e ELECTRON_BUILDER_CACHE=/cache/eb \
  -v ~/.cache/electron:/cache/electron -v ~/.cache/electron-builder:/cache/eb -v "$PWD:/project" -w /project "$IMAGE" \
  bash -c "set -e; [ -d node_modules/electron-builder ] || npm ci --no-audit --no-fund
           npx electron-builder --config electron-builder.config.js --linux deb AppImage --win nsis
           cd dist && sha256sum *.deb *.AppImage *.exe > SHA256SUMS; chown -R $(id -u):$(id -g) /project/dist /cache"
ls -la dist/*.deb dist/*.AppImage dist/*.exe dist/SHA256SUMS

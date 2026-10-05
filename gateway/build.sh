#!/usr/bin/env bash
# Stage NovaAgent's gateway sources and build the image.  Usage: gateway/build.sh <NovaAgent src> <image>
set -euo pipefail
cd "$(dirname "$0")"
SRC=$1 IMAGE=$2
rm -rf scripts && mkdir -p scripts
cp -r "$SRC/scripts/customer-workspace" "$SRC/scripts/local-tools-sidecar" "$SRC/scripts/customer-workspace-gateway.mjs" scripts/
python3 patch_gateway.py scripts      # cross-tenant response mix-up fix + 4001 session_replaced (see the file)
python3 ../patches/ws_close_codes.py scripts   # close frames carry code + reason
docker build -t "$IMAGE" .

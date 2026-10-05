#!/usr/bin/env bash
# Stage NovaAgent's gateway sources and build the image.  Usage: gateway/build.sh <NovaAgent src> <image>
set -euo pipefail
cd "$(dirname "$0")"
SRC=$1 IMAGE=$2
rm -rf scripts && mkdir -p scripts
cp -r "$SRC/scripts/customer-workspace" "$SRC/scripts/local-tools-sidecar" "$SRC/scripts/customer-workspace-gateway.mjs" scripts/
python3 patch_gateway.py scripts      # cross-tenant response mix-up fix (see the file)
docker build -t "$IMAGE" .

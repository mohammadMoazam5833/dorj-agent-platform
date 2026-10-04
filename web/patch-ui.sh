#!/usr/bin/env bash
# Build-time overlay applied to a copy of the NovaAgent UI source (never to the upstream repo).
# 1. Self-host the fonts: src/index.css @imports Google Fonts, a render-blocking stylesheet that
#    leaves the page blank until the request fails on an air-gapped network.
set -euo pipefail
cd "${1:?usage: patch-ui.sh <ui-source-dir>}"
npm install --no-save --no-audit --no-fund @fontsource-variable/outfit@5 @fontsource/ibm-plex-mono@5
node - <<'JS'
const fs = require("fs");
const p = "src/index.css";
let s = fs.readFileSync(p, "utf8");
const before = s.length;
s = s.replace(/^@import url\("https:\/\/fonts\.googleapis\.com[^"]*"\);\n/gm, "");
const fonts = '@import "@fontsource-variable/outfit";\n' +
  [400, 500, 600, 700].map(w => `@import "@fontsource/ibm-plex-mono/${w}.css";\n`).join("") +
  '@import "@fontsource/ibm-plex-mono/400-italic.css";\n';
fs.writeFileSync(p, fonts + s);
console.log(`index.css: removed ${before - s.length} bytes of Google Fonts imports, self-hosted Outfit + IBM Plex Mono`);
JS
if grep -rn "fonts.googleapis.com" src index.html 2>/dev/null; then echo "Google Fonts reference left" >&2; exit 1; fi

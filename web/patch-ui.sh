#!/usr/bin/env bash
# Build-time overlay applied to a copy of the NovaAgent UI source (never to the upstream repo).
# 1. Self-host the fonts: src/index.css @imports Google Fonts, a render-blocking stylesheet that
#    leaves the page blank until the request fails on an air-gapped network.
# 2. Apply the platform.isigpu.local brand: platform D mark, favicon, product title, and manifest.
set -euo pipefail
# Resolve before the cd (BASH_SOURCE may be relative) and export it: the node block below reads it.
export SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${1:?usage: patch-ui.sh <ui-source-dir>}"
if [ ! -d node_modules/@fontsource-variable/outfit ] || [ ! -d node_modules/@fontsource/ibm-plex-mono ]; then
  npm install --no-save --no-audit --no-fund @fontsource-variable/outfit@5 @fontsource/ibm-plex-mono@5
fi
node - <<'JS'
const fs = require("fs");
const path = require("path");
const p = "src/index.css";
let s = fs.readFileSync(p, "utf8");
const before = s.length;
s = s.replace(/^@import url\("https:\/\/fonts\.googleapis\.com[^"]*"\);\n/gm, "");
const fonts = '@import "@fontsource-variable/outfit";\n' +
  [400, 500, 600, 700].map(w => `@import "@fontsource/ibm-plex-mono/${w}.css";\n`).join("") +
  '@import "@fontsource/ibm-plex-mono/400-italic.css";\n';
fs.writeFileSync(p, fonts + s);
console.log(`index.css: removed ${before - s.length} bytes of Google Fonts imports, self-hosted Outfit + IBM Plex Mono`);

const applyProductBrand = (value) => value
  .replace(/OpenHands Provider LLM Key/g, "Dorj Agent API Key")
  .replace(/OpenHands Cloud/g, "Dorj Agent")
  .replace(/OpenHands/g, "Dorj Agent")
  .replace(/NovaAgent/g, "Dorj Agent")
  // lower-case visible mentions ("@openhands fix ...", contact address); `.openhands/` stays:
  // it is the real folder the agent reads (memory, setup.sh), renaming it would break that.
  .replace(/[\w.+-]+@openhands\.dev/g, "your administrator")
  .replace(/@openhands\b/g, "@dorj-agent");

// Locale values are shown to users. Keep JSON keys and internal lower-case
// identifiers untouched so server contracts and imports remain stable.
const translationPath = "src/i18n/translation.json";
const translations = JSON.parse(fs.readFileSync(translationPath, "utf8"));
for (const values of Object.values(translations)) {
  for (const [key, value] of Object.entries(values)) {
    if (typeof value === "string") values[key] = applyProductBrand(value);
  }
}
// Command-menu search keywords (not shown, but searchable): "openhands" -> "dorj".
for (const [id, values] of Object.entries(translations)) {
  if (!id.endsWith("_KEYWORDS")) continue;
  for (const [key, value] of Object.entries(values)) {
    if (typeof value === "string") values[key] = value.replace(/\bopenhands\b/gi, "dorj");
  }
}
fs.writeFileSync(translationPath, JSON.stringify(translations, null, 2) + "\n");

const defaultBackendPath = "src/api/backend-registry/default-backend.ts";
const defaultBackend = fs.readFileSync(defaultBackendPath, "utf8");
fs.writeFileSync(defaultBackendPath, defaultBackend.replace(
  /export const LOCKED_CLOUD_BACKEND_NAME = "OpenHands Cloud";/,
  'export const LOCKED_CLOUD_BACKEND_NAME = "Dorj Agent";'
));
const backendModalPath = "src/components/features/backends/backend-form-modal.tsx";
const backendModal = fs.readFileSync(backendModalPath, "utf8");
fs.writeFileSync(backendModalPath, backendModal.replace(
  /name: "OpenHands Cloud",/,
  'name: "Dorj Agent",'
));

const brandDir = path.join(process.env.SCRIPT_DIR, "brand");
const dBase64 = fs.readFileSync(path.join(brandDir, "favicon-64x64.png")).toString("base64");
const dSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><image href="data:image/png;base64,${dBase64}" width="64" height="64"/></svg>\n`;
fs.mkdirSync("src/assets/branding", { recursive: true });
for (const name of ["dorj-agent-logo.svg", "dorj-agent-logo-mark.svg"]) {
  fs.writeFileSync(path.join("src/assets/branding", name), dSvg);
}
for (const name of [
  "favicon.svg", "favicon.ico", "favicon-16x16.png", "favicon-32x32.png", "favicon-48x48.png",
  "favicon-64x64.png", "favicon-96x96.png", "favicon-128x128.png", "favicon-256x256.png",
  "favicon-384x384.png", "favicon-512x512.png", "favicon-1024x1024.png", "apple-touch-icon.png",
  "android-chrome-192x192.png", "android-chrome-384x384.png", "android-chrome-512x512.png",
  "mstile-150x150.png",
]) {
  const src = path.join(brandDir, name);
  if (fs.existsSync(src)) fs.copyFileSync(src, path.join("public", name));
}
let root = fs.readFileSync("src/root.tsx", "utf8");
root = root.replace('{ name: "description", content: "Let\'s Start Building!" }', '{ name: "description", content: "Dorj Agent" }');
if (root.includes('{ name: "description", content: "Dorj Agent" }') === false) throw new Error("description replacement failed");
fs.writeFileSync("src/root.tsx", root);
if (fs.existsSync("index.html")) {
  let index = fs.readFileSync("index.html", "utf8");
  index = index.replace(/<title>[^<]*<\/title>/, "<title>Dorj Agent</title>");
  index = index.replace(/<meta name="description" content="[^"]*"/, '<meta name="description" content="Dorj Agent"');
  fs.writeFileSync("index.html", index);
}
fs.writeFileSync("public/site.webmanifest", JSON.stringify({
  name: "Dorj Agent", short_name: "Dorj Agent",
  icons: [
    { src: "/android-chrome-192x192.png", sizes: "192x192", type: "image/png" },
    { src: "/android-chrome-512x512.png", sizes: "512x512", type: "image/png" },
  ],
  theme_color: "#0B1B3A", background_color: "#0B1B3A", display: "standalone"
}, null, 2) + "\n");
console.log("brand: platform D, favicon, title, and manifest applied");
JS
if grep -rn "fonts.googleapis.com" src index.html 2>/dev/null; then echo "Google Fonts reference left" >&2; exit 1; fi
if grep -R "Let's Start Building!" src/root.tsx >/dev/null 2>&1; then echo "old root description left" >&2; exit 1; fi
if grep -q '"name": ""' public/site.webmanifest; then echo "empty webmanifest name left" >&2; exit 1; fi
if grep -R 'OpenHands\|NovaAgent' src/i18n/translation.json >/dev/null; then
  echo "visible old product name left in UI translations" >&2
  exit 1
fi

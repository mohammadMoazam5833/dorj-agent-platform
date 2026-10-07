#!/usr/bin/env bash
# Post-build UI finalizer: remove customer-visible legacy product strings while
# preserving internal protocol/package references.
set -euo pipefail
cd "${1:?usage: finalize-ui.sh <ui-source-dir>}"
node - <<'JS'
const fs = require("fs");
const path = require("path");
const assetsDir = path.join("build", "assets");
if (!fs.existsSync(assetsDir)) throw new Error("build/assets not found; run the UI build first");

const preserved = [
  "X-OpenHands-Client-Version",
  "X-OpenHands-Client",
  "X-OpenHands-Telemetry-Distinct-Id",
  "github.com/OpenHands/",
  "github:OpenHands/",
  "cdn.jsdelivr.net/gh/OpenHands/",
];
const applyProductBrand = (value) => {
  let output = value;
  preserved.forEach((token, index) => {
    output = output.replaceAll(token, `__DORJ_PRESERVE_${index}__`);
  });
  output = output
    .replace(/OpenHands Provider LLM Key/g, "Dorj Agent API Key")
    .replace(/OpenHands Cloud/g, "Dorj Agent")
    .replace(/OpenHands/g, "Dorj Agent")
    .replace(/NovaAgent/g, "Dorj Agent");
  preserved.forEach((token, index) => {
    output = output.replaceAll(`__DORJ_PRESERVE_${index}__`, token);
  });
  return output;
};

let assetCount = 0;
for (const name of fs.readdirSync(assetsDir)) {
  if (!name.endsWith(".js")) continue;
  const file = path.join(assetsDir, name);
  const original = fs.readFileSync(file, "utf8");
  const updated = applyProductBrand(original);
  if (updated !== original) {
    fs.writeFileSync(file, updated);
    assetCount++;
  }
}

let localeCount = 0;
const localesDir = path.join("build", "locales");
if (fs.existsSync(localesDir)) {
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const file = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(file);
      else if (entry.isFile() && entry.name.endsWith(".json")) {
        const data = JSON.parse(fs.readFileSync(file, "utf8"));
        const rewrite = (value) => {
          if (typeof value === "string") return applyProductBrand(value);
          if (Array.isArray(value)) return value.map(rewrite);
          if (value && typeof value === "object") {
            return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, rewrite(v)]));
          }
          return value;
        };
        const updated = rewrite(data);
        const serialized = JSON.stringify(updated, null, 2) + "\n";
        if (serialized !== fs.readFileSync(file, "utf8")) {
          fs.writeFileSync(file, serialized);
          localeCount++;
        }
      }
    }
  };
  walk(localesDir);
}

console.log(`finalize-ui: branded ${assetCount} JS asset(s), ${localeCount} locale file(s)`);
JS
if grep -R 'NovaAgent' build/assets build/locales build/index.html build/site.webmanifest >/dev/null 2>&1; then
  echo "NovaAgent remains in customer-visible UI build" >&2
  exit 1
fi
if grep -RIl 'OpenHands\|NovaAgent' build/locales | grep -q .; then
  echo "legacy product name remains in UI locale values" >&2
  exit 1
fi

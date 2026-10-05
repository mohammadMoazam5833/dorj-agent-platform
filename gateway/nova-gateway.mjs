#!/usr/bin/env node
/**
 * NovaAgent reverse-workspace gateway for Kubernetes: runs NovaAgent's own
 * scripts/customer-workspace-gateway.mjs unchanged and keeps its customer registry
 * file (NOVAAGENT_CUSTOMERS_FILE, hot-reloaded by mtime) in sync with nova-control,
 * which owns the registry (Keycloak-authenticated users -> tenant tokens).
 *
 * Env: NOVA_REGISTRY_URL, NOVA_REGISTRY_TOKEN (shared with nova-control), NOVA_REGISTRY_INTERVAL_MS
 */
import { renameSync, writeFileSync, readFileSync } from "node:fs";

const FILE = process.env.NOVAAGENT_CUSTOMERS_FILE || "/tmp/nova-customers.json";
process.env.NOVAAGENT_CUSTOMERS_FILE = FILE;
const URL_ = process.env.NOVA_REGISTRY_URL;
const TOKEN = process.env.NOVA_REGISTRY_TOKEN || "";
const EVERY = Number(process.env.NOVA_REGISTRY_INTERVAL_MS || 5000);

let last = "";
try { last = readFileSync(FILE, "utf8"); } catch { writeFileSync(FILE, '{"customers":[]}\n', { mode: 0o600 }); }

async function sync() {
  try {
    const r = await fetch(URL_, { headers: { Authorization: `Bearer ${TOKEN}` }, signal: AbortSignal.timeout(4000) });
    if (!r.ok) throw new Error(`registry HTTP ${r.status}`);
    const body = JSON.stringify(await r.json());
    if (body !== last) {
      writeFileSync(FILE + ".tmp", body, { mode: 0o600 });
      renameSync(FILE + ".tmp", FILE);
      last = body;
      console.log(`[nova-gateway] registry updated (${JSON.parse(body).customers.length} customers)`);
    }
  } catch (e) {
    console.error(`[nova-gateway] registry sync failed: ${e.message}`); // keep the last good registry
  }
}

if (!URL_) { console.error("[nova-gateway] NOVA_REGISTRY_URL is required"); process.exit(1); }
await sync();
setInterval(sync, EVERY).unref();
// the upstream CLI only starts when it is the entry script (argv[1]); keep it unmodified
const cli = new URL("./scripts/customer-workspace-gateway.mjs", import.meta.url).pathname;
process.argv[1] = cli;
await import(cli);

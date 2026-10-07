# T1 Report — Dorj Agent 1.6.6+nova.1 e2e

- Date: 2026-10-07
- Target: `https://nova.isigpu.local/` (ingress `172.16.50.202`)
- Control version observed live: `0.4.4`
- Test environment: proxy variables unset; `E2E_USER`/`E2E_PASS` absent (checked by presence only)
- Result: **BLOCKED — need E2E_PASS**
- Pass count: **0/8**
- Rule compliance: test-only; no rebuild, redeploy, code change, commit, or tag.

## Summary

`download.spec.js` was the only test that can run without credentials. It failed because the
specification still expects the old `NovaAgent-*` artifact names, while the live page and published
artifacts are correctly named `Dorj-Agent-Client-*`. After that, all remaining tests were stopped
because they require `E2E_USER` and `E2E_PASS`.

## Results

| # | Test | Result | Duration | Evidence / key log |
|---:|---|---|---:|---|
| 1 | `download.spec.js` | **FAIL** | 14.20s | `download.spec.js:10:42` expected `#dl-main` href `/NovaAgent-...`; received `Dorj-Agent-Client-1.6.6+nova.1-amd64.deb` |
| 2 | `server.spec.js` | **BLOCKED** | 0s | Not run: needs `E2E_PASS` |
| 3 | `cli.spec.js` | **BLOCKED** | 0s | Not run: needs `E2E_PASS`; also needs `NOVA_PY` with httpx |
| 4 | `admin.spec.js` | **BLOCKED** | 0s | Not run: needs `E2E_PASS` |
| 5 | `identity.spec.js` | **BLOCKED** | 0s | Not run: needs `E2E_PASS` |
| 6 | desktop via `run-desktop.sh` | **BLOCKED** | 0s | Not run: needs `E2E_PASS`; script also expects old `NovaAgent-*-amd64.deb` artifact name |
| 7 | Wayland via `run-wayland.sh` | **BLOCKED** | 0s | Not run: needs `E2E_PASS` |
| 8 | `tunnel-replace.sh` | **BLOCKED** | 0s | Not run: needs `E2E_PASS`; also needs `NOVA_CA_BUNDLE` |

## FAIL analysis: download

**Exact error**

```text
Error: expect(locator).toHaveAttribute expected pattern
/NovaAgent-(Setup-[\d.]+(?:\+nova\.\d+)?-x64\.exe|[\d.]+(?:\+nova\.\d+)?-amd64\.deb|[\d.]+(?:\+nova\.\d+)?-x86_64\.AppImage)$/
Received string: "Dorj-Agent-Client-1.6.6+nova.1-amd64.deb"
```

**Observed page state**

- Page URL: `/download/`
- Page title/branding: Dorj Agent
- Version: `1.6.6+nova.1`
- Main download link on Linux: `Dorj-Agent-Client-1.6.6+nova.1-amd64.deb`
- Windows installer link: `Dorj-Agent-Client-Setup-1.6.6+nova.1-x64.exe`
- AppImage link: `Dorj-Agent-Client-1.6.6+nova.1-x86_64.AppImage`

**Root-cause guess**

The downloaded page/publishing pipeline was migrated from NovaAgent to Dorj Agent, but
`e2e/download.spec.js` was not migrated. Its assertion still matches only `NovaAgent-*` file names.
This is an outdated e2e assertion, not evidence that the artifacts are missing or corrupt.

**Proposed fix**

Update `download.spec.js` to accept the current Dorj artifact contract, for example:

```js
/Dorj-Agent-Client-(?:Setup-)?1\.6\.6\+nova\.1-(?:x64\.exe|amd64\.deb|x86_64\.AppImage)$/
```

Then re-run the full SHA256SUMS verification. For longer-term resilience, make the regex generic:
match any `Dorj-Agent-Client-*` setup/deb/AppImage artifact and assert the exact version from the
page, rather than hard-coding `1.6.6+nova.1`.

## Additional compatibility risks found (not fixed)

1. `e2e/run-desktop.sh` still selects `../desktop/dist/NovaAgent-*-amd64.deb`; current artifact is
   `Dorj-Agent-Client-1.6.6+nova.1-amd64.deb`. It will fail before installing the client.
2. `e2e/run-wayland.sh` reports installed package name as `novaagent-client`; the rebuilt package
   identity is `dorj-agent-client`. Verify this line and any assertions when credentials are
   available.
3. `NOVA_CA_BUNDLE` and `NOVA_PY` are also not present in the current task environment, so CLI and
   tunnel tests cannot be run even after only `E2E_PASS` is supplied.

## Required before full T1 rerun

- `E2E_USER` / `E2E_PASS` from the owner
- `NOVA_CA_BUNDLE=/workdir/nova-platform/desktop/org-root-ca.crt`
- `NOVA_PY` set to a Python interpreter with `httpx`

No credentials were guessed, printed, logged, or stored.


## T1b update — e2e installer-name compatibility (owner-approved)

Date: 2026-10-07  
Scope: e2e-only update; `identity.spec.js` was intentionally left unchanged pending the owner’s branding decision.  
No rebuild, redeploy, commit, tag, or other test was run.

### Changes

- `e2e/download.spec.js` now accepts both current `Dorj-Agent-Client-*` artifacts and legacy
  `NovaAgent-*` artifacts.
- `e2e/run-desktop.sh` selects `Dorj-Agent-Client-*-amd64.deb` first and falls back to
  `NovaAgent-*-amd64.deb`; it exits with a clear error if neither exists.
- `e2e/run-wayland.sh` uses the same backward-compatible deb selection when no explicit path is passed.
- Syntax checks passed for both updated shell scripts (`bash -n`), and `git diff --check` was clean.

### Diff

```diff
diff --git a/e2e/download.spec.js b/e2e/download.spec.js
index 4c28547..629b9f4 100644
--- a/e2e/download.spec.js
+++ b/e2e/download.spec.js
@@ -7,7 +7,7 @@ test('download page: public, every artifact listed in SHA256SUMS and intact', as
   const res = await page.goto(N + '/download/')
   expect(res.status()).toBe(200)
   expect(new URL(page.url()).pathname).toBe('/download/')            // not bounced to the realm login
-  await expect(page.locator('#dl-main')).toHaveAttribute('href', /NovaAgent-(Setup-[\d.]+(?:\+nova\.\d+)?-x64\.exe|[\d.]+(?:\+nova\.\d+)?-amd64\.deb|[\d.]+(?:\+nova\.\d+)?-x86_64\.AppImage)$/)
+  await expect(page.locator('#dl-main')).toHaveAttribute('href', /(?:Dorj-Agent-Client|NovaAgent)-(Setup-[\d.]+(?:\+nova\.\d+)?-x64\.exe|[\d.]+(?:\+nova\.\d+)?-amd64\.deb|[\d.]+(?:\+nova\.\d+)?-x86_64\.AppImage)$/)
   expect(await page.content()).not.toContain('{{')
   const fontOk = await page.evaluate(async () => { await document.fonts.ready; return document.fonts.check('16px Vazirmatn', 'سلام') })
   expect(fontOk).toBe(true)
diff --git a/e2e/run-desktop.sh b/e2e/run-desktop.sh
index 41e0dc5..b4faea7 100755
--- a/e2e/run-desktop.sh
+++ b/e2e/run-desktop.sh
@@ -5,7 +5,11 @@
 set -euo pipefail
 cd "$(dirname "$0")"
 SHOTS=$(realpath "${1:-.}")
-DEB=$(ls ../desktop/dist/NovaAgent-*-amd64.deb | tail -1)
+DEB=$(ls ../desktop/dist/Dorj-Agent-Client-*-amd64.deb ../desktop/dist/NovaAgent-*-amd64.deb 2>/dev/null | tail -1 || true)
+if [[ -z "$DEB" ]]; then
+  echo "No Dorj-Agent-Client-*-amd64.deb or NovaAgent-*-amd64.deb found in ../desktop/dist" >&2
+  exit 1
+fi
 IP=${NOVA_INGRESS_IP:-172.16.50.202}
 # /dev/shm read-only: emulates guests where Chromium cannot create shared memory there (VMware Ubuntu
 # report 2026-10-05) - the packaged app must start anyway (--disable-dev-shm-usage)
diff --git a/e2e/run-wayland.sh b/e2e/run-wayland.sh
index 3bd49dd..4cc9b5b 100755
--- a/e2e/run-wayland.sh
+++ b/e2e/run-wayland.sh
@@ -4,7 +4,12 @@
 #   NOVA_EXPECT=stuck, NOVAAGENT_TEST_IGNORE_READY_TO_SHOW=1)
 set -euo pipefail
 cd "$(dirname "$0")"
-DEB=$(realpath "${1:-$(ls ../desktop/dist/NovaAgent-*-amd64.deb | tail -1)}")
+DEFAULT_DEB=$(ls ../desktop/dist/Dorj-Agent-Client-*-amd64.deb ../desktop/dist/NovaAgent-*-amd64.deb 2>/dev/null | tail -1 || true)
+if [[ -z "$DEFAULT_DEB" ]]; then
+  echo "No Dorj-Agent-Client-*-amd64.deb or NovaAgent-*-amd64.deb found in ../desktop/dist" >&2
+  exit 1
+fi
+DEB=$(realpath "${1:-$DEFAULT_DEB}")
 SHOTS=$(realpath "${2:-.}")
 IP=${NOVA_INGRESS_IP:-172.16.50.202}
 docker run --rm --tmpfs /dev/shm:ro,size=1m -e HTTPS_PROXY -e HTTP_PROXY -e E2E_USER -e E2E_PASS \
```

### download.spec.js rerun result

Command (proxy variables unset):

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
  NO_PROXY='*' no_proxy='*' npx playwright test download.spec.js --reporter=line
```

Result: **PASS — 1 passed (5.9s)**

This validates the public download page and every SHA256SUMS-listed artifact against its live checksum.
No credentials were supplied or used. Remaining T1 tests remain blocked pending `E2E_PASS` and the
missing optional environment described above.

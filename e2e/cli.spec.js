// `nova` CLI live e2e: real Dex device-flow login (approved in the browser), then whoami/models/usage/run.
// Needs: E2E_USER/E2E_PASS, NOVA_CA_BUNDLE (internal root CA), NOVA_PY (python with httpx; default python3).
import { test, expect } from '@playwright/test'
import { spawn, spawnSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

const CLI_DIR = path.resolve(__dirname, '../cli')
const PY = process.env.NOVA_PY || 'python3'
const cfg = mkdtempSync(path.join(tmpdir(), 'nova-cli-e2e-'))
const env = { ...process.env, PYTHONPATH: CLI_DIR, NOVA_ISSUER: process.env.NOVA_ISSUER || 'https://identity.isigpu.local/realms/nova', PYTHONUNBUFFERED: '1', NOVA_CONFIG_DIR: cfg,
  NOVA_RESOLVE: process.env.NOVA_RESOLVE || 'nova.isigpu.local=172.16.50.202' }
for (const k of ['HTTPS_PROXY', 'HTTP_PROXY', 'https_proxy', 'http_proxy', 'ALL_PROXY', 'all_proxy']) delete env[k]
const nova = (...args) => spawnSync(PY, ['-m', 'nova_cli.main', ...args], { env, encoding: 'utf8', timeout: 600_000 })

test.afterAll(() => rmSync(cfg, { recursive: true, force: true }))

test('nova CLI: device login, whoami, models, usage, run', async ({ page }) => {
  expect(process.env.NOVA_CA_BUNDLE, 'NOVA_CA_BUNDLE must point at the internal root CA').toBeTruthy()

  // 1. `nova login` prints the verification URL, then polls Dex until the browser approves it
  const login = spawn(PY, ['-m', 'nova_cli.main', 'login'], { env })
  let out = ''
  login.stdout.on('data', d => { out += d })
  login.stderr.on('data', d => { out += d })
  const done = new Promise(res => login.on('exit', code => res(code)))
  await expect.poll(() => out, { timeout: 30_000 }).toMatch(/https:\/\/\S+\/device\S*/)
  const url = out.match(/https:\/\/\S+\/device\S*/)[0]

  await page.goto(url)
  if (await page.locator('input[name=device_user_code], input[name=user_code]').count()) await page.locator('button[type=submit], input[type=submit]').first().click()
  const pw = page.locator('input[type="password"]:visible').first()
  await pw.waitFor({ timeout: 30_000 })
  const form = page.locator('form', { has: pw })
  await form.locator('input:visible:not([type="password"]):not([type="hidden"]):not([type="checkbox"])').first().fill(process.env.E2E_USER)
  await pw.fill(process.env.E2E_PASS)
  await page.waitForFunction(u => { const h = document.getElementById('username'); return !h || h.type !== 'hidden' || h.value.startsWith(u.split('@')[0] + '@') }, process.env.E2E_USER)
  await pw.press('Enter')
  // Keycloak's device-grant consent screen ("Do you grant these access privileges?")
  const grant = page.locator('[name="accept"], #kc-login, button:has-text("Yes")').first()
  if (await grant.waitFor({ timeout: 10_000 }).then(() => true, () => false)) await grant.click()

  expect(await done, out).toBe(0)
  expect(out).toMatch(/vared shodid be onvan-e \S+@/)

  // 2. read-only commands, JSON mode for scripts/CI
  const me = nova('--json', 'whoami')
  expect(me.status, me.stderr).toBe(0)
  const email = JSON.parse(me.stdout).email
  expect(email).toMatch(new RegExp('^' + process.env.E2E_USER.split('@')[0] + '@', 'i'))

  const models = JSON.parse(nova('--json', 'models').stdout)
  expect(models.models).toContain(models.default)

  const usage = JSON.parse(nova('--json', 'usage').stdout)
  expect(usage.user).toBe(email)
  expect(usage.llm.available).toBe(true)
  expect(typeof usage.llm.spend_usd).toBe('number')

  // 3. a real agent task in the user's own workspace
  const run = nova('run', 'Create a file nova_e2e.txt containing exactly the word pong, then reply DONE.', '--timeout', '540')
  expect(run.status, run.stdout + run.stderr).toBe(0)
  expect(run.stderr).toMatch(/\[(finished|idle)\] \S+\/conversations\/[0-9a-f-]+/)

  // 4. logout drops the stored credentials: the next call is an auth error (exit 3)
  expect(nova('logout').status).toBe(0)
  expect(nova('whoami').status).toBe(3)
})

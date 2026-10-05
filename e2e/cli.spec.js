// nova CLI against the live server: sign in like the desktop app (username/password -> tenant token),
// whoami/models/usage, a real agent task, logout -> auth error (exit 3).
// Needs: E2E_USER/E2E_PASS, NOVA_CA_BUNDLE (org root CA), NOVA_PY (python with httpx; default python3).
import { test, expect } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

const CLI_DIR = path.resolve(__dirname, '../cli')
const PY = process.env.NOVA_PY || 'python3'
const cfg = mkdtempSync(path.join(tmpdir(), 'nova-cli-e2e-'))
const env = { ...process.env, PYTHONPATH: CLI_DIR, PYTHONUNBUFFERED: '1', NOVA_CONFIG_DIR: cfg,
  NOVA_RESOLVE: process.env.NOVA_RESOLVE || 'nova.isigpu.local=172.16.50.202' }
for (const k of ['HTTPS_PROXY', 'HTTP_PROXY', 'https_proxy', 'http_proxy', 'ALL_PROXY', 'all_proxy']) delete env[k]
const nova = (args, extra = {}) => spawnSync(PY, ['-m', 'nova_cli.main', ...args], { env: { ...env, ...extra }, encoding: 'utf8', timeout: 600_000 })

test.afterAll(() => rmSync(cfg, { recursive: true, force: true }))

test('nova CLI: sign in, whoami, models, usage, run, logout', async () => {
  expect(process.env.NOVA_CA_BUNDLE, 'NOVA_CA_BUNDLE must point at the org root CA').toBeTruthy()
  expect(nova(['login', '-u', process.env.E2E_USER], { NOVA_PASSWORD: 'wrong' }).status).toBe(3)
  const login = nova(['login', '-u', process.env.E2E_USER], { NOVA_PASSWORD: process.env.E2E_PASS })
  expect(login.status, login.stdout + login.stderr).toBe(0)
  expect(login.stdout).toMatch(/vared shodid be onvan-e \S+@/)

  const me = nova(['--json', 'whoami'])
  expect(me.status, me.stderr).toBe(0)
  expect(JSON.parse(me.stdout).email).toMatch(new RegExp('^' + process.env.E2E_USER.split('@')[0] + '@', 'i'))
  const models = JSON.parse(nova(['--json', 'models']).stdout)
  expect(models.models).toContain(models.default)
  const usage = JSON.parse(nova(['--json', 'usage']).stdout)
  expect(usage.llm.available).toBe(true)

  const run = nova(['run', 'Create a file nova_cli_e2e.txt containing exactly the word pong, then reply DONE.', '--timeout', '540'])
  expect(run.status, run.stdout + run.stderr).toBe(0)
  expect(run.stderr).toMatch(/\[(finished|idle)\] \S+\/conversations\/[0-9a-f-]+/)

  expect(nova(['logout']).status).toBe(0)
  expect(nova(['whoami']).status).toBe(3)
})

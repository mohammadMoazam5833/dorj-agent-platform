// dorj-agent CLI against the live server: sign in like the desktop app (username/password ->
// tenant token), whoami/models/usage, a real agent task with a reverse workspace bridge, logout.
// Needs: E2E_USER/E2E_PASS, NOVA_CA_BUNDLE (org root CA), NOVA_PY (python with httpx; default python3).
import { test, expect } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

const CLI_DIR = path.resolve(__dirname, '../cli')
const PY = process.env.NOVA_PY || 'python3'
const cfg = mkdtempSync(path.join(tmpdir(), 'dorj-cli-e2e-'))
const work = mkdtempSync(path.join(tmpdir(), 'dorj-cli-work-'))
const env = { ...process.env, LANG: 'fa_IR.UTF-8', PYTHONPATH: CLI_DIR, PYTHONUNBUFFERED: '1',
  NOVA_CONFIG_DIR: cfg,
  NOVA_RESOLVE: process.env.NOVA_RESOLVE || 'nova.isigpu.local=172.16.50.202' }
for (const k of ['HTTPS_PROXY', 'HTTP_PROXY', 'https_proxy', 'http_proxy', 'ALL_PROXY', 'all_proxy']) delete env[k]
const nova = (args, extra = {}) => spawnSync(PY, ['-m', 'dorj_agent_cli.main', ...args],
  { cwd: work, env: { ...env, ...extra }, encoding: 'utf8', timeout: 600_000 })

test.afterAll(() => {
  rmSync(cfg, { recursive: true, force: true })
  rmSync(work, { recursive: true, force: true })
})

test('dorj-agent CLI: auth, models, usage, reverse-workspace run, logout', async () => {
  expect(process.env.NOVA_CA_BUNDLE, 'NOVA_CA_BUNDLE must point at the org root CA').toBeTruthy()
  expect(nova(['login', '-u', process.env.E2E_USER], { NOVA_PASSWORD: 'wrong' }).status).toBe(3)
  const login = nova(['login', '-u', process.env.E2E_USER], { NOVA_PASSWORD: process.env.E2E_PASS })
  expect(login.status, login.stdout + login.stderr).toBe(0)
  expect(login.stdout).toMatch(/وارد شدید/)

  const me = nova(['--json', 'whoami'])
  expect(me.status, me.stderr).toBe(0)
  expect(JSON.parse(me.stdout).email).toMatch(new RegExp('^' + process.env.E2E_USER.split('@')[0] + '@', 'i'))
  const models = JSON.parse(nova(['--json', 'models']).stdout)
  expect(models.models).toContain(models.default)
  const usage = JSON.parse(nova(['--json', 'usage']).stdout)
  expect(usage.llm.available).toBe(true)

  const filePath = path.join(work, 'nova_cli_e2e.txt')
  const run = nova(['run', 'Create a file nova_cli_e2e.txt in the current directory containing exactly the word pong, then reply DONE.', '--timeout', '540'])
  expect(run.status, `${run.stdout}\n${run.stderr}`).toBe(0)
  expect(run.stderr).toMatch(/ابزارهای فضای کاری/)
  expect(run.stderr).toMatch(/\[(finished|idle)\] \S+\/conversations\/[0-9a-f-]+/)
  expect(existsSync(filePath), `local file was not created: ${run.stdout}\n${run.stderr}`).toBe(true)
  expect(readFileSync(filePath, 'utf8')).toBe('pong')

  expect(nova(['logout']).status).toBe(0)
  expect(nova(['whoami']).status).toBe(3)
})

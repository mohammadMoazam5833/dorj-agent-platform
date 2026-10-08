import { chromium } from '../../e2e/node_modules/@playwright/test/index.mjs'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'

const root = path.resolve('..', 'nova-platform')
const src = readFileSync(path.join(root, 'control', 'admin-src', 'nova-admin.mjs'), 'utf8')
const a = src.indexOf('const PAGE = `') + 'const PAGE = `'.length
const b = src.indexOf('`;\n', a)
const html = src.slice(a, b).replace(/\\([\s\S])/gs, (m, c) => c === 'n' ? '\n' : c === 't' ? '\t' : '\\' + c)
  .replace('__FAVICON__', 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==')

const services = Array.from({ length: 22 }, (_, i) => {
  const n = String(i + 1).padStart(2, '0')
  return { unit: `service-${n}`, active: i % 5 === 4 ? 'inactive' : 'active', upSec: 3600 * (i + 1) }
})
const tenants = Array.from({ length: 22 }, (_, i) => {
  const n = String(i + 1).padStart(2, '0')
  return { name: i ? `tenant-${n}` : 'default', port: i ? `ws-tenant-${n}` : '-', active: i % 6 === 5 ? 'inactive' : 'active', clientConnected: i % 3 !== 2, agentOk: i % 4 !== 3, convCount: i + 1, size: 1024 * 1024 * (i + 3), sizeText: `${i + 3}.0 MB`, hasPortalUser: true }
})
const conversations = Array.from({ length: 22 }, (_, i) => ({
  id: `conv-${String(i + 1).padStart(2, '0')}`,
  title: `Conversation ${String(i + 1).padStart(2, '0')}`,
  created_at: Date.now() - (i + 1) * 86_400_000,
  updated_at: Date.now() - i * 3_600_000,
  status: i % 3 ? 'idle' : 'running'
}))
const logs = Array.from({ length: 25 }, (_, i) => `2026-10-08T10:${String(i).padStart(2, '0')}:00 nova-control log line ${i + 1}`).join('\n')
const overview = {
  services, uptime: 86_400, uptimeText: '۱ روز', mem: { total: 32 * 1024 ** 3, free: 12 * 1024 ** 3 },
  load: [0.42, 0.4, 0.38], cpuCount: 32, disk: { total: 1024 ** 4, used: 512 * 1024 ** 3 },
  gateway: { customers: tenants.slice(0, 5).map(t => ({ name: t.name, connected: t.clientConnected })) }, hostname: 'local-evidence', now: Date.now()
}

mkdirSync(path.join(root, 'tasks', 'T14-evidence'), { recursive: true })
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const context = await browser.newContext({ locale: 'fa-IR', viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 })
const page = await context.newPage()
const errors = []
page.on('pageerror', e => errors.push(e.message))
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()) })

await page.route('**/*', route => {
  const u = new URL(route.request().url())
  const p = u.pathname.startsWith('/admin/api/') ? u.pathname.slice('/admin'.length) : u.pathname
  const body = (value) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) })
  if (p === '/admin' || u.pathname === '/admin/') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: html })
  if (p === '/api/overview' || u.pathname.startsWith('/api/overview?')) return body(overview)
  if (p === '/api/disk-usage') return body({ items: [{ dir: '/var/lib/docker', bytes: 50 * 1024 ** 3, sizeText: '50.0 GB' }, { dir: '/var/log', bytes: 2 * 1024 ** 3, sizeText: '2.0 GB' }] })
  if (p === '/api/llm') return body({ local: { ok: true, ms: 10, models: ['qwen3'] }, remote: { ok: true, ms: 80, models: ['qwen3', 'deepseek-v3'] } })
  if (p === '/api/tenants' || u.pathname.startsWith('/api/tenants?')) return body({ tenants })
  if (p === '/api/conversations' || u.pathname.startsWith('/api/conversations?')) return body({ conversations })
  if (p === '/api/logs' || u.pathname.startsWith('/api/logs?')) return body({ lines: logs })
  return route.fulfill({ status: 404, body: 'not found' })
})

await page.goto('https://local.admin/admin?fixture=1')
const servicesTable = page.locator('[data-table-id="services"]')
await expectFlush(servicesTable)
await page.screenshot({ path: path.join(root, 'tasks', 'T14-evidence', 'fa-dashboard.png'), fullPage: true })

// Filter, sort, and paginate the dashboard service table.
const before = await servicesTable.locator('[data-table-meta]').innerText()
await servicesTable.locator('[data-table-filter]').fill('service-1')
await page.waitForTimeout(100)
const filtered = await servicesTable.locator('[data-table-meta]').innerText()
const firstAsc = await servicesTable.locator('tbody tr td').first().innerText()
await servicesTable.locator('[data-table-sort][data-key="unit"]').click()
await page.waitForTimeout(100)
const firstDesc = await servicesTable.locator('tbody tr td').first().innerText()
await servicesTable.locator('[data-table-filter]').fill('')
await servicesTable.locator('[data-table-page="next"]').click()
await page.waitForTimeout(100)
const pager = await servicesTable.locator('[data-table-page-label]').innerText()
await page.screenshot({ path: path.join(root, 'tasks', 'T14-evidence', 'fa-dashboard-page2.png'), fullPage: true })
if (before === filtered) throw new Error('service filter did not reduce rows')
if (firstAsc === firstDesc) throw new Error('service sort did not change order')
if (!pager.includes('۲')) throw new Error(`unexpected pager: ${pager}`)

await page.locator('.tab[data-t="tenants"]').click()
const tenantsTable = page.locator('[data-table-id="tenants"]')
await expectFlush(tenantsTable)
await page.screenshot({ path: path.join(root, 'tasks', 'T14-evidence', 'fa-tenants.png'), fullPage: true })

await page.locator('.tab[data-t="convs"]').click()
const convsTable = page.locator('[data-table-id="convs"]')
await expectFlush(convsTable)
await page.screenshot({ path: path.join(root, 'tasks', 'T14-evidence', 'fa-conversations.png'), fullPage: true })

await page.locator('.tab[data-t="logs"]').click()
const logsTable = page.locator('[data-table-id="logs"]')
await expectFlush(logsTable)
await page.screenshot({ path: path.join(root, 'tasks', 'T14-evidence', 'fa-logs.png'), fullPage: true })

const summary = { before, filtered, firstAsc, firstDesc, pager, errors }
writeFileSync(path.join(root, 'tasks', 'T14-evidence', 'local-checks.json'), JSON.stringify(summary, null, 2) + '\n')
if (errors.length) throw new Error(`browser errors: ${errors.join('\n')}`)
await browser.close()

async function expectFlush(table) {
  await table.locator('[data-table-filter]').waitFor({ timeout: 10_000 })
  await page.waitForTimeout(250)
}

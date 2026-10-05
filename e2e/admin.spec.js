// NovaAgent admin panel (NovaAgent's own nova-admin UI, served by nova-control on Kubernetes):
// organisation sign-in (group nova-admins), dashboard services, users with their workspace +
// connected app, conversations, logs, LLM status; wrong password refused; API closed without session.
import { test, expect } from '@playwright/test'
import path from 'node:path'

const N = 'https://nova.isigpu.local'
const SHOTS = process.env.NOVA_SHOTS || '.'

test('admin panel: sign in, dashboard, users, conversations, logs', async ({ page }) => {
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  await page.setViewportSize({ width: 1440, height: 900 })

  expect((await page.request.get(N + '/admin/api/tenants', { failOnStatusCode: false }).catch(() => null))?.status() ?? 401).not.toBe(200)
  await page.goto(N + '/admin')
  await page.locator('#un').fill(process.env.E2E_USER)
  await page.locator('#pw').fill('definitely-wrong')
  await page.locator('#pw').press('Enter')
  await expect(page.getByText('نام کاربری یا گذرواژه نادرست است')).toBeVisible({ timeout: 20_000 })

  await page.locator('#un').fill(process.env.E2E_USER)
  await page.locator('#pw').fill(process.env.E2E_PASS)
  await page.locator('#pw').press('Enter')

  // dashboard: Kubernetes services incl. the gateway and the users' workspaces
  await expect(page.locator('.tab[data-t="dash"]')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('nova-gateway').first()).toBeVisible({ timeout: 30_000 })
  await page.waitForTimeout(1500)
  await page.screenshot({ path: path.join(SHOTS, 'nova-admin-dash.png'), fullPage: true })

  // users: the signed-in admin is a tenant with a workspace
  await page.locator('.tab[data-t="tenants"]').click()
  const me = process.env.E2E_USER.split('@')[0]
  await expect(page.locator('tbody tr', { hasText: me }).first()).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('خروج دستگاه‌ها').first()).toBeVisible()
  await page.screenshot({ path: path.join(SHOTS, 'nova-admin-tenants.png'), fullPage: true })

  // conversations of that user, logs of the control plane
  await page.locator('.tab[data-t="convs"]').click()
  await page.waitForTimeout(2500)
  await page.screenshot({ path: path.join(SHOTS, 'nova-admin-convs.png'), fullPage: true })
  const convs = await page.evaluate(async (t) => (await fetch('/admin/api/conversations?tenant=' + t)).status, me)
  expect([200, 502]).toContain(convs)
  await page.locator('.tab[data-t="logs"]').click()
  await page.waitForTimeout(2500)
  const logs = await page.evaluate(async () => (await (await fetch('/admin/api/logs?unit=nova-control&lines=20')).json()).lines || '')
  expect(logs.length).toBeGreaterThan(0)

  const llm = await page.evaluate(async () => (await fetch('/admin/api/llm')).json())
  expect(llm.remote.ok).toBe(true)
  expect(llm.local.models).toEqual(expect.arrayContaining(['qwen3']))
  expect(errors, errors.join('\n')).toEqual([])
})

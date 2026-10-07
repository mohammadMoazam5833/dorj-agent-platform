// Brand checks for every customer-facing page on nova.isigpu.local: the product is Dorj Agent
// («دستیار دُرج») and no NovaAgent/OpenHands branding reaches the title or visible body text.
// This intentionally checks what a customer sees, not internal protocol names in JS/source.
import { test, expect } from '@playwright/test'

const N = 'https://nova.isigpu.local'
const OLD_BRAND = /NovaAgent|OpenHands/i

async function expectDorjBrand(page, label) {
  const brand = await page.evaluate(() => ({ title: document.title, text: document.body.innerText }))
  const visible = `${brand.title}\n${brand.text}`
  expect(brand.title, `${label} title`).toMatch(/Dorj Agent/i)
  expect(brand.text, `${label} visible product name`).toMatch(/Dorj Agent|دستیار دُرج/i)
  expect(visible, `${label} must not expose old product branding`).not.toMatch(OLD_BRAND)
}

test('public pages are branded Dorj Agent and redirect browsers to downloads', async ({ page }) => {
  await page.goto(N + '/')
  expect(new URL(page.url()).pathname).toBe('/download/')
  await expect(page.locator('body')).toBeVisible()
  await expectDorjBrand(page, 'download')

  await page.goto(N + '/download/')
  await expect(page.locator('body')).toBeVisible()
  await expectDorjBrand(page, 'download direct')
})

test('admin login page is branded Dorj Agent', async ({ page }) => {
  await page.goto(N + '/admin')
  await expect(page.locator('#un')).toBeVisible({ timeout: 30_000 })
  await expectDorjBrand(page, 'admin login')
})

test('authenticated UI bundle is branded Dorj Agent', async ({ page }) => {
  // Establish the company origin first so the relative login API uses the real host, not about:blank.
  await page.goto(N + '/download/')
  const login = await page.evaluate(async ({ user, pass }) => {
    const r = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: user, password: pass }),
    })
    return { status: r.status, json: await r.json().catch(() => null) }
  }, { user: process.env.E2E_USER, pass: process.env.E2E_PASS })
  expect(login.status).toBe(200)
  expect(login.json?.token).toBeTruthy()

  await page.goto(`${N}/?t=${login.json.token}`)
  await expect(page.locator('[contenteditable="true"]').first()).toBeVisible({ timeout: 120_000 })
  await expectDorjBrand(page, 'UI bundle')
})

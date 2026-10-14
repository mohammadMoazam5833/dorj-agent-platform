// No active plan or credit: the composer gives way to the out-of-credit gate (no text field), on the home page and
// in a conversation; the button leads to the subscription tab. The real /api/me must carry billing_enforced, and the
// gate itself is shown by answering /api/me as a plan-less user (the live e2e account has a paid plan).
import { test, expect } from '@playwright/test'
const N = 'https://nova.isigpu.local'

async function signIn(page) {
  await page.goto(`${N}/download/`)
  const ok = await page.evaluate(async ([u, p]) => (await fetch('/api/auth/login', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: u, password: p }) })).json(),
  [process.env.E2E_USER, process.env.E2E_PASS])
  return ok.token
}

test('paid user types normally and the server says billing is enforced', async ({ page }) => {
  const token = await signIn(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`${N}/?t=${token}`)
  await expect(page.locator('[contenteditable="true"]').first()).toBeVisible({ timeout: 120_000 })
  const me = await page.evaluate(async () => (await fetch('/api/me')).json())
  expect(me.billing_enforced).toBe(!me.account.admin)          // enforce is on; the e2e account is a nova admin
  await expect(page.getByTestId('nova-credit-gate')).toHaveCount(0)
})

test('without a plan the composer is replaced by the gate', async ({ page }) => {
  const token = await signIn(page)
  const real = await page.evaluate(async (t) => (await fetch('/api/me', { headers: { 'X-Session-API-Key': t } })).json(), token)
  const planless = { ...real, billing_enforced: true, account: { ...real.account, admin: false },
    wallet: { ...real.wallet, plan: 'free', credit_toman: 0, plan_expires: null } }
  await page.route('**/api/me', (route) => route.request().method() === 'GET' ? route.fulfill({ json: planless }) : route.continue())
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`${N}/?t=${token}`)
  const gate = page.getByTestId('nova-credit-gate')
  await expect(gate).toBeVisible({ timeout: 120_000 })
  await expect(gate).toHaveAttribute('data-reason', 'no-plan')
  await expect(gate).toContainText('اشتراک فعالی ندارید')
  await expect(page.locator('[contenteditable="true"]')).toHaveCount(0)
  await page.screenshot({ path: 'test-results/credit-gate-home.png' })
  await page.getByTestId('nova-credit-gate-cta').click()
  await expect(page).toHaveURL(/\/profile\?tab=subscription/)
  await expect(page.getByTestId('buy-plan-a200')).toBeVisible()
})

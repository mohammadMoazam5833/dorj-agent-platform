// Public download page (no sign-in): links resolve, sizes match SHA256SUMS entries, checksums verify.
import { test, expect } from '@playwright/test'

const N = 'https://nova.isigpu.local'

test('download page: public, every artifact listed in SHA256SUMS and intact', async ({ page }) => {
  const res = await page.goto(N + '/download/')
  expect(res.status()).toBe(200)
  expect(new URL(page.url()).pathname).toBe('/download/')            // not bounced to the realm login
  await expect(page.locator('#dl-main')).toHaveAttribute('href', /NovaAgent-(Setup-[\d.]+-x64\.exe|[\d.]+-amd64\.deb|[\d.]+-x86_64\.AppImage)$/)
  expect(await page.content()).not.toContain('{{')
  const fontOk = await page.evaluate(async () => { await document.fonts.ready; return document.fonts.check('16px Vazirmatn', 'سلام') })
  expect(fontOk).toBe(true)
  await page.screenshot({ path: process.env.NOVA_DL_SHOT || 'nova-download.png', fullPage: false })

  // fetched in the page (same network path as a user's browser), hashed with WebCrypto
  const sums = (await page.evaluate(async () => (await fetch('SHA256SUMS')).text())).trim().split('\n').map(l => l.split(/\s+/))
  expect(sums.map(s => s[1])).toEqual(expect.arrayContaining([
    expect.stringMatching(/\.exe$/), expect.stringMatching(/\.deb$/), expect.stringMatching(/\.AppImage$/), expect.stringMatching(/\.whl$/), 'isigpu-root-ca.crt']))
  for (const [sha, name] of sums) {
    const got = await page.evaluate(async n => {
      const r = await fetch(n)
      const d = await crypto.subtle.digest('SHA-256', await r.arrayBuffer())
      return { status: r.status, sha: [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, '0')).join('') }
    }, name)
    expect(got.status, name).toBe(200)
    expect(got.sha, name).toBe(sha)
  }
  for (const a of ['#alt-win', '#alt-deb', '#alt-appimage']) {
    const href = await page.locator(a).getAttribute('href')
    expect(sums.map(s => s[1])).toContain(href)
  }
})

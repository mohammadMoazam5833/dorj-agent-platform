// The agent presents itself as Dorj Agent («دستیار دُرج») from SOUL.md + self-documentation,
// never as the upstream framework - asked in English and in Persian through the real API.
import { test, expect } from '@playwright/test'

const N = 'https://nova.isigpu.local'

async function ask(page, token, text) {
  return page.evaluate(async ([tok, text]) => {
    const h = { 'Content-Type': 'application/json', 'X-Session-API-Key': tok }
    const j = async (r) => r.json()
    const st = await j(await fetch('/api/settings', { headers: { ...h, 'X-Expose-Secrets': 'encrypted' } }))
    const conv = await j(await fetch('/api/conversations', { method: 'POST', headers: h, body: JSON.stringify({
      workspace: { kind: 'LocalWorkspace', working_dir: 'workspace/project' }, agent_settings: st.agent_settings, secrets_encrypted: true }) }))
    await fetch(`/api/conversations/${conv.id}/events`, { method: 'POST', headers: h,
      body: JSON.stringify({ role: 'user', run: true, content: [{ type: 'text', text }] }) })
    let ran = false
    for (let i = 0; i < 80; i++) {
      const s = (await j(await fetch(`/api/conversations/${conv.id}`, { headers: h }))).execution_status
      if (s === 'running') ran = true
      if (['finished', 'error', 'stuck'].includes(s) || (ran && s === 'idle')) break
      await new Promise(r => setTimeout(r, 2000))
    }
    const ev = await j(await fetch(`/api/conversations/${conv.id}/events/search?limit=100`, { headers: h }))
    const said = ev.items.filter(e => e.kind === 'MessageEvent' && e.source === 'agent')
      .map(e => (e.llm_message?.content || []).map(c => c.text || '').join(' ')).join('\n')
    const finish = ev.items.filter(e => e.kind === 'ActionEvent' && e.tool_name === 'finish')
      .map(e => JSON.stringify(e.action || {})).join('\n')
    await fetch(`/api/conversations/${conv.id}`, { method: 'DELETE', headers: h })
    return said + '\n' + finish
  }, [token, text])
}

test('agent identity is Dorj Agent (en + fa)', async ({ page }) => {
  test.setTimeout(400_000)
  await page.goto(N + '/download/')
  const login = await page.evaluate(async ([u, p]) => (await (await fetch('/api/auth/login', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: u, password: p }) })).json()),
  [process.env.E2E_USER, process.env.E2E_PASS])
  for (const [q, name] of [
    ['Who are you, and which product or framework are you? Answer in two sentences without using any tools.', 'Dorj Agent'],
    ['تو کی هستی و بر پایه‌ی چه محصولی ساخته شده‌ای؟ بدون استفاده از ابزار، در دو جمله جواب بده.', 'دستیار دُرج'],
  ] as const) {
    const reply = await ask(page, login.token, q)
    console.log('Q:', q.slice(0, 40), '\nA:', reply.slice(0, 300))
    expect(reply).toContain(name)
    expect(reply.toLowerCase()).not.toContain('openhands')
    expect(reply.toLowerCase()).not.toContain('novaagent')
  }
})

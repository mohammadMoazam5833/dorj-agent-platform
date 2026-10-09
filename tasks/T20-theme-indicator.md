# T20 — Global agent theme + thinking indicator at the bottom (owner request 2026-10-09)

Owner: «جای آیکون در هر تفکر باید وقتی تا جواب میاد بره پایین نشون داده بشه شبیه Claude» and
«تم رو مجدد درست طراحی کن، یه رنگ گلوبال که اکثر ایجنت‌ها استفاده می‌کنن».
Owner of this task: GLM. Reviewer/deployer: Claude (CTO). Repo: /workdir/NovaAgent. Do NOT deploy.

## A. Thinking indicator follows the conversation (like Claude / Cursor / ChatGPT)
Today `src/components/conversation-events/chat/messages.tsx` renders `<TypingIndicator />` right
under the user's latest message (`maybeShimmer`), so the agent's tool calls pile up BELOW it.
- Render the indicator ONCE, as the LAST item of the message list, while the agent is running
  (curAgentState RUNNING, also while a tool call is streaming). It must always sit under the newest
  event and move down as new events arrive. Remove the per-user-message placement.
- Hide it as soon as the agent is idle / finished / awaiting confirmation / error.
- Keep the auto-scroll: when the user is at the bottom, new events + the indicator stay in view.
- Indicator look: small sparkle (lucide Sparkles) + shimmering phrase, colours from the theme accent
  (no hard-coded #d97757 / #8b7cf6 in index.css — use var(--oh-accent) and the muted text colour).
- Unit test: indicator is after the last event while running; absent when idle.

## B. Theme: neutral charcoal + one blue accent (the common agent look: Cursor, VS Code Dark Modern,
Copilot, Windsurf, Zed). Replace the "Dorj Graphite" values in `src/themes/color-themes.ts`
(`DORJ_DARK_SCALE`, `DORJ_DARK_HEROUI`, tokens) and the pre-hydration copy in `src/index.css`
(`:root` cool-grey scale and the `--dorj-*` chrome block), keep the key names `dorj-dark` / `dorj-light`
and dark as default. Label: "Dorj Dark" / "Dorj Light".

Dark
| role | value |
|---|---|
| sidebar / left rail (darkest) | #141414 |
| app + chat background | #181818 |
| panels, cards, right panel | #1F1F1F |
| input / raised / hover | #262626 |
| active item / selection | #2E2E2E |
| borders | #2B2B2B (subtle: rgba(255,255,255,.06)) |
| text primary | #E6E6E6 |
| text secondary | #A3A3A3 |
| text muted / placeholders | #6E6E6E |
| accent (primary buttons, focus ring, links, active tab underline, indicator) | #4C8DFF, hover #6AA0FF, on-accent text #FFFFFF |
| success / warning / danger | #3FB950 / #D29922 / #F85149 |

Light
| role | value |
|---|---|
| sidebar | #F7F7F8 |
| app background | #FFFFFF |
| panels | #FAFAFA |
| input / hover | #F2F2F3 |
| borders | #E5E5E7 |
| text primary / secondary / muted | #1F1F1F / #555555 / #8A8A8A |
| accent | #2563EB, hover #1D4ED8 |

Rules
- Map the cool-grey scale (50…975) so every existing component lands on the table above; check
  contrast (text ≥ 4.5:1 on its surface).
- Gold (#E8A317) is no longer a UI colour; keep it only inside the Dorj logo asset.
- Also apply to: desktop `nova-platform/desktop/login-prompt.html` + `loading.html` override blocks,
  download page `NovaAgent/scripts/download-site/index.html`, admin page
  `NovaAgent/scripts/admin/nova-admin.mjs` (same tokens, so the product looks like one app).
- Persian RTL stays as is (Vazirmatn, no letter-spacing).

## Done when
- `npx tsc --noEmit`, eslint on changed files, vitest for touched areas green.
- Screenshots (playwright, local dev server or static) dark + light: home, a conversation with tool
  calls (indicator at the bottom), right panel open, settings. Put them in tasks/T20-evidence/.
- Commit in NovaAgent (and nova-platform for desktop html), reply `T20 ready`.

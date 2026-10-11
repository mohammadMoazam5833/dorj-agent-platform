# T21 — Redesign the LIGHT theme of the Dorj Agent app (owner request 2026-10-11)

Owner: «تم روشن اپ اصلا جالب نیست درست دیزاین کن». Owner of this task: GLM. Reviewer/deployer: Claude (CTO).
Repo: /workdir/NovaAgent (theme code: `src/themes/color-themes.ts` `dorj-light`, `src/themes/appearance.ts`
light tokens, pre-hydration copy in `src/index.css`). Do NOT deploy. Commit in /workdir/NovaAgent.

## What is wrong today (see tasks/T21-light-before.png)
- Sidebar nav items («گفتگوی جدید»، «سفارشی‌سازی»، «قواعد»، «اتوماسیون»، «گفتگوها»، «تنظیمات») are almost invisible
  (very light grey on white). Conversation meta (time "1d", model "qwen3") is unreadable.
- The selected conversation is a flat grey block whose title disappears.
- Header icons (panel toggle, theme toggle, collapse) are faint. Everything is the same white: no hierarchy
  between sidebar, chat area, composer, cards. Skeleton bars and borders are barely visible.

## Target (the common light look of agent apps: Cursor Light, VS Code Light Modern, Claude, ChatGPT)
| role | value |
|---|---|
| sidebar / left rail | #F3F4F6 (one step darker than the chat) with a 1px #E5E7EB edge |
| app + chat background | #FFFFFF |
| panels, cards, composer | #FFFFFF with 1px #E5E7EB border and a soft shadow (0 1px 2px rgba(16,24,40,.06)) |
| hover row | #E9EBEF ; selected row #E3E8F4 + 2px accent bar on the start edge, title stays #111827 |
| primary text | #111827 ; secondary #4B5563 ; muted/meta #6B7280 (never lighter than #6B7280 for text) |
| accent (buttons, links, focus ring, selected model) | the Dorj blue #1E3A8A (hover #1E40AF), focus ring rgba(30,58,138,.25) |
| gold highlight (badges only) | #B7791F text on #FEF3C7 |
| code blocks | #F6F8FA background, #1F2937 text |
| success / warning / danger | #15803D / #B45309 / #B91C1C |
All text/background pairs must meet WCAG AA (4.5:1) — add a unit test that computes the contrast of the main
pairs from the token table. Icons use the secondary text colour, accent on hover/active.

## Rules
- Only the light theme changes; dark stays as it is. Keep key names `dorj-light` / labels.
- RTL Persian default and LTR English must both look right; Vazirmatn; no letter-spacing.
- No hard-coded colours in components: fix the tokens; if a component ignores tokens, switch it to the token.
- Screens to check and screenshot (fa + en, 1440 and 1024): home, conversation with tool calls + code block,
  settings, the model picker popover, the memory-save card, file/terminal panel. Save before/after into
  /workdir/nova-platform/tasks/T21-evidence/.
- `npm run lint`, `npm run typecheck`, unit tests green.
- Reply `T21 ready <hash>` with the changed files.

## Review 1 (CTO, after 5b030e3) — NOT accepted yet
Your own evidence (T21-evidence/after-fa-1440-home.png, after-fa-1440-conversation.png) still shows the main bug:
- Sidebar items «سفارشی‌سازی»، «قواعد»، «اتوماسیون»، «گفتگوها»، «تنظیمات» and the filter/folder icons are almost
  invisible. Conversation meta (time "4h", model "minimax-m2.7") is invisible. Header icons (collapse, theme
  toggle) are invisible. Markdown table first column ("CLI", "MCP support"...) is faint grey.
- The tokens are right (#6B7280) so something else wins: find it with getComputedStyle in the browser
  (effective colour AND the product of opacity up the parent chain, plus any overlay/backdrop/filter on the
  sidebar). Likely: a disabled/inactive style with opacity, a dark-only hard-coded colour (text-white/#ddd,
  rgba(230,230,230,..)) or the sidebar's own scoped vars in src/index.css that only exist for dark.
- Acceptance (must be a real Playwright test, light mode, fa + en): for every visible text node in the sidebar,
  the header and the conversation list, effective contrast (colour x cumulative opacity) against its actual
  background >= 4.5:1 (meta text >= 4.5 too). Icons >= 3:1. The test must fail on 5b030e3 and pass after.
- New screenshots after the fix; reply `T21 ready <hash>`.

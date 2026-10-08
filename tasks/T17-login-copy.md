# T17 — Platform login page: better Persian copy (after T16)

Owner: mm_work_1 (GLM). Reviewer: Claude (CTO). Owner request 2026-10-08: «چیدمان جملات page login
را از نظر فارسی بهتر کن». Theme: `/workdir/k8s-deploy/keycloak-theme/dorj-v3` (realm dorj, used by
platform.isigpu.local and goodarzi). **Do NOT deploy and do NOT restart Keycloak** — a theme deploy
restarts Keycloak (all logins ~1 min); CTO does it.

## Do
1. Inventory every user-visible Persian string of the login flow: `login/messages/messages_fa.properties`,
   the `.ftl` templates (login, login-reset-password, login-update-password, error, info, logout
   confirm), `template.ftl` (hero, docs panel), and JS (`v3-scene.js`, email-suffix.js) messages.
2. Rewrite in natural, formal, modern Persian (Iranian enterprise product tone — like the portal
   dorj.isigpu.local copy in k8s-deploy/dorj-site/src/lib/content.ts):
   - short lines, no «،» chains, no «—», no trailing «.» in headings/buttons/labels;
   - consistent terms: «نام کاربری»، «گذرواژه»، «ورود»، «فراموشی گذرواژه»، «پشتیبانی»;
     the platform is «پلتفرم دُرج»; avoid mixed Finglish/English unless it is a product name;
   - error messages tell the user what to do next («گذرواژه نادرست است. دوباره تلاش کنید» → «گذرواژه
     درست نیست و می‌توانید دوباره تلاش کنید» style, polite, one sentence);
   - correct Persian typography: ZWNJ (نیم‌فاصله) in می‌/ها/ترین/ای, «ی» and «ک» Persian (not Arabic
     ي/ك), Persian digits in visible numbers, «» quotes.
3. Make the English messages_en consistent with the new Persian meaning.
4. Produce `tasks/T17-evidence/copy-diff.md`: table old → new for every changed string, and render the
   pages locally (test realm or static ftl preview if the repo has one — `scripts/133 --create-test-realm`
   exists but needs CTO; otherwise a static HTML mock) for screenshots fa desktop + 390px.
5. Commit in k8s-deploy (theme files only) and reply `T17 ready`.

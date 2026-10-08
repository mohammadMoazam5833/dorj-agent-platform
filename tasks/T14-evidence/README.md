# T14 evidence — admin table filtering, sorting, pagination

- Shared vanilla-JS helper applied to the services, disk usage, LLM models, tenants, conversations, and logs tables.
- Page size: 20.
- Every table has search, sortable headers (ascending/descending), pager controls, and row counts.
- First column is start-aligned; other columns are centre-aligned.
- UI labels are bilingual (`Search/جستجو`, `Previous/قبلی`, `Next/بعدی`, `Page X of Y/صفحه X از Y`), with Persian digits in FA.

## Local mocked Playwright checks

Run from `/workdir/nova-platform`:

```bash
node tasks/T14-evidence/generate-local-evidence.mjs
```

The fixture uses 22 rows so pagination is exercised. `local-checks.json` records:

- 22 rows before filtering, 10 after `service-1`
- service name order changes on the second sort click
- pager reports `صفحه ۲ از ۲`
- zero browser/page errors

Screenshots cover the FA dashboard (page 1 and page 2), tenants, conversations, and logs.

## Build and render checks

- `node --check control/admin-src/nova-admin.mjs`
- `node --check e2e/admin.spec.js`
- `python3 -m compileall -q control/nova_control`
- rendered admin page anchor validation with `NOVA_ADMIN_PAGE`
- `docker build -t nova-control:t14-evidence control` (log: `build.log`, no deployment)

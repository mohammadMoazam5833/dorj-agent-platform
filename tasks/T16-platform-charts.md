# T16 — Platform (platform.isigpu.local) charts: clearer, prettier, better layout

Owner: mm_work_1 (GLM). Reviewer: Claude (CTO). Owner request 2026-10-08. Repo: /workdir/dorjService
(branch goodarzi-phase3, React + ECharts). Live: dorj-service 0.3.31 (`DORJ_SERVICE_TAG`, deploy by
k8s-deploy scripts/135; **do NOT deploy** — CTO deploys after review).

## Where
- `src/components/EChart.jsx` (wrapper), `src/lib/echart-theme.js` (theme),
  `src/pages/Dashboard.jsx`, `src/pages/Usage.jsx`, `src/pages/admin/Monitoring.jsx`.

## Goals (keep data/queries as they are — presentation only)
1. One polished chart theme in `echart-theme.js` used by every chart: Dorj palette (lapis #2747B8,
   sky #5B8CFF, gold #E8A317, teal #2BB6A8, violet #8B7CF6, rose #E76A5E as series order), soft grid
   lines, no chart borders, rounded bar corners (4px), smooth lines with a light area gradient,
   Vazirmatn everywhere, Persian digits in fa (axis labels, tooltips, legends), RTL-aware legends.
2. Tooltips: one shared style (card, 10px radius, shadow), formatted units (GiB, %, ms, تومان),
   all series of that x in one tooltip (axis trigger), thousands separators.
3. Readability: axis labels never overlap (rotate/interval/abbreviate), y-axis unit in the name,
   time axes formatted by range (HH:mm for < 1 day, MM/DD otherwise; Jalali date in fa if a helper
   exists in the repo — check `lib/` before adding one).
4. Layout: dashboards use a responsive card grid (1 col < 700px, 2 cols < 1200px, 3 cols above);
   each card = title + one-line subtitle (what it shows) + chart + small "last updated"; equal
   heights per row; KPI tiles (big number + delta) above charts where the page has headline numbers.
5. States: skeleton while loading (exists — reuse), friendly empty state, error state with retry.
6. Motion: gentle initial animation (≤ 600ms), none on live refresh; respect prefers-reduced-motion.
7. Dark/light: if the app has both, both must look right.

## Rules
- Project rules (k8s-deploy/CLAUDE.md): bilingual fa/en, Vazirmatn, no letter-spacing, tables keep
  filter/sort/pagination, value columns centred.
- Must pass: `npm run build`, `npm test` (if present), the repo's lint, and the existing e2e
  `e2e/charts.spec.js` logic (do not run live e2e against platform — CTO does; you may run it against a
  local `vite preview` with mocked APIs if the repo supports it).
- Screenshots before/after (fa, desktop + 390px) → dorjService `tasks/T16-evidence/` (create).
- Commit in small steps; reply `T16 ready`.

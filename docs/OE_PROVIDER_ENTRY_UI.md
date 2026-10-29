# Normal OpenEvolve entry integration

The authenticated `/engine-runs/structured-search` page uses the actual normal027
router and generated main SDK. The dedicated capability read must report exact
installed027 routes before model/snapshot catalogs or submission controls appear.
A missing route, unavailable schema or unknown qualification fails closed. This
is distinct from the legacy generic OpenEvolve capability and maintained fixture.

- Select an owner-listed, explicitly released execution snapshot by its dedicated
  ID. Source navigation uses the actual source run; no legacy snapshot coercion.
  A saved choice missing from the current page is retained, never silently replaced.
- The current market profile accepts synthetic-by-construction data. Operator
  model transport may independently be real or synthetic. Neither is live-market
  or point-in-time evidence, and provider billing remains unknown.
- Only the four closed strategy parameters are editable; there is no arbitrary
  Python, provider endpoint, credential or image field. Targets and realized orders
  or fills are explicitly distinct.
- The user declares execution window, initial cash, optional minimum closed trades,
  mutation ceiling and all budget dimensions. Zero budget does not authorize work.
  Preflight may return a required budget; applying it edits the draft only and
  requires a new preflight. Missing estimates are not invented.
- Operator availability is not submission authority. Final wiring must require the
  exact fresh preflight and runtime capability, and preserve an unresolved owner-
  scoped idempotent envelope rather than replacing it on network uncertainty.

Regenerate using `python -m backend.app.export_openapi` followed by
`npm run generate-client` in `frontend`. Component-only schema copies were removed;
all request, capability and response types come from the registered actual router.
The ordinary026 routes/components are preserved. The server is still responsible
for exact schema, operator profile, release, budget and fresh request admission.

The page saves an owner/SUBPATH-scoped immutable key/body before POST, checks the
full owner/request/model/snapshot/unit receipt, and retains uncertainty across
reloads. Only backend-confirmed post-replay `preflight_changed`409 and
`run_quota`429 release the pending draft; auth/schema/conflict/network failures
preserve it. An authentication change discards an in-flight SDK reply.

Confirmed receipts are separate from current status. Starting another search is
explicit. `/engine-runs/structured-search/runs/<run-id>` reads actual owned status
and budget; terminal/error reads stop polling. No pause/resume/cancel action or
unreleased candidate performance/result is invented. The demo onboarding remains
an explicit synthetic-data action followed by a separate preset application and
normal research preflight; it does not submit or release anything automatically.

Local unit/transport/type/build checks do not prove a browser submission,
PostgreSQL migration, generated strategy, native execution or cold restore.
Coherent027 hosted acceptance remains required.

Validation on the composed local tree: 41 focused tests, all 1191 frontend tests,
full `tsc --noEmit`, and Vite production build passed. SDK transport tests exercise
root/SUBPATH bearer headers, exact retry body/key, error status, and session change.
No hosted/browser or PostgreSQL acceptance is claimed.

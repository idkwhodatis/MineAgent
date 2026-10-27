# RD persisted-history read slice

Based on composed revision019 (`3ccd894`). This adds authenticated metadata reads
and a real UI over existing persisted RD rows. It does not enable production
submission, dispatch, cancellation, resume, OpenEvolve, providers, or deployment.
No new migration, execution fence or process scope is introduced.

The explicit product catalog in `engine_run_catalog.py` leaves creation closed
independently of installed database capabilities. POST `/engine-runs` rejects with
409 and never writes a run. GET list/detail/history uses owner and exact RD
identity; unknown, legacy and OpenEvolve details are not coerced into seed runs.
History reports absent outcomes as absent, not as success. Snapshot IDs and
hashes come only from persisted evaluation outcomes. Reads do not parse files,
claim current artifact availability, or release metrics/execution grants.
The existing factor workbench remains responsible for verifying linked evidence.

## Required acceptance

- Backend unit route/projection tests: `backend/tests/test_engine_history.py`.
- Real PostgreSQL + authenticated HTTP (must execute, not skip in acceptance):
  `tests/integration/test_engine_history_reads.py`. It creates actual maintained
  RD source evidence with the existing fixture, not an in-memory run.
- Frontend typecheck and unit contracts: `tests/unit/engine-history.test.ts` and
  `tests/unit/navigation.test.ts`. HTTP interception there is only unit evidence.
- Self-provisioning real browser harness: `python -m pytest
  tests/engine_history_browser -q` requires TEST_DATABASE_URL and a built frontend.
  It reuses real Alembic isolated-schema/two-owner fixtures, persists maintained
  source evidence, serves the actual API and compiled UI, then launches Chromium
  with only synthetic account credentials. It checks exactly one non-skipped
  browser outcome and confirmed browser/API cleanup. No manual run ID is needed.
- Existing-stack browser gate: `npx playwright test -c playwright.engine-history.config.ts`.
  Supply normal two-account E2E credentials, API/frontend addresses and an actual
  owner-bound saved source run as `E2E_RD_HISTORY_RUN_ID`. No route mocks or skip
  are present. Repeat with nonempty `E2E_APP_SUBPATH`, matching frontend SUBPATH
  and `E2E_API_URL` deployment prefix. The run must be retained by the fixture
  preparation process; an empty list does not satisfy the gate.

Local implementation time: 2026-10-26 approximately 14:21–14:30 UTC. Requested
commit author/committer metadata is separately fixed at 2026-10-26T02:00:00-04:00;
it is not the actual execution time. Browser/real PostgreSQL acceptance requires
the real stack and is not claimed by local collection or unit tests.

Local validation checkpoint: frontend 71 files / 1062 tests passed; TypeScript,
production build and format check passed. New backend projection plus existing
control-response tests: 46 passed. Two real PostgreSQL HTTP cases and the real
browser harness were collected but not executed (TEST_DATABASE_URL absent).
Both PG cases are in neutral REQUIRED and REQUIRED_SOURCES, so the hosted closed
inventory requires execution. The inherited neutral static AST parity test fails
on CORPUS.CANONICAL_CASES even on untouched 3ccd894; it is not waived by this slice.

## Revision021 successor

The successor is based on `6ecb184` (composed021 plus the repaired exact collection
parity test). History dispatches the actual evaluation-consumer kind and joins
execution publication/completion/results with owner/run/attempt/generation scope.
`execution_snapshot_id` and the exact `mineagent.factor-execution-snapshot.v1`
schema are separate from legacy `snapshot_id`/feature-snapshot hashes. A pending
publication is not completion; completed publication is not an evaluation result.
Cross-version duplicate results, consumer substitution and hash mismatch fail
closed rather than silently displaying legacy or unevaluated output.

The existing actual CoSTEER/numeric/completed-snapshot integration case now also
checks authenticated history HTTP, exact execution snapshot hashes, reload, and
foreign-owner refusal after real execution. This remains a hosted Docker+PG gate,
not satisfied by the pure projection tests or the source-only browser harness.
Production submission remains explicitly disabled in both versions.

Successor validation (actual work approximately 14:39–14:46 UTC): 17 projection
and 20 closed-inventory tests passed; frontend 1062 tests, typecheck, build and
format passed. Full backend: 1865 passed / 11 failed, all daily-reference benchmark
identity tests: composed021 already has 532 tracked eligible source files, above
its inherited 512-file cap (`script/benchmark_v1_daily_reference.py`). The slice
retains that guard and reports this owner-level baseline repair dependency.
The standalone browser harness now fails explicitly when TEST_DATABASE_URL is
missing instead of allowing a skipped gate. Real execution/browser acceptance is
still not claimed by collection.

The full application workflow now invokes the isolated-schema browser harness
immediately after the built frontend and matched Chromium are available, before
starting the separate ordinary application stack. It requires exactly one
successful, non-skipped case and confirmed fixture cleanup. Raw logs/JUnit stay
in RUNNER_TEMP; only the allowlist-sanitized summary enters ci-evidence artifacts.
This is a required hosted step, not an optional standalone-only instruction.

The existing actual Caddy root/nonempty-SUBPATH browser case also now navigates
to Engine history, reads real disabled capabilities, reloads, deep-links an
unknown engine run through the prefixed API, observes 404 (never seed fallback),
and returns with Back. Its existing request-escape and failed-asset checks cover
these new operations. Old login/logout, factor-export and SSE cases are retained.
This is added hosted coverage, not a claim that local collection executed Caddy.

After owner commit `dc1f056` was composed locally (same bounded inventory repair),
the final full backend regression passed: **1879 passed**, 3 existing warnings.
The prior 11 benchmark failures are resolved; no test was waived. Final authored
source work was performed approximately 14:21–14:52 UTC, distinct from requested
historical commit metadata. Hosted PostgreSQL, actual Chromium and Caddy gates
remain required and unexecuted locally.

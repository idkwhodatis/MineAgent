# Real normal027 browser boundary

The actual PG/container fixture calls three helpers while its database and
retained files remain alive. These are required checks, never an API-only fallback:

1. `create_demo_in_browser(engine, settings, account)` starts with an owned account
   having no datasets. The real browser explicitly acknowledges synthetic data,
   creates it through the normal API, and applies the returned RD preset. It
   verifies that source declarations remain unchecked and reload creates no run.
   Returns a validated `StructuredDemoReceipt`; pass `.model_dump(mode="json")`
   into the foundation helper's optional `demo_receipt` argument.
2. `create_oe_submission_in_browser(engine, settings, account, request)` takes the
   exact required-floor request obtained by actual preflight (no model call).
   The browser selects the already-released actual factor/model, fills the fixed
   declaration with zero budget, checks, explicitly applies the quoted budget,
   rechecks, and submits once. `BrowserSubmission` returns the real observed
   `receipt`, `idempotency_key`, typed `body`, and `request_canonical` (full submit
   JSON) for exact HTTP replay. No key or storage envelope is injected.
3. `assert_oe_run_in_browser(engine, settings, account, run_id, other)` reads the
   actual completed run, reloads and refreshes, proves other-owner404 via API and
   browser, and verifies unchanged actual release-ledger IDs. Status/budget is not
   candidate/result disclosure: no orders/fills or release/submit controls are
   invented. A separate terminal result/release browser seam remains required
   when the actual result reader is available; this helper does not claim it.

Set `MINEAGENT_OE_NORMAL_BROWSER_REQUIRED=1`, build `frontend/dist`, and provide
installed Chromium/Playwright. Each call requires exactly one named non-skipped
JUnit case; the dedicated config collects three cases total. The fixture alone
owns RD/OE worker dispatch, actual provider calls, and teardown. Synthetic login
credentials and bounded observations reach Node; DB URL/settings do not. Traces,
videos and screenshots are disabled, private receipt files are temporary, and
all owned browser/API processes must terminate before returning.

Local validation is collection/type/static checks only until the actual fixture
executes the browser against PostgreSQL and protected containers.

## Explicit normal result releases

The fourth helper `assert_oe_results_in_browser(engine, settings, account,
run_id, execution_ids, other)` takes the actual parent/child execution IDs in
that order. Invoke it after the status-only helper and before any fixture-side
result release. It first requires both actual result reads to return 409, then
uses separate unchecked exposure acknowledgements and release POSTs for each
side. It checks actual retained order/fill identities, the explicit same-snapshot
comparison, reload without new releases, and foreign-owner 404 responses. It
creates no new search, model call, or engine execution. Collection/type checking
alone is not evidence that this PG/browser acceptance ran.

The normal status page exposes cancellation only when the actual run action
permits it. A pending cancellation receipt means waiting for containment and
settlement, not a positive stop or refund. Result metadata alone grants no result
visibility; ineligible scores remain null and comparisons are not causal gains.

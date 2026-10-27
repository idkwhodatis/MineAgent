# Configured RD submission and queue (revision 023)

This is the initial **fixture-model** profile, not a paid/live LLM integration,
PIT certification, out-of-sample claim, general multi-window research or automatic
checkpoint resume. Its declared output window contains exactly one retained
session and it runs exactly one iteration. Historical lookback is additional
input (L prior sessions plus current), never silently truncated output coverage.

## Operator configuration

The default remains `RDAGENT_RESEARCH_ENABLED=false`. Merely upgrading the schema,
installing an image or setting a client field does not select a runtime. After the
exact composed HTTP/queue/four-unit gate passes, the operator may explicitly set
`RDAGENT_RESEARCH_ENABLED=true` and `RDAGENT_RUNTIME_PROFILE` to JSON matching
`backend.app.rdagent_server_profile.RDServerRuntimeProfile`:

- fixed schema `mineagent.rdagent-server-profile.v1`;
- fixed profile `configured_rd_single_output_session_v1`;
- fixed model profile `fixture.rdagent.v1` (not a provider credential);
- only time-series `close[t]/close[t-L]-1`, where L is the declared lookback,
  with close input and return output; unsupported free-form tasks are rejected,
  never silently replaced with the maintained response source;
- complete `RuntimePins` with the actual built orchestrator, candidate, normalizer
  and analysis image IDs and the four exact shipped dependency-lock digests;
- `qlib_installed_parent_image_id`, the actual verified installed Qlib parent.

No mutable tags, caller-selected images, arbitrary env/argv/mounts, paid providers
or deployment operations are introduced. Process issuers independently check
image/source/inventory identity; configuration is not evidence that CI passed.
The repository does not enable an installation on behalf of its operator.

For an operator-controlled installation **after the exact composed gate passes**,
provide `RDAGENT_RUNTIME_PROFILE` as the JSON serialization of the model above and
`RDAGENT_RESEARCH_ENABLED=true` to the API, dispatcher and worker processes; all
three must use the same immutable image IDs and installed-parent pin. The normal
commands remain `python -m workers.research dispatcher`, `worker`, and `reconciler`
(one role per process). Missing profile, wrong revision, unsupported lock or
changed runtime emits a distinct denial; no environment setting is inferred from
client requests or automatically enabled by a successful local test.

## Authenticated user flow

1. `GET /api/v1/engine-runs/capabilities` reports independent history/preflight and
   create availability, fixed profile limits and a server budget recommendation.
2. `GET /api/v1/engine-runs/datasets` lists owned metadata, not a proof of eligibility.
3. `POST /api/v1/engine-runs/preflight` reads the retained daily market-data files,
   exact calendar and fixed membership declaration. The objective window equals
   the output window; forward labels remain separate from candidate input. The
   explicit acknowledgement describes **declared**, not independently verified,
   daily availability and static membership.
4. Keep the exact returned normalized request plus `preflight_sha256` and one
   stable `Idempotency-Key`. `POST /api/v1/engine-runs` commits the queued run,
   immutable config/spec/plan/request receipt, conservative full-dataset exposure,
   zero-used budget and durable outbox in one transaction. The run has generation
   counter 1 but **no attempt, lease or execution fence** until the worker claims it.
5. The normal dispatcher enqueues `mineagent.execute_rd_run` in Procrastinate's
   research queue. The normal worker creates one real attempt after revalidating
   the receipt, runtime and retained source. Duplicate deliveries cannot relaunch
   an already claimed generation. No legacy strategy row is substituted.
6. A successful result requires the real controller, candidate, fresh normalizer
   and protected Qlib unit, their positive stopped chains, published snapshot,
   measured declared objective and final adoption. History routes never translate
   these into legacy strategy PnL.

A timed-out POST is not rejection. Retry the **same body, preflight hash and key**.
Exact accepted replay is checked before current runtime availability and source
preflight and remains readable after the profile is disabled or the run finishes.
Conflicting bodies/keys, transport errors and ambiguous responses must not discard
an unresolved client envelope. A changed server profile requires fresh preflight
for a *new* request, not rewriting an already accepted receipt.

## Control and containment recovery

`POST /api/v1/engine-runs/{id}/cancel` is owner scoped and idempotent. A queued run
with no attempt cancels atomically. An active run receives `cancel_requested` and
`pending=true`: this revokes future fenced work, but is **not** a positive stop,
refund, successful terminal result or evidence-import authorization. Independent
runtime deadlines still apply. The reconciler commits old-token revocation before
recovering the exact durable controller/numeric/analysis admission inventory. Only
complete typed positive stop receipts permit cancelled/interrupted terminal state.
Missing identity, failed containment or an incomplete receipt chain leaves the run
pending and its reservations held. Recovery imports no result, refunds no budget,
and does not retry the generation. Actual cancellation containment acceptance is
still required; local doubles are not that proof.
Pause/resume remain explicitly unsupported for this fresh-only profile; no failed
or interrupted generation is automatically restarted.

## Acceptance status

Local pure tests and collection are not PG/Docker/browser acceptance. The required
hosted gate retains the existing actual configured-result Chromium and cold
restore cases and adds eight real-PG submission/negative cases plus an actual
real Chromium form/preflight/HTTP → outbox → Procrastinate worker → four-unit
result/release browser → cold restore/receipt replay case.
No selected case may skip. That actual case uses isolated disposable **databases**,
so independent normal worker connections cannot accidentally miss a test-only
connection search path. The revision023 receipt closure supplements, rather than
reinterprets, historical source/analysis closures. Downgrade refuses retained
submission receipts.

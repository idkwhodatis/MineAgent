# Configured RD entry and cancellation UI (revision023 successor)

This local successor is separate from the frozen022 read/release cut. It does not
change deployment settings, enable a paid provider, or make the server runtime
available by itself. The initial server-controlled model is explicitly
`fixture.rdagent.v1`; its accepted task is the declared-lookback time-series close
return (`close[t]/close[t-L]-1`, close input, return unit). Other tasks fail
preflight rather than being silently rewritten. This is not arbitrary task
generation; no client-selected image, runtime or provider field exists.
Actual UI implementation began 2026-10-26 16:25 UTC. Hosted acceptance is pending.

## Submission identity

The form selects actual owned retained datasets and declares task, output window,
input fields/lookback/lag, objective and unverified daily-source acknowledgement.
The backend supplies its bounded budget/profile. Preflight creates no run or plan.
A new POST requires both authoritative `can_create` and `can_submit`, not merely
an installed schema or an enabled-looking button.

Before POST, a tab-scoped, owner- and deployment-prefix-bound envelope persists the
exact normalized request, preflight commitment and random idempotency key. An
unresolved envelope locks edits and survives reload. Retry sends the same body and
key even after the runtime closes. Invalid/unavailable storage blocks new requests;
it is not treated as an empty draft. A receipt must have the exact request and
preflight hashes, a valid run/plan identity, `accepted=true`, and an actual backend
status before the UI navigates. It never invents queued or succeeded state.

Only agreed explicit post-owner-key-lookup rejections permit a fresh preflight:
`preflight_changed`, `engine_request_ineligible`, `run_quota`, disabled/unconfigured/
unsupported runtime and `runtime_profile_changed`. Transport/5xx,401/403,
`neutral_busy`, `idempotency_conflict` and unavailable submission schema retain the
original envelope. A known accepted run remains linked if navigation fails; the
same form cannot silently mint another key for it.

## Controls

Detail polls authoritative status while active. Cancel is offered only when the
owner-scoped backend action says it is available. Queued cancellation and active
`cancel_requested` are distinct; the latter explicitly does not prove physical
sandbox stop. Pause/resume remain unavailable for the fresh-run profile. There is
no browser-created attempt, fake checkpoint, optimistic completion or seed-route
fallback.

## Evidence boundaries

Portable tests cover storage isolation/replay, corruption/failure, definitive
rejection classification, mismatched receipts and cancellation status integrity.
They do not prove the live UI or worker.

`rd_submission_browser.create_queued_submission_browser` requires its explicit
workflow flag, actual compiled frontend/HTTP/Chromium and exactly one non-skipped
private-JUnit case. It fills the real form, preflights and creates the sole queued
run, checks its true receipt, reloads, navigates Back and verifies no generation
was fabricated. It does not run a worker or return an execution grant. The owning
normal-user-flow test resolves the immutable receipt from PostgreSQL, then uses
normal outbox/Procrastinate dispatch and the same actual four protected units.
The existing completed-result browser then verifies release and historical data.
The helper/spec are wired for hosted acceptance, not locally claimed executed.

Real browser transport-response loss and active cancellation containment are not
proven by storage unit tests or merely clicking through successful queued creation.

## Exact execution evidence in the factor workbench

The existing factor version route now accepts a separate `execution` selection,
never a coerced legacy `snapshot`. Selecting a new version or a legacy snapshot
clears dependent execution selections. Library links use the exact latest
execution only when its factor and version match; URLs retain the chosen ID on
reload and Back. The existing owner-scoped execution-record endpoint resolves the
selected identity through at most 1,000 metadata records; outside this bound the
page fails explicitly rather than selecting a newer result.

Overview/Values/Evidence embed the existing historical release, bounded rows and
canonical JSON export. Evaluation embeds the actual direct-analysis release and
checks its exact snapshot identity. Evolution preserves definition/code comparison
and permits two exact execution views side by side. Each side must be released
before its values appear; this is not a persisted new comparison, full-data delta,
equal-protocol evaluation or financial-improvement claim. Legacy snapshot panels
and comparison endpoints retain their own dispatch.

The page explains declared inputs, lookback, lag, units and output window, with
exact retained source under an expandable formula implementation. There is no
invented symbolic explanation or intermediate operand reconstruction: the current
execution read endpoint has no verified calculation-step sample. The two initial
025 root parameter versions are not relabeled parent/child. Actual browser/root/
SUBPATH acceptance of these integrated tabs remains pending coherent hosted gates.

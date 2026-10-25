# Neutral research foundation

## Accepted predecessor

N0 is accepted at `d205bb65c3c0ca6b996a5577f565cdccb49656d7`:
[full staged validation](https://github.com/idkwhodatis/MineAgent/actions/runs/40209567439),
[factor acceptance](https://github.com/idkwhodatis/MineAgent/actions/runs/40209567413),
[completed-evidence cold restore](https://github.com/idkwhodatis/MineAgent/actions/runs/40209567472),
and [migration boundary](https://github.com/idkwhodatis/MineAgent/actions/runs/40209567420).

N1 adds internal durable primitives. It does **not** enable RD-Agent, Qlib,
OpenEvolve, external model transports, a public generic upload/launch API, or a
new run purpose/engine pair. Legacy strategy references remain mandatory and the
legacy DTOs and runtime codecs remain unchanged. N1 acceptance requires its new
exact-head PostgreSQL/Docker/restore gate **and** all existing full browser,
Compose/restart, native engine and capacity gates. Local collection is not execution.

## Accepted N1 evidence

N1 is accepted at `19583e465f6759c36f53fc69f66b3d76af57edaf`:
[full staged validation](https://github.com/idkwhodatis/MineAgent/actions/runs/40225806283),
[neutral acceptance](https://github.com/idkwhodatis/MineAgent/actions/runs/40225806422),
[factor acceptance](https://github.com/idkwhodatis/MineAgent/actions/runs/40225806441),
[completed-evidence cold restore](https://github.com/idkwhodatis/MineAgent/actions/runs/40225806335),
and [migration boundary](https://github.com/idkwhodatis/MineAgent/actions/runs/40225806298).
All four mandatory full-validation jobs passed. Seven artifact ZIP digests were
independently verified; the frozen capacity report was revalidated against its
baseline and budget. Neutral acceptance executed 68 real PostgreSQL cases,
9 real PostgreSQL/Docker isolation cases and 3 cold-restore cases with zero skips.
The existing factor acceptance executed 92 PostgreSQL, 5 parser-isolation and
9 browser cases with zero skips. The complete application and Compose browser
journeys also passed. Optional legacy profiles remain separately selected;
this does not claim paid-provider execution or production deployment.

Hosted PostgreSQL exposed a zero-argument trigger NULL-array bug during this cut.
The accepted migration normalizes trigger arguments and explicitly rejects changes
to immutable evidence. Downgrade obtains maintenance ownership and all neutral/FK
parent table locks with NOWAIT, requires READ COMMITTED for authoritative emptiness,
and refuses any evidence-bearing downgrade. Regression tests retain these checks.

RD-Agent/Qlib and OpenEvolve adapter development can now proceed in parallel.
Their production admission, runtime profiles, artifact kinds and schema closures
remain closed until each engine's separate staged acceptance is complete.

## Transaction and filesystem ownership

`AttemptFence` binds exact owner, run, attempt, generation and lease token.
`neutral_transaction` owns a fresh short session; helpers only flush. The order is
maintenance admission → owner submission → declared physical quota/publication
locks → Run → Attempt → budget → operation/admission → reservation/outcome.
Nested transactions and helper commits are rejected. Lost connections are not
reconnected to acknowledge work. Existing legacy factor publication is never
nested here: its entry rollback and internal commit have different ownership.

A supervisor retains `shared_lifecycle` PostgreSQL and filesystem maintenance
barriers across short transactions, external I/O, verified stop and cleanup.
No transaction spans model transport or container lifetime. Terminal run/attempt
changes and new generations are database-blocked while a process lacks positive
stop evidence or an object publication lacks a durable verified outcome. A new
generation also requires all old budget reservations to be settled.

## Broker and accounting

Chat and embedding requests/responses are distinct bounded typed envelopes.
Contexts, logical operations, physical transports and outcomes have independent
immutable identities. A logical retry with different request bytes is rejected;
each physical submission has a separately committed reservation and ordinal.
Completed exact replay performs no transport. Unknown submission is neither
retried nor refunded automatically.

The run-global integer ledger covers calls, tokens, cost ceiling, mutation slots,
evaluator executions, wall milliseconds, physical storage and process slots.
Deferred database checks reconcile immutable reservations/settlements with budget
projections and transport outcomes. N1 process/object settlement retains the full
conservative peak charge; it is not a measured refund. A verified object outcome
can settle its uncertainty without making its charged bytes disappear.

`BrokerAbort` escapes ordinary upstream `Exception` retry handlers. Explicit
`abort_context` durably revokes new operations/submissions without releasing
accounting, while preserving prior completed/unknown evidence. It currently
requires a valid current fence. Retry-sleep cancellation aborts immediately but
does not claim to persist a context abort after authority is lost. Unresolved
accounting remains conservative; future engine activation must supply any needed
revocation-only recovery protocol, never infer a refund from an exception.

## Process trust boundary

Only the registered, locally built **trusted neutral fixture** image/profile is
admitted. `MINEAGENT_RESEARCH_SUPERVISOR_IMAGE` must be its immutable image ID;
unset/mismatched configuration disables new admission/launch. Historical
containment-only recovery uses the exact recorded image and never launches.
The fixture profile is not approval for arbitrary uploaded code or new engines.

A committed admission and one-use immutable launch intent precede create. The
runtime receives opaque launch authority, not a freely constructed admission as
permission. Process/container identity, protected journals and scoped IPC tickets
are verified; broker work also requires the exact durable running admission and
identity receipt, not kernel credentials alone. A trusted PID-namespace init and
host watchdog enforce the bounded deadline. Network, mounts, entrypoint, memory,
PID, CPU and capability settings are checked against the registered profile.
The fixture trust boundary is explicit: future untrusted engine profiles require
separate image registration and reviewed isolation, including any required
separation from their deadline/IPC supervisor.

## Physical evidence and recovery

Neutral JSON objects live at
`research-objects/{owner}/by-kind/{kind}/{sha}.json`; identical bytes under different
kinds are distinct charged identities. Existing factor object addresses and
factor package v1 remain unchanged. Every publication has committed peak storage
admission before filesystem writes. The namespace fence survives DB loss;
crash-left temporary files block retry rather than create unlimited new temps.
Before each write, physical bytes and unresolved peaks are re-inventoried under
the fence. Exact completed replay verifies existing bytes without writing.

Revision `015_neutral_primitives` has explicit closure capabilities. Backup and
restore validate all neutral rows, typed references, objects, launch/identity/stop
journals and conservative accounting. Backup refuses live/uncertain process
containment; restore never executes a checkpoint or starts a unit. Neutral files
have no garbage-collection deletion authority in this cut. Historical 011/013
archives are checked before upgrade and retain old note/package/result pins.
Downgrade refuses to erase any neutral evidence.

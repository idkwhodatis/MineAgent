# Worker binding draft: no enabled event dispatcher

`openevolve_worker_models.py` and `openevolve_worker_services.py` are unregistered.
The immutable ticket binds exact sampling controller receipt, intent and unit;
only its hash is stored. Assignment binds exact expected worker identity and the
live kernel peer (PID/start ticks/namespace/cgroup). One intent gets one assignment.
The fixed one-worker unit cannot receive another assignment while any earlier
assignment remains open. Append-only closure names that intent's worker outcome.
Exact closure replay creates no effects; a closed assignment cannot be restarted.

## Deliberate missing authority boundary

There is **no enabled issuance API for SearchWorkerExpectedIdentity**. A descendant's
possession of an intent ticket must not let it become the expected worker by being
the first arrival. Resource tracker and other descendants have no work authority.
Existing IPC authenticates live unit membership, not pool-worker scheduling.
All new worker binding therefore remains closed until trusted placement is proved.
The separate `openevolve_worker_issuance.py` draft can persist an expected identity
only after a fresh live controller/fence check, exact closed placement wire/event
hash agreement with finalized evidence, scoped intent/ticket validation and a
repeated bounded shared kernel observation. It flushes only; no listener, Docker
inventory source, object publication or commit ACK is enabled.

Pinned ProcessPoolExecutor starts lazily at submit. A possible later narrow seam
is a controller-observed post-submit inventory of its exact single pool worker,
with native intent and worker namespace PID. A host must independently map and
verify live host PID/start/namespace/cgroup and parent lineage. A worker-started
request may wait boundedly without holding SQL locks for that controller evidence,
then reauthenticate in a fresh fenced transaction before assignment/ACK. This is
proved locally by separate phase8 actual API/spawn observation and synthetic
bounded waiter, not an enabled host listener. The shared kernel helper is tested,
but exact durable-container Docker PID inventory, real socket transport and
committed host issuance still require integration acceptance. No algorithm/RNG changes or first-arrival permission shortcut.

## Remaining migration/event interfaces

- Same pending epoch tuple unique as controller receipt: full attempt scope plus
  epoch ID and admission ID. All assignment/expected/ticket/closure rows immutable.
- Deferred exact controller sampling receipt -> ticket -> expected identity ->
  assignment identity agreement; row existence alone is not an issuance protocol.
- Deferred unit-wide at-most-one-open-assignment check, serialized by locked epoch.
  Closure must name exact assignment intent/native iteration's worker outcome.
- Every worker event must independently revalidate live peer/current fence,
  assigned identity, intent ticket, allowed purpose, phase and exact context.
  Render/proposal/evaluation phase receipt mappings are not implemented; these
  methods do not yield wildcard broker/evaluator permits.
- Worker outcome persistence and assignment closure must share one transaction.
  Explicit historical replay may return an already committed exact receipt but
  must never create a new operation after assignment closure.
- Persist exact nonsecret identity evidence and include all these rows/objects in
  backup/restore closure. Candidate processes never receive any IPC channel.

Unit tests mock authenticated APIs to check wiring and fail-closed invariants.
They do not establish kernel authentication, PostgreSQL durability or hosted
containment. No deployment, paid provider or process admission is enabled.

### Transaction-local action enforcement draft

`backend/app/openevolve_worker_actions.py` now provides a closed observer dispatcher
and separate model/evaluator checks. They reauthenticate the exact live assigned
worker, lock the current epoch, and reject closed assignments, completed worker
outcomes, another intent's ticket, and PID reuse on every request. No returned
value is a reusable permit. Caller-provided callbacks are not accepted.

The observer phase is derived from immutable rendered context → completed proposal
→ applied source → enforced source → candidate grant → objective acknowledgement.
Every predecessor is bound by its actual row ID. The dispatcher requires exact
intent/epoch/admission and rejects out-of-phase requests before calling existing
full domain/evidence validators. Worker outcome and assignment closure share one
flush-only transaction. Any exception requires rollback of the whole transaction;
only the owner may commit and only then send ACK. A worker cannot drop an already
acknowledged objective by subsequently reporting an unbound error.

Model checks require the exact preexisting logical operation, typed rendered
context/hash, mutation intent, chat purpose, ordinal zero, open state, original
request hash, and no context abort. They do not allocate fake operations or retries.
Evaluator checks are mutation-only and bind the exact candidate grant, source,
evaluation row and logical ticket, with no existing physical outcome or objective
ACK. These checks do not replace the broker's physical retry/reservation/state
checks, process launch admission, or before/after-I/O lifecycle fencing.

Historical requests from a worker are not replay authorization: a closed assignment
never performs another side effect. Historical immutable receipt delivery must use
the separate live-controller-authenticated exact receipt path. There is no enabled
network dispatcher and these helpers are not registered with N1. Activation still
requires actual socket/kernel+SQL tests, atomic transaction ownership, revisioned
schema/backup closure, and end-to-end broker/evaluator wiring; service-double tests
are not that acceptance.

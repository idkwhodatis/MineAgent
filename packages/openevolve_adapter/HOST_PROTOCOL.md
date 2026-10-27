# Disabled OpenEvolve host protocol draft

This is an activation specification, not an implemented host server. All callbacks
currently fail closed without the host. The published legacy-only run gate remains
unchanged. Pure tests and the native full-loop fixture do not establish durable
history, containment, financial correctness or recoverability.

## Authority and transport

One admitted orchestrator unit owns the pinned image/profile, fixed bootstrap,
current attempt/generation and epoch. `/ipc/events.sock` carries closed observer
and evaluation events; `/ipc/broker.sock` carries the separately closed model
protocol. Both require real socket peer credentials, current process membership,
current fence and exact assigned phase/context—not only possession of a ticket.
The launch operation ID is not blanket authority for subsequent model/evaluator
operations. A resource-tracker peer receives no work authority. Generated native
candidates execute in separate registered evaluator units with no IPC, broker,
DB, provider credentials or host writable mounts.

The event frame is `mineagent.openevolve-event.v1`, containing admission_id,
epoch_id, kind and payload; its canonical UTF-8 SHA256 and process ticket are
outside the event. JSON is sorted, compact, ensure_ascii=False, no NaN/Infinity or
duplicate keys. Maximum complete request is 1 MiB; response 256 KiB. Replies bind
schema, event digest and durable receipt UUID. Client timeout/IO uncertainty is
terminal, never an automatic replay. The host must deduplicate by durable semantic
operation identity and exact request hash before dispatch, not rely on a client
connection ID. Conflicting replay rejects; exact replay returns existing state
without charging/dispatching a second physical execution. An event digest alone
is insufficient for operation identity.

## Ordered state transitions

1. **Bootstrap/preflight.** Resolve exact owner/run/full SearchSpec JSON and
   admitted prepared source. Resolve the separate frozen native input profile,
   its exact digest, full market/warmup/contract/capture values, dataset byte
   inventory and semantic projections. Validate the pinned runtime profile and
   source cap before preparation or rendering. This profile is not yet wired into
   the submitted SearchSpec/schema; activation must bind it atomically. Refuse
   unsupported factors and unresolved preparation parity. No run_evolution starts
   until all required original/prepared native parity receipts are accepted.
2. **seed_source.** Compare the actual controller-loaded bytes with prepared
   source identity. It is not sufficient to hash the same file twice. This event
   does not replace the prior parity gate.
3. **candidate_intent.** For the seed or a registered mutation intent, validate
   exact complete source/parent/protected region, assign the persisted candidate
   identity and return a closed EvaluationGrant binding owner/run/epoch/native
   program UUID/source hash/phase. No source rewrite or silent truncation is
   accepted. Repeated same native identity with different bytes rejects.
4. **execute_evaluation.** Resolve grant from durable state; reject client-selected
   windows/config/dataset/profile. Reserve exact global physical evaluator budget
   and register each evaluation unit before launch. Stage only admitted window+
   warmup data and full frozen RNG initialization. Every physical retry gets its
   own host-issued ordinal and reservation, never a worker-local counter. Do not
   return an objective until evaluator stop, artifacts, bounded native bundle,
   exact physical execution binding and all required window observations are
   validated. Undefined/native-error/trade-constraint outcomes are ineligible with
   null score, not zero or minus infinity. Compute weighted unrounded objective
   on the host; store exact evidence and window membership, then return the
   closed EvaluationResponse. Negative and zero eligible scores are valid.
5. **evaluation_complete.** The installed evaluator wrapper compares actual
   returned metrics to the prior host receipt. Eligible is exactly one finite
   combined_score; ineligible is an internal empty dict and raises a closed
   ordinary rejection *outside* upstream's error-to-zero catch. ACK exact receipt
   before returning to native code. Missing receipt, changed score, wrong owner or
   stale generation is terminal. A seed rejection aborts admission; a mutation
   rejection becomes an ordinary native worker error without population insertion.
6. **terminal_applied seed 0.** After complete seed insertion/artifacts/state,
   validate full native state and receipt membership, ACK frontier zero. No model
   call was required for seed/parity. Seed checkpoint follows before worker start.
7. **iteration_intent.** Before each native submission, persist exact selected
   parent/inspirations and deep native snapshot, ordinal, island, global mutation
   slot reservation and native iteration. Prefetched tail remains durable even if
   never applied. Return intent-only descriptor: no model grant exists yet.
8. **worker_started.** Bind actual spawned peer to that intent and phase. The
   descriptor itself is not permission for a different peer or generation.
9. **rendered_prompt.** Persist actual rendered system/user bytes and actual
   sampled reference roles/order, current source and rendered artifact section.
   Do not rerun sampling to invent exposure. Bind selection digest and rendered
   digest to typed SearchOperationContext, physical model operation and immutable
   model profile. Issue context ticket only after durable intent and budget
   reservation. Host global retry accounting is authoritative. Request bound is
   768 KiB UTF-8 canonical JSON, each message 262144 characters; response/source
   cap is 32768 bytes. Do not reuse the N1 256 KiB codec or historical revision
   closure without a separately versioned grammar.
10. **proposal_response/source.** Persist exact bounded raw model response and
    complete applied/enforced source before upstream can overwrite it. Oversize
    source is rejected with digest/byte length, never represented as an accepted
    truncated source. Model response provenance and candidate source provenance
    are distinct; malformed patch/protected-region failures remain evidence.
11. **worker_outcome.** Persist returned ordinary error or exact child projection,
    parent and island. Validate admitted child source/objective receipt before any
    controller insertion. It does not itself advance the applied frontier.
12. **terminal_applied mutation K.** Require next contiguous K before mutation.
    After all native insertion/feature/migration/artifact mutations and stop
    decisions succeed, validate projected state and associated durable evidence;
    ACK then advance K. Accepted, rejected and error outcomes all have explicit
    terminal records. Duplicate/out-of-order completion fails closed before
    mutation. Generic failures poison the epoch and prevent new submissions.
13. **checkpoint_prepare/publish.** Only acknowledged K can be saved. Host-issued
    token authorizes one fresh `/work/checkpoints/<UUID>` directory. Native bytes
    retain the upstream schema. Validate complete bounded inventory, exact full
    program fields, inline-only artifacts, hashes and stable persisted projection
    after handles close, then atomically commit the checkpoint pointer. Failure
    leaves charged orphan evidence and aborts; no replacement submission follows.
    A pointer ACK is NOT positive whole-unit stop evidence. Final uses actual K,
    never configured maximum. Durable checkpoint closure also includes prefetched
    intent tail and typed broker/evaluator/object references.
14. **checkpoint_load_prepare/complete.** Host requires accepted checkpoint
    closure, prior positive whole-unit stop, current fence and fresh epoch. Missing
    path must never fall back to a new seed. Strict inventory/schema validation
    precedes native load into empty state; compare post-load persisted projection
    exactly to reject upstream silent repair or field dropping. ACK before any
    worker starts. New epoch identities/materialized lineage must be explicitly
    supported by future schema; current draft models restrict initial epoch only.

## Remaining activation work

The owner must implement/migrate the closed host state machine, versioned broker
request/context grammar, current-fence process-context bindings, physical native
evaluator launch/stop flow, exact checkpoint closure/restore lineage, resolved
profile persistence and immutable image/bootstrap projection. Historic revision
015 archives must continue rejecting these new contexts. Hosted PG/Docker,
backup/restore, stale-fence, poison-epoch, no-IPC candidate and Playwright gates
must pass exact head. Full-loop local tests explicitly replace host callbacks and
model transport and use a synthetic evaluator; they cannot satisfy those gates.

## Phase7 control cursor (not part of frozen phase6 runtime)

The actual pinned sampling callback additionally sends a before-state hash and
full after-state. Host atomically acknowledges the iteration intent and a scoped
control receipt before executor submission. Control sequence advances on sampling
and terminal application; K advances only on terminal application. This is an
authenticated native observation, not a host replacement sampler. Checkpoint
closure binds the exact cursor and charged pending tail. Native RNG/diversity
cache are not checkpointed, so identical resumed trajectories are not claimed;
resume remains disabled pending explicit tail reconciliation.

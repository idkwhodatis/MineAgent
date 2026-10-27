# Initial-epoch persistence draft: activation checklist

`backend/app/openevolve_models.py` and `openevolve_services.py` are deliberately
unregistered and unmigrated. No RPC/router/dispatcher calls them. The existing
neutral fence still rejects the new run pair. DDL compilation and mocked-session
service tests are not PostgreSQL transaction, stop, transport or restore proof.

## Exact external unique targets

The additive activation migration must provide the following unique tuples
without weakening existing uniqueness or historical revision checks:

- `strategy_versions(owner_id, id, content_sha256)`.
- `dataset_versions(owner_id, id, content_sha256)`.
- `research_process_receipts(owner_id, run_id, attempt_id, generation,
  admission_id, id, kind)`.

Other external targets already exist: execution_attempts(run_id,generation,id),
research_runs(owner_id,id), and scope+id on neutral reservation/context/process
admission tables; ResearchObject owner/id/kind/sha. Tests scan every *internal*
foreign key for an exact declared target unique tuple. Owner must rerun this
against the complete migration catalog, not assume compilation validates FKs.

## New closed tables and causal identities

- `strategy_search_specs`: exact full run.config, owner, original/prepared source
  role tuples, dataset, execution/objective digests.
- `search_resolved_execution_profiles`: full typed market/task/warmup/native
  declarations/capture profile; digest binds the exact execution plan. It is not a
  replacement public submission DTO. Resolve idempotency before generated run ID.
- `strategy_search_epochs`: only ordinal 1, first iteration 1, reason initial;
  one exact scoped orchestrator admission, image/profile and complete bootstrap
  inventory SHA (not merely bootstrap.json SHA). Per-event peer authorization must
  resolve this unit; another same-attempt unit with the same role is not enough.
- `search_candidates`: separate raw UTF-8 source SHA and canonical JSON source
  envelope object SHA; parent binds full owner/run/attempt/generation/epoch/source.
- `search_iteration_intents`: global mutation slot, exact preexisting reserved
  `engine_mutation` vector (mutation_slots=1, all other units zero), actual native
  selection snapshot. No rendered model context is required before it exists.
- `search_rendered_contexts`: later exact broker context plus rendered object;
  intent/context each unique. Host rendering receipts are not sampling substitutes.
- `search_candidate_grants`: one seed/mutation grant per candidate and mutation
  intent, exact orchestrator admission, hashed ticket, immutable request digest;
  mutation also requires exact enforced complete proposal source.
- `search_proposal_responses` / `search_proposal_sources`: exact intent/rendered
  context/completed model outcome and raw response object, separately preserved
  applied/enforced source bytes or explicit oversize digest.
- `search_evaluations`: exact source/window/execution/prelaunch input-manifest hash,
  per-logical-ticket physical ordinal, evaluator reservation/admission.
- `search_evaluation_outcomes`: one outcome per physical evaluation; exact
  admission+stop receipt FK, input-manifest hash, native bundle SHA separated from
  JSON evidence wrapper SHA. Failed observation carries no native bundle hash.
- `search_objective_receipts` / `search_objective_evaluations`: exact candidate,
  objective, ordered one physical outcome per window; full precision finite score
  only if eligible, otherwise SQL NULL. Deterministic host feedback text and its
  SHA are persisted; every population artifacts_json must equal exactly the
  host-issued native_feedback, and accepted steps cannot alter existing Program
  records in the bounded one-island/two-mutation profile. Membership binds the physical outcome row
  as well as the source/window/evaluation tuple.
- `search_objective_acknowledgements`: exact grant/candidate/source/objective;
  native returned metrics must equal receipt, not upstream error-zero fallback.
- `search_worker_outcomes`: one per intent; evaluated requires objective ACK;
  ordinary error may precede candidate creation, or reference ineligible ACK.
- `search_terminal_applications`: immutable contiguous chain K=0 seed then K+1;
  full state object and previous boundary FK. Completed future workers are not
  applied frontier. Error/clean rejection cannot mutate native state except K.
- `search_checkpoint_intents` / `search_checkpoint_publications` /
  `search_checkpoint_files`: token-derived fresh destination, exact terminal,
  immutable inventory/closure objects, every original native file's byte digest
  distinct from its JSON/base64 storage envelope. **Snapshot only, not resume.**
- `search_epoch_aborts`: immutable poison record blocks further epoch operations.

## Required database invariants, not satisfied by draft CHECK clauses alone

1. Append-only update/delete triggers on every evidence row. Profile/spec JSON
   must match exact versioned typed grammar and canonical SHA, not merely contain
   a schema_version key. Direct SQL must not bypass DTO validation.
2. Deferred complete SearchSpec/run.config/profile agreement at commit; original
   versus prepared exact stored code/manifest hashes, execution/objective hash
   projections, dataset file/semantic projections. Published DTO does not yet
   embed the resolved profile; owner must bind it atomically in activation.
3. One initial seed per epoch; source envelope bytes/hash/AST/protected-region
   relations validated. Candidate grant phase equals candidate kind; mutation
   grant's intent parent equals the candidate's full ancestry. Seed grant has no
   model operation and no mutation reservation.
4. Intent slots globally unique and bounded by immutable spec; reservation purpose,
   exact vector, owner/fence and operation relation cannot be substituted. All
   dispatch reserves before external work. No worker-local retry accounting.
5. Rendered context must agree with intent selection, owner/run/epoch/parent,
   actual prompt digest, exact versioned broker request profile and immutable
   process-context binding. Logical launch operation is not wildcard authority.
6. Physical outcome's stop kind must be `stop`, positive confirmed-absent closed
   journal, exact admission/input/image/source/window/RNG. Merely having a stopped
   boolean or receipt UUID is insufficient. Services currently call existing
   supervisor offline journal validation; future typed profiles must use it.
7. Objective receipt membership is complete, ordered, distinct and exactly the
   spec window set; all referenced outcomes and object hashes resolve. Failed
   outcomes cannot be promoted by a caller-supplied score. SQL finite/non-null
   predicates must be `IS TRUE`-safe; null CHECK pass-through is not authority.
8. Worker and terminal outcome semantics agree; current accepted candidate's
   ancestry/iteration matches its originating intent. No prefetched future
   candidate can enter earlier K by forging iteration_found. State error/rejection
   equality includes metadata, feature statistics, map/archive/best and artifacts.
9. Checkpoint closure includes all epoch intents and physical/context evidence,
   including **finished but not terminally applied** prefetched work. Inventory
   rows exactly equal typed inventory; no missing/extra object. Snapshot's final
   state equals acknowledged K, not configured iteration limit. Every file's
   raw byte hash is validated through its separate JSON envelope hash.
10. All state advances require current fenced transaction and locked epoch;
    poison abort blocks new operations. Exact immutable replay lookup occurs
    after fence/epoch check but before mutable latest-frontier/reservation-state
    checks; conflicting replay never dispatches or overwrites evidence.
11. Version-specific backup closure must enumerate every table/object association,
    transitive broker operation/outcome, evaluator admission/stop journal and
    source reference. Revision 015 must still reject new kinds/contexts/roles.

## Deliberate remaining gaps / unsupported capabilities

- No epoch-creation/admission route, authenticated event server, token issuance,
  process-context registration, real evaluator launch or broker operation handler.
- Initial draft explicitly rejects marker-parity preparation and pinned factors;
  original/prepared parity comparison/approval closure is not implemented here.
- No resumed epoch/materialized ancestry, checkpoint selection or restart service.
  A snapshot publication cannot authorize continuation. Future restart requires
  positive whole-unit stop and tail reconciliation under a fresh fence/epoch.
- Rendered-context and proposal services now bind actual prompt references, the
  exact versioned shared broker request, completed model outcome, raw response
  object and both applied/enforced complete source (or explicit oversize digest).
  They still require authenticated host/broker integration, typed closure SQL and
  actual-PG proof. Objective feedback response generation remains a closed host concern.
- No active database enum/CHECK/schema widening, shared process role activation,
  registered new object codecs, or historic archive policy changes in this cut.
- Draft physical outcomes use roles `openevolve_orchestrator` / `native_evaluator`
  and admission.input_sha256 = prelaunch evaluator input-manifest SHA. Owner must
  settle exact operational profile semantics before migrating; never map these
  onto neutral_fixture or assume a proof image is the production orchestrator.

Required acceptance: exact-head PG constraint/concurrency/replay/fencing tests,
registered native evaluator containment with no IPC, actual seed parity and
objective execution, host broker trace/budgets, checkpoint stop/closure/cold
restore, both old/new revision behavior and UI Playwright flows. None is replaced
by the independently hosted synthetic upstream compatibility proof.

## Observed native sampling transitions (separate phase7 draft)

`tests/openevolve/native_state_transition_probe.py` verifies surviving Program
records remain byte-canonically unchanged for eligible seed + two mutations and
native continuation. Its `--reject children` mode intentionally exposes a current
host-draft incompatibility: the first error terminal has extra feature-statistic
samples despite unchanged Program records. Pinned native
`sample_from_island -> _sample_inspirations -> _calculate_feature_coords ->
_update_feature_stats` mutates controller feature statistics during selection.
With one seed and two prefetched selections, four complexity samples are added
before K=1 applies. This contradicts the upstream sampling docstring.

The separate phase7 draft captures full persisted state immediately before and
after the one actual native sampling call. `SearchControlTransition` records an
immutable sequence independent of applied frontier K: seed, sampling receipts,
and terminal applications. `register_sampling_intent` validates the previous
acknowledged state hash, unchanged Programs/unrelated metadata, exact native
complexity append count/order and bounded finite diversity history, and persists
the charged intent and sampling receipt in one host transaction. No sampler or
RNG is rerun. Attestation is from the pinned authenticated orchestrator; private
diversity cache values are observed rather than independently recomputed.

Checkpoint preparation binds both K and the exact control cursor. Closure requires
all control receipts, one sampling receipt per charged intent, terminal receipts,
and the unapplied prefetched tail, even if a worker already completed. Failed or
rejected terminal application must leave the latest acknowledged sampling state
unchanged except K. Missing/duplicate/reordered cursor evidence fails closed.
These remain unregistered draft services, not production durability acceptance.

Native checkpoints omit diversity caches and RNG state. No identical resumed
trajectory is claimed. Resume remains closed until persisted charged prefetched
intents can be reconciled explicitly instead of rerunning the same logical intent
with a new random selection. Frozen phase6 runtime/proofs remain unchanged.

### Phase7 deferred constraints required at activation

In addition to the scoped FKs declared by the draft ORM, the owner migration must
install deferred completeness/immutability checks for the whole epoch transaction:

- Exactly one sequence-1 seed control row per initial epoch; every terminal row
  and every charged iteration intent has exactly one corresponding control row.
- The scoped previous-row FK and sequence uniqueness form a contiguous chain.
  Sampling retains prior K; terminal advances K by exactly one and requires its
  already-sampled native iteration. Sampling iterations are contiguous and cannot
  be replayed as a fresh selection after acknowledgement.
- A mutation candidate grant cannot exist without the corresponding intent's
  sampling receipt. An ACK is emitted only after the host transaction commits;
  service `flush()` alone is not an external acknowledgement.
- A checkpoint intent's control row is current when prepared; publication must
  still reference that same current cursor. Its terminal identifies applied K,
  while its state hash identifies the potentially newer acknowledged sampling
  projection. Closure includes every cursor, intent and terminal plus exact
  unapplied tail, not merely unfinished workers.
- All evidence/control rows are append-only and epoch-scoped; mutation fences,
  exact process-peer/context checks and positive whole-unit stop remain mandatory
  independent of these SQL constraints. None of these checks authorizes resume.

### Event/worker checkpoint closure extension (excluded draft)

`InitialCheckpointClosure` now requires exact sorted controller event receipt,
worker ticket, expected identity, assignment, assignment closure and open-assignment
IDs. Publication queries every epoch row, rejects omissions/substitutions, checks
full scope, binds every control transition to one controller event receipt, and
reads the actual registered event/selection/population/worker identity objects.
Event payload state must equal the acknowledged control population; sampling also
binds the prior cursor hash and exact ordered worker snapshot. Worker evidence
reconstructs the controller placement event hash and exact scoped kernel tuple.

The fixed one-worker pending-tail policy permits at most one open assignment,
only for an unapplied intent. A prefetched not-yet-started intent is allowed, but
its sampling ticket and acknowledged placement must already exist before a
checkpoint boundary. Worker outcomes and assignment closures must be atomic;
completed-but-unapplied outcomes remain pending, never silently reapplied. No
assignment may skip an earlier charged intent or reuse the pool before closure.
The live binding service applies the same prior-closed ordering before granting
an assignment, not only when snapshotting.

These checks are initial snapshot validation only. They do not replace positive
whole-unit stop, backup's revision-specific table/object closure, per-worker
purpose/phase receipts, physical native evaluator provenance or explicit resume
materialization. `continuation=not_admitted_initial_epoch_only` remains mandatory.

### Initial-epoch semantic SQL fragment (excluded, not PostgreSQL-accepted)

`backend/app/sql/openevolve_initial_semantics.sql` is an explicit review-only
fragment, loaded as text by `openevolve_semantic_ddl.fragment()`. The loader refuses
model snapshot drift. Nothing imports it at application startup or in Alembic.
It adds 16 deferred INSERT constraint triggers after the separately reviewed
model DDL; it assumes the base scoped FKs, uniques, checks and append-only triggers
are installed together. It neither changes those models nor supplies activation.

The fragment closes ordered exact objective-window membership, execution/objective
profile columns, physical outcome scope/input agreement, eligible/failed/null-score
semantics (zero and negative eligible scores remain valid), mutation applied→enforced
proposal lineage, grant/candidate/source/parent agreement, and worker ACK eligibility.
It also checks native sampling cursor order, terminal-after-own-sampling ordering,
reverse intent/terminal→control completeness, control↔controller receipt agreement,
and scoped checkpoint intent/publication references. A prefetched completed outcome
must already agree with its objective even before terminal application.

External JSON evidence bytes, native metric recomputation, canonical JSON digests,
exact checkpoint object inventories, worker/process authentication and positive
whole-unit stop are NOT checked by this fragment. Existing host validators remain
mandatory; snapshot publication still gives no resume authority. This is not a
substitute for the neutral lifecycle, budget/retry accounting or live IPC guards.

`tests/integration/test_openevolve_semantic_fragment.py` uses the existing isolated
`postgres_engine` fixture and direct SQL. Projection tables use the frozen models'
actual column names/types but deliberately omit base constraints to isolate these
additional checks. Some adversarial cases alter not-yet-committed projection rows
to represent omitted/wrong inserts; this does not relax production append-only rules.
No SQLite/mocked SQL/create_all fallback is used. The tests have only been collected
locally: PostgreSQL execution, combined migrated-schema tests at supported isolation
levels, concurrent write-skew/replay checks, upgrade and cold restore remain owner
acceptance gates. Do not report collection or static provenance checks as SQL success.

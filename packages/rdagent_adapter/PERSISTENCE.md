# Disabled RD domain integration closure proposal

This is a proposal, not deployed schema/authority. The draft model module is not
registered, routes/dispatcher are not enabled, and service helpers call the
existing neutral transaction guard, which rejects the new purpose. PostgreSQL,
crash/restore and hostile candidate tests have not run for this draft.

## Fixed domain sequence

1. Immutable FactorResearchSpec, then current AttemptBinding. Fresh-only service
   currently rejects all resume. A resumed attempt must independently read the
   full prior checkpoint closure; never recover arbitrary native pickle/RAG state.
2. GenerationStart binds exact spec, step/task, RD ResearchOperationContext and
   already reserved engine_mutation grant (exact BudgetVector mutation_slots=1).
   Budget remains run-global across attempts. Generation starts are unique by
   run/attempt generation/step/task: an abandoned incomplete step can be retried
   after fencing with a new charged mutation; completed checkpoints cannot fork.
3. FactorUpstreamCall groups one actual upstream outer call, scoped by
   generation-start/context/phase/kind and host-allocated ordinal. It is not a
   broker request operation. Explicit phases: generation, output_format, critique,
   final_decision, knowledge_query, knowledge_update. Knowledge query begins
   after durable GenerationStart and before source generation.
4. FactorGenerationModelOperation records every inner request in the call with a
   host ordinal and same-call predecessor. Initial has no predecessor;
   continuation requires observed previous finish_reason=length; semantic repair
   requires a bounded actual parser/schema failure receipt. Different requests
   always have distinct immutable ModelOperations. Physical network retries keep
   one operation/request hash and separate ModelTransport ordinals.
5. ResponseAssembly + ordered AssemblyParts bind all response objects used by the
   actual parser/extractor, not just the last continuation's text. Every part
   follows Outcome→Transport→Operation→membership in the exact call. All failed
   repair requests remain in call history even if not selected into final source.
6. GenerationSource binds exact generation call+assembly to actual immutable
   FactorVersion/code hash, before any candidate workspace injection/admission.
   Invalid syntax but bounded nonempty Python is still a retained source version;
   malformed or rejected empty/oversized proposals are distinct bounded outcomes.
7. Each CutoffExecution binds source and frozen prefix projection plus candidate
   process admission. It must match manifest input hashes and fixed profile. A
   fresh isolated state per cutoff prevents future-data carryover. Each outcome
   separately binds stopped/extracted HDF, normalizer admission and typed numeric
   Parquet. Candidate receives no IPC or model/DB authority. Host assembly validates
   primitives and chooses availability from input/membership/preprocess/lag/event.
8. Publish stitched snapshot only after every expected cutoff and object/row
   inventory closes. Retain full static universe with explicit missing outcomes;
   candidate-reported missing is not a proven causal diagnosis.
9. Evaluation feedback is NOT generation completion: upstream knowledge-update
   model calls happen after feedback, then actual CoSTEER adoption/fallback
   decision. Final GenerationOutcome/checkpoint must wait for those operations,
   source selection and budget/process/object closure. Snapshot validity alone
   does not imply adoption. Call the real upstream decision method, never replace
   the search loop with a platform rule.

## Shared migration/API requirements (owner coordination)

- Extend only the explicit `(factor_research,rdagent)` pair, retain old closed
  registries and historical codec validators. No nullable legacy strategy DTO.
- RD context version `mineagent.rdagent-context.v1`: spec_sha256,
  input_sha256, logical_step 0..7, task_index exactly0. Context bytes/hash immutable.
- Engine broker versioned codecs supplied separately by owner. Required
  response_format is hashed; response stop/length preserved. Strict bounds,
  no silent prompt truncation; source retention cap is a separate 128KiB bound.
- Reservation purpose `engine_mutation`, exactly one mutation slot. Add measured
  domain-start settlement proof without changing N1 historical refund rules;
  no refund after durable start/source failure. Until supported, held reservation
  counts against the same run-global budget and cannot be reset on restart.
- Unique factor_versions(owner_id,id,code_sha256).
- Unique research_model_operations(owner_id,run_id,attempt_id,generation,id,context_id).
- Unique factor_objects(owner_id,id,object_kind,sha256); new `native_hdf` kind with
  application/x-hdf5 media and .h5 storage suffix. Existing native_evidence is
  JSON-only and must NOT be used to disguise raw HDF. Existing values_partition
  remains Parquet and exact-kind binding prevents swapping attachments.
- New bounded JSON research-object kinds: factor_response_assembly, factor_parse_failure,
  factor_generation_proposal, factor_generation_feedback, factor_adoption_receipt,
  factor_row_assembly.
  Exact media/storage identity and aggregate object budgets apply. Do not widen
  model_response into an arbitrary artifact or accept unknown kinds.
- Include every new domain table/object in cold backup/restore and post-restore
  closure verification. Append-only triggers, owner/current-attempt checks,
  advisory storage gates and ordinary rollback/uncertainty handling remain required.

## Required deferred/direct-SQL checks, not replaced by Python helpers

- Exact spec/run purpose/engine/config digest + complete attempt binding.
- Start context and mutation reservation exactly match spec/step/task; previous
  completed checkpoint or prior same-attempt outcome chain, no gaps/forks.
- Call context/phase and all request codec/profile identities; request ordinals
  contiguous, predecessor ordinal exactly n-1, observed finish/parse disposition
  authorizes continuation/repair. A same-owner unrelated model response is invalid.
- Assembly part count and ordinals exact; assembly.call==part.call; each completed
  response object/hash is exactly the selected outcome's response; transport's
  operation is the member's operation. GenerationSource call phase is generation.
  Assembly records parser/adapter identity and all selected response parts/source
  bytes so continuation extraction is reproducible, not inferred from last response.
- Process admissions bind candidate/normalizer role, fixed image/dependency/profile,
  frozen input hashes and current attempt. Successful process exit alone is not
  stopped identity/extraction/hash publication proof. Zero fabricated evaluator
  usage; repeated value reads reuse one actual evaluation/cutoff receipt.
- Cutoff objects kind/hash match extraction/normalization receipts; row counts,
  host assembly digest and complete snapshot inventory match exact frozen source.
- GenerationOutcome requires full call/process/budget/object closure and actual
  adoption receipt, not only evaluator final_decision. Checkpoint completed_step
  exactly equals last outcome's start.logical_step (now a composite FK chain),
  JSON history closes every prior generation; no terminal continuation.
- Current attempt cannot mutate/reuse old attempt's live authority. New attempt
  can reference immutable completed checkpoint evidence only through verified
  resume mapping; old processes, unresolved model transports and staging objects
  remain conservatively charged until authoritative stop/non-submission proof.

## Current draft implementation status

Exact source hash, assembly/call/member scope, typed HDF/Parquet attachment and
checkpoint-step FKs are present in draft models. Offline tests compile/check
these constraints. `begin_generation` and `open_upstream_call` implement scoped,
flush-only, idempotent DB transitions behind the disabled guard; they consume no
remote transport and do not fake missing grants. Remaining transitions require
shared codec/object/process registration and real PG integration tests before any
activation. None of these drafts is part of the frozen compatibility-runtime cut.

### Still-missing source publisher (not hidden by source binding)

`bind_generation_source` only verifies and binds a preexisting real FactorVersion;
it is NOT complete generation publication. Operational creation needs a new
flush-only fenced factor publisher, research-global storage reservation and
factor physical-storage locks in the established maintenance lock order. Never
invoke a legacy helper that internally commits/rolls back inside a neutral
transaction. If bytes are staged but the fence is lost, keep conservative orphan
storage charge/recovery evidence; do not publish a stale-attempt domain row.

### Follow-up distinctions now modeled

Generation outcome now distinguishes evaluated source, adopted/fallback source,
and continuation workspace source, each through exact same-run source/version
FKs. Current-generation source creation is separately recorded by GenerationSource;
evaluation may reuse older immutable source only with authenticated continuation
lineage/verified resume closure. Next parent is the continuation workspace, not
assumed to be either newly generated code or final adopted fallback. A typed
factor_adoption_receipt and actual upstream step boundary must close this choice.
Mutation reservation is now one-to-one with GenerationStart in SQL and service.

FactorEvaluationStart is a durable logical ticket (one per generation in this
profile); repeated upstream value reads must use that ticket's existing completed
snapshot/result, never re-enter candidate execution. Every CutoffExecution binds
its exact ticket/start/evaluated-source/version tuple. EvaluationOutcome is
separate from generation adoption and requires full snapshot/cutoff closure.

Atomic request admission draft accepts exact canonical bytes plus a scoped replay
nonce, checks the prepublished request object, host-allocates IDs and ordinals,
and creates ModelOperation+membership+initial ModelTransport reservation in the
same fenced transaction. No placeholder ModelOperation or local-UUID authority.
The compatibility mock bridge's two-call identity seam is not operational RPC;
a separate future atomic submit protocol must replace it without new unclosed
grant state. Existing objects may remain conservatively charged if the atomic
transaction aborts. This draft has not passed real PostgreSQL constraints yet.

Snapshot verification needs a distinct isolated-prefix recomputation kind/profile
with source_pit_verified=false, never fixed_fixture_recomputed or verified_source.
Also select the explicit public/export inventory policy for raw native_hdf versus
a typed JSON cutoff inventory referencing separately preserved HDF; no binary HDF
under the legacy JSON native_evidence kind. The integration owner owns closed
codec, export/import and restore revisions.


### Source publication draft progress (still disabled)

`rdagent_source_publication.reserve_source` now validates assembly and retained
source identity, checks factor physical inventory plus all unresolved RD peaks,
and flushes a one-to-one start/storage reservation pre-admission under exact
`factor_objects`. That durable pre-admission closes the generation call to new
request members. Its fixed bounded content-addressed journal fits existing JSON
physical inventory. `finalize_source` verifies journal/source bytes, exact assembly,
retained factor storage guard and current fence, then flushes Factor/Object/Version,
response assembly/source and publication outcome without commit/rollback or writes.

The external writer now retains maintenance and factor FD guards across fresh
pre-write transaction, exact journal/code writes, and freshly fenced metadata
publication. It re-admits capacity and blocks every crash-left temporary, unsafe
link or oversized inventory; it never deletes ambiguous evidence. Mocked crash
boundaries and real small filesystem inventory tests pass, not hosted PG/crash
acceptance. Optional source-specific closure verification permits only existing
conservative_unknown FULL-peak settlement, never a measured refund. Measured
cleanup/settlement and restart recovery acceptance remain unfinished; existing N1
research-object measured proof must not be reused.
Legacy publishers can consume free physical headroom between RD transactions, so
future writer must re-admit capacity before every write, never treat pre-admission
as a capacity guarantee. New rows need append-only/deferred source/start/call,
version allocation, journal and outcome closure and backup/restore registration.
Checkpoint draft now preserves evaluated, adopted and continuation source/version
pairs plus adoption receipt hash; this is not authenticated resume acceptance.


### Operational controller identity (excluded draft)

Fresh-only controller profile admits exactly `/input/bootstrap.json` as its sole
non-IPC input; code comes from the pinned image. Its canonical bytes hash is the
complete admitted input commitment, not one arbitrary file from a larger mount
set. Source/checkpoint/market input mounts are forbidden. Any additional bytes
require a new versioned complete inventory/profile. IPC mount/ticket is supplied
and authenticated independently by the supervisor, never encoded as authority in
bootstrap JSON. Operational bootstrap/entrypoint/profile acceptance remains pending.

FactorControllerLaunch binds one exact admission per attempt to host-derived
spec/attempt bootstrap and logical operation equal to AttemptBinding.id. Each
live event revalidates the shared kernel-peer resolver against locked persisted
launch/config, not request-supplied admission fields. Historical source receipts
check exact persisted launch identity but are not themselves live authorization.

FactorUpstreamCallOutcome freezes ordered member/response outcomes after an
authenticated actual exchange boundary. Completed calls require all physical
attempts contiguous, prior failed and last completed, all outcomes/reservations
settled, and final chat finish_reason=stop. Source publication requires a completed
generation exchange; terminal call membership cannot grow. The explicit service
read cap is256 physical attempts per request; exceeding it fails closed, not
truncates. Deferred direct-SQL accounting closure remains mandatory.

### Fresh evaluation ticket and exposure history

`open_generation_evaluation` now verifies current generated source/version/definition,
owned dataset identity/window, exact calendar digest/timezone and bounded cutoff/row
count, then flushes one ticket with canonical SnapshotInput. Its materialization
profile is `mineagent.rdagent-isolated-prefix.v2`, environment is the digest of full
RuntimePins, and adapter version comes from frozen config. Repeated reads reuse
that ticket and its frozen history; they do not resample mutable later exposure.
Historical/RAG-source reuse remains rejected pending exact verified lineage.

Known host-read `locally_recorded_claim` envelopes are mapped only after reloading
owner-bound DataUsageExposure and DatasetVersion, comparing exact encoded row
bytes/hash and dataset content. Original evidence UUID/window/purpose/hash survive;
prefix-provided and actual-computation purposes are never conflated. Completeness
stays partial/unknown and identity mapping unresolved. Absence is not clean history;
unknown imported envelopes fail closed. Dataset input-file byte verification and
membership provenance still require the fixed provider admission path.

Evaluation tickets confer no process/snapshot authority. Actual cutoff admission,
protected numeric candidate launch, normalizer extraction, storage publication,
owner-authenticated v2 snapshot closure and evaluation feedback finalization remain
required before generation adoption/checkpoint issuance. Pure v2 evidence DTOs
must not be substituted for a verified snapshot or stopped-process receipt.

## Additional disabled input and boundary witnesses

`rdagent_input_models.py` / `rdagent_input_services.py` are separate, unregistered
from the original model snapshot. Candidate inventory persistence requires actual
persisted v2/no-IPC admission columns; the closed v2 persistence gate is not bypassed.
It rereads the real FactorVersion code object and three current-attempt completed
publication objects (`factor_prefix_manifest`, `factor_prefix_listings`,
`factor_prefix_observations`). Those new object codecs/kinds are not registered yet.
The complete sorted InputBinding list digest, not the prefix JSON digest, must
match the process admission. One append-only header and four exact object/file
references are flushed. Replay validates full scope, execution, ordinal0..3,
ordered destinations and object refs before dictionary comparison. It creates no
objects, reservations or process grants. Normalizer input persistence remains
absent until a genuine candidate stop/extraction/HDF-publication witness exists;
using a same-owner HDF object or the later CutoffOutcome would respectively be an
unproven link or a circular dependency.

`rdagent_boundary_models.py` / `rdagent_boundary_services.py` persist actual fixed
controller observations separately from final verified generation outcomes. Every
event reauthenticates the current controller and current fence. All issued calls
must already be completed, including knowledge-update embedding. A new decision
selects only the current source or previous authenticated adopted lineage; the
continuation source is the actually evaluated workspace source. A subsequent
advanced/returned event is a distinct append-only boundary. A decision alone does
not complete a step. The immutable qualification is explicitly
`authenticated_observation_not_execution_verification`. No accepted outcome,
snapshot, checkpoint, budget settlement or resume admission is produced.

Generation writers reject new call/source/publication admission after an observed
decision, but preserve exact immutable replay before terminal/observed guards.
This service ordering requires corresponding deferred SQL closure before any
activation: no new call/member/source after observation, all observation source IDs
in the canonical JSON must resolve to exact authorized lineage, one complete file
inventory per admitted cutoff, publication-outcome exact object linkage, and
append-only protection for all four new tables. The offline44-table DDL snapshot
does not contain these four new tables and must be deliberately regenerated and
reviewed. No direct-SQL/PG/concurrency/restore acceptance is claimed by the focused
service/storage-double tests.

## Retained-response source derivation (disabled)

`rdagent_source_witness_models.py` adds two unregistered append-only tables, separate
from the original model snapshot. `rdagent_source_witness_services.py` derives the
witness from actual retained assembly/response/code object reads, never a submitted
parser result. It validates the completed generation-chat call, selected final
continuation chain and recorded controller, uses the pinned upstream JSONParser
semantics plus `Dict[str, str]` validation, and compares extracted `code` exactly
with the assembly and FactorVersion. Source reservation performs this comparison
before external source publication; binding rereads and persists the witness in
the same fenced transaction. Existing-source replay requires an existing witness;
it cannot silently backfill historical rows.

The parser profile is `mineagent.rdagent-source-parser.v1`. Its copied class and
MIT provenance are recorded in `response_parser_provenance.json`. A separate
installed-only differential probe compares actual pinned upstream parsing over
24 valid/repair/continuation/think/duplicate/invalid cases. The historical profile
keeps `reasoning_think_rm=False`; repaired JSON is not silently canonicalized.

Each witness stores only hashes, exact byte counts and ordered references to
already charged immutable response/source objects. It does **not** store another
copy of raw response content, concatenated content, normalized JSON or source.
At most six response parts are admitted; reconstructed raw UTF-8 is bounded at
786432 bytes, normalized JSON at2359296 bytes and source at131072 bytes. Parsing
memory is bounded by these limits; metadata does not stand in for charging any
future retained payload copy. Normalized bytes remain deterministically
recomputable from retained object bytes and pinned parser identity.

The SQL fragment can verify relational lineage, exact object identities and the
host witness's bounded metadata digest. SQL does not prove that Python actually
read physical bytes or executed this parser; that is the explicitly trusted host
service boundary. Service tests use real retained files but database/ledger
identity doubles. Neither those tests nor offline SQL generation establish real
PostgreSQL, concurrent fencing, restore or operational engine acceptance. The
original offline44-table catalog excludes these two tables as well as the four
input/boundary tables above; future registration must include all six explicitly.

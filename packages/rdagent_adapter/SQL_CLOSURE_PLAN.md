# Disabled RD semantic SQL: smallest implementation order

Review input only. No registration, SQL execution or activation; the model snapshot
is unchanged. Model FKs and offline DDL are not semantic closure. All checks below
must be deferred, initially deferred, and attached to every participating changed
table, including shared rows whose terminal state can change. A run-local check
cannot replace immediate unique arbitration for shared reservation consumers.

## 1. Generation source publication (first executable fragment)

For each `factor_generation_sources` row, join exact owner/run/attempt/generation/
start/context/call/assembly tuples (not IDs alone):

- upstream call phase is `generation`, kind `chat`; matching
  `factor_upstream_call_outcomes` is completed, with frozen ordered member/response-outcome ID arrays;
- member request ordinals are contiguous, predecessor/relation belongs to the same
  call; each exact operation is completed and response points to its completed
  outcome and retained `model_response` object;
- assembly has exactly `part_count` rows, contiguous ordinals and correct selected
  continuation-chain membership; semantic-repair abandoned responses cannot be
  silently included. Last used response has `stop`, preceding continuations
  `length`. Versioned response JSON bytes need a verified storage witness: SQL
  object IDs/hashes alone do not prove decoded finish_reason or assembly content;
- `factor_versions.owner_id/id/code_sha256` equals source identity; its
  `code_object_id` joins same-owner `factor_objects` with kind `code`, media
  `text/plain`, matching digest and byte length. Publication/outcome must bind
  that exact source/assembly and reservation consumer, not any same-owner object.

Prerequisite target unique `(owner_id,id,code_sha256)` on factor_versions and
`(owner_id,id,object_kind,sha256)` on factor_objects is absent from old schema.
Do not widen old factor object kinds or source/profile identities merely to make
this fragment install. Cross-table kind check is required: old code-object FK
binds hash/owner but not `object_kind`.

Direct-SQL negatives: source from critique call; member outcome of another
operation; truncated/duplicate assembly; terminal call appended member; matching
hash object of wrong kind; foreign owner/source; publication using another held
reservation. Include valid terminal replay and source retained after failed eval.

## 2. Evaluation and per-cutoff processes

An evaluation binds its exact generated source/start and canonical snapshot input
(full runtime pins/materialization v2). `expected_cutoffs` must match the frozen
calendar/window. Each cutoff ordinal is unique and contiguous on successful
closure, not merely within 0..4095.

Candidate admission: same scope, role `factor_cutoff_candidate`, fixed candidate
profile/image from frozen spec, logical_operation_id = cutoff execution ID,
version2/no-IPC/null ticket, input digest = **complete mounted input inventory**.
`projection_sha256` hashes prefix JSON, not that inventory; they must never be
compared as if identical. Current cutoff row has no persisted full inventory
witness, so SQL cannot yet derive this relation. Add reviewed immutable witness
before declaring cutoff closure. Normalizer requires corresponding exact role,
profile/image, execution ID and inventory binding retained HDF + listings +
manifest. No placeholder v1 ticket.

Successful cutoff requires both real stopped/settled admissions, protected output
extraction receipts and exact same-owner `native_hdf`/`values_partition` objects
with digests; typed DTO declarations and file hashes are not stopped evidence.
Successful evaluation requires all cutoff outcomes and verified stitched snapshot
matching factor version/input/output digest. Native HDF registration and v2 stop/
publication witnesses are presently missing: this fragment must remain disabled.

Direct-SQL negatives: candidate uses orchestrator role; right role/wrong image;
prefix digest substituted for input inventory; wrong cutoff HDF; unstopped process;
foreign owner or swapped HDF/Parquet kind; missing cutoff; snapshot from another
input or same-owner version. No fake-success fixture issuer.

## 3. Actual adoption, boundary and checkpoint

Generation outcome's evaluated source must correspond to that generation's real
evaluation. Adopted and continuation sources are separately bound: each is either
the current source or authenticated earlier same-run lineage, never future/foreign
source. `accepted` must reference a valid evaluation, but valid feedback alone
cannot authorize adoption. Require actual controller-bound immutable adoption and
step-boundary receipt after upstream knowledge update. Next generation parent is
previous **continuation** source; checkpoints bind last outcome/logical step and
actual retained source/workspace/feedback history. Fresh-only runtime rejects
resume until separate reconstruction closure is implemented.

The current schema has no fully authenticated boundary/finalization witness.
Keep this obligation explicit rather than inventing a SQL assertion from an
untrusted adoption object alone. Direct-SQL negatives must cover future-source
adoption, valid-but-unadopted feedback, wrong next parent, interrupted knowledge
update, checkpoint ahead of boundary and cross-attempt receipt substitution.

## PostgreSQL execution requirement

Use an isolated test database/schema with exact neutral migrations plus explicitly
reviewed draft prerequisite DDL. Install fragments only there; direct INSERT/UPDATE
must bypass services and exercise `SET CONSTRAINTS ALL IMMEDIATE` and commit.
Run read-committed/repeatable-read/serializable concurrent mutations; assert exact
constraint failures, never catch-and-skip. Local collection is not execution.
No PostgreSQL or Docker acceptance has been performed for this plan.

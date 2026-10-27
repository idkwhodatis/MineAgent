# Revision022 engine-result release: implemented locally, hosted acceptance pending

The composed022 successor to frozen `be8322e` registers authenticated release,
values, export and analysis routes in `backend/app/engine_result_routes.py`, using
`engine_result_release.py` and the existing exposure ledger. Initial design was
prepared 2026-10-26 15:24 UTC; implementation and local validation followed. No
new release table or execution capability is introduced. This is not a claim of
completed hosted PostgreSQL, Docker or browser acceptance.

## Existing authority and transaction semantics

`experiment_services.record_exposure` takes the owner `submit:` advisory lock,
checks owner resources, computes actual identities, and inserts/flushes the existing
append-only `DataUsageExposure`. A repeated `(owner_id, source_key)` is accepted
only when dataset, extent, purpose, identities and provenance agree. The caller
must commit before returning any exposed content. It does not compare `run_id`
itself, so keys and provenance bind run and resource IDs explicitly. The release
wrapper additionally requires the returned ledger row's owner/run/experiment/split
foreign-key bindings to match before committing, including on GET replay.
Do not change legacy helper behavior or create a parallel release ledger.

Existing seal semantics are exposure accounting, not a global data-read ban or
holdout unlock. `get_exposure_snapshot` / `classify_exposures` classify overlapping
identities and dates as `previously_exposed`. A new release cannot make an exposed
holdout clean, issue a legacy FinalSelection, or establish out-of-sample validity.

The configured worker already owns neutral factor-object locks and therefore uses
an equivalent exact-replay ledger writer without reacquiring the owner advisory
lock in reverse order. Public release must establish owner lock ordering before
run locks; do not call the legacy helper inside an already-held worker fence.

## Implemented public contract

- Explicit authenticated POST release for an owned execution snapshot or direct
  analysis; separate typed resource IDs, never a legacy FeatureSnapshot alias.
- No client-supplied window, checksum, dataset identity or proof. Resolve all
  bindings from immutable actual owner/run/source/snapshot/plan/analysis graph.
- Require native historical reader: owner-bound terminal run, exact complete DB
  graph, current shared storage guard and retained bytes. Historical integrity is
  not live execution authority and does not rewrite old inode provenance.
- Use deterministic key `engine-result:{kind}:{resource_id}:{role}:v1`, with roles
  `factor` and (analysis only) `label`. Provenance must bind run/resource IDs,
  actual dataset identity/hash, snapshot manifest/input/output hashes, and for
  analysis its plan/protocol/objective/label/result/measurement hashes.
- Record conservative full actual factor and label dataset extents, including
  future labels/warmup. Even same-dataset roles retain distinct deterministic keys.
  Do not expose row/valid/defined counts, values or metrics before release.
- Transaction must atomically commit all required role rows before acknowledging
  release. Stable returned exposure IDs allow lost-response replay. If any role
  conflicts, rollback all new rows; no response content may escape.
- GET requires all exact existing receipts and repeats historical integrity checks.
  Missing receipt returns a release-required error without implicitly inserting.
  Existing receipts may be validated using the existing idempotent writer after
  establishing existence and correct lock order. A receipt is neither proof that
  files remain present nor a substitute for current byte verification.
- The implementation uses two bounded phases with no nested independently owned sessions: first
  privately verify and buffer immutable results under the historical reader's own
  shared guard; then acquire owner-submit-first transaction and recheck every
  immutable owner/run/resource/hash binding before recording release and commit.
  Only the already verified private buffer may then reach the response, never a
  fresh unguarded file read. Lost replies can conservatively retain exposure.
  The helpers return detached immutable evidence; no caller-supplied verified flag
  is accepted. The phases are independent, with no nested guard ownership.

## Validation and remaining acceptance

Nine local ledger contract tests exercise the *actual existing* writer and
classifier with inert persistence/locking: exact replay, changed run provenance,
hash, window, dataset and purpose, foreign dataset/run owner, full future-label
extent classified exposed, and half-open nonoverlap. These do not prove PostgreSQL
atomicity, concurrency, routes, native bytes, release policy or browser behavior.

Six additional inert-persistence tests cover phase ordering, missing receipt,
failed commit/binding, exact replay and wrong ledger run binding. The configured
workflow invokes a real completed-result Chromium case after storage relocation;
its follow-up HTTP helper checks values/export, exact replay, foreign ownership
and full factor/future-label exposure. These hosted gates have not run locally.

The broader real-PG/HTTP negative-case review must account for: wrong owner/run/dataset/engine/version,
nonterminal/missing graph or bytes, relocated retained history, receipt missing,
changed immutable evidence, rollback on second-role failure, concurrent duplicate
release, lost POST response then retry, and future-label exposure classification.
The required completed-result browser covers explicit release, reload, Back and
foreign-owner denial. Existing Caddy coverage preserves prefixed history/form
navigation; it does not by itself prove completed-result release under a prefix.
Real browser response-loss and full concurrent release/rollback acceptance are
not established by the local unit tests or ordinary successful HTTP replay. No mock
completion or creation-enabled claim may substitute for this gate.

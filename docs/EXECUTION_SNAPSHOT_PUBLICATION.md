# Execution snapshots (revision 021, composition in progress)

This is a new `mineagent.factor-execution-snapshot.v1` protocol, **not** legacy
`feature-snapshot.v1` admission and not the disabled prefix-evidence DTO. It does
not set `verified_source`, establish upstream point-in-time truth, activate a
provider, or permit arbitrary code. The dependency claim remains
`provided_not_proven_read`.

## Authority and completeness

`publish_execution_snapshot` accepts an evaluation ID, not caller-supplied rows,
bytes, manifests or stop attestations. A fresh `factor_numeric` transaction
resolves the maintained immutable calendar, complete pre-execution evaluation
and exact ordered cutoff set. Every cutoff is read through the actual stopped
normalizer/report resolver, bound to its candidate HDF publication, full input
inventory, current source/witness, whole-unit stop and protected extraction.

The host parses only bounded primitive JSON. It does **not** load HDF, Parquet,
pickle or a candidate module. Host availability/lag/membership rules assemble
`FeatureRow`s; every cutoff must be present before a snapshot is materialized.
One cutoff is a complete result only when the declared output window actually
contains exactly that one session. Missing cutoffs are not partial success.

The new manifest retains one opaque normalized Parquet partition per cutoff and
a canonical host-assembled JSON row document. It does not fabricate a stitched
Parquet object or make old input-hash shortcuts: numeric input commitments bind
the entire persisted input inventory.

## Publication and accounting

The storage boundary holds the installation lifecycle admission and the existing
factor filesystem lock across fresh database transactions. A unique evaluation
and shared `engine_object_reservation_consumers` claim are committed before any
external write. Snapshot storage is another consumer family, not another quota
or a parallel per-table uniqueness check.

The pre-admission contains the complete journal commitment and conservative
peak: twice all declared object sizes plus 4 MiB for journal temporary/final
bytes. No existing final byte is credited. Legacy, source, candidate-HDF and
snapshot writers count all unfinished snapshot peaks, including other owners
and lost attempts, under the same quota lock order. Every finalization rereads
actual stopped outputs and actual stored bytes in a fresh current-fenced
transaction. Unknown outcomes retain all bytes and the full charge.

Journals are content-addressed JSON in `factors/<owner>/objects/`; the journal
payload omits its own address/hash to avoid a circular commitment. Its semantic
registration is in dedicated execution tables. A legacy JSON object with the
same physical bytes is not execution authority. Old generic backups may retain
opaque orphan bytes but cannot register this snapshot protocol.

The profile caps a complete snapshot at 64 cutoffs, 100,000 assembled rows and
64 MiB declared physical objects. Individual normalized partitions and assembled
JSON are capped at 16 MiB; the manifest is capped at 1 MiB. Capacity exhaustion
fails before publication, never truncates or silently skips output.

## Evaluation outcome and restore

Valid execution evaluations use a separate `factor_execution_evaluation_outcomes`
table whose snapshot foreign key targets a **completed** execution outcome.
Legacy `FactorEvaluationOutcome` columns, foreign keys and verification meaning
remain unchanged. One immediate evaluation-result registry arbitrates old
invalid results and new valid results. The new result binds the exact controller
launch, authenticated generation boundary, source/evaluation and feedback.
A snapshot is deliberately independent of a final generation outcome; the final
RD boundary consumes it, avoiding a circular admission dependency.

Restore dispatch is explicit: research closure v6, RD source closure v3, native
closure v2, then execution-snapshot closure v1, then final RD generation links.
Historical research/native versions reject the report extension. Opaque Parquet
hashes, protected primitive report, full source/native/normalizer references,
host row assembly, physical journal and full conservative settlement must agree.
Unregistered or partial files are retained, not promoted or deleted. No garbage
collection authority is added.

## Acceptance boundary

Local contract, opaque filesystem and structural tests are not PostgreSQL,
container execution or cold-restore proof. Revision 020 controller composition,
revision 021 migration, exact-head real PostgreSQL/Docker execution and an actual
stopped-controller backup/restore drill are required before this slice can be
accepted. No paid provider, deployment or default dispatcher activation is part
of this implementation.

Downgrade is conservative: it rejects snapshot/result evidence, typed final
artifacts and report extraction receipts. It also reconstructs the exact old
normalization-v1 inventory for **every** retained normalizer intent, including
pre-claim intents. Report-v2 or unclassifiable inventories block downgrade;
compatible historical v1 inventories are not reinterpreted or deleted.

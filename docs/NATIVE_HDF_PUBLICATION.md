# Per-candidate opaque HDF publication (019 integration)

This is an internal, pre-normalizer storage boundary, not an API route, engine
activation, HDF parser, completed factor snapshot or deployment instruction.
Historical `native_evidence` remains JSON in `factor_objects`; native HDF uses
separate kind-qualified tables. Revision015/016 capabilities are not broadened.

## Authority and transaction order

1. The caller reserves exactly the conservative storage vector with logical key
   `native:<publication UUID>` under the existing current-attempt budget boundary.
2. `admit_native` retains maintenance admission and the same factor-directory
   nonblocking FD lock as legacy factor publication. A fresh `factor_objects`
   transaction resolves the real scoped RD source/read witness, pre-execution
   evaluation/cutoff, source-derived SnapshotInput and actual numeric admission.
   It rereads protected output and exact persisted launch/whole-unit-stop/MANU2
   extraction receipts. It never accepts a detached receipt as physical proof.
3. Pre-admission records a single HDF identity, immutable journal and peak quota
   of twice the HDF size plus twice the fixed 64 KiB journal ceiling (eight
   conservative filesystem entries). This needs no placeholder reservation ID.
   The shared reservation-consumer registry arbitrates immediately across
   research-object, RD-source and native-HDF writers. Physical inventory includes
   all owners, orphans and directories, plus every outstanding source/native peak.
4. `publish_native` reacquires fresh SQL authority, while retaining lifecycle and
   FD guards across both short transactions and opaque atomic writes. Its journal
   is written first, then bounded HDF bytes, then both are reread. Finalization
   resolves source and protected numeric output again before committing the
   native object, outcome and full conservative charge. No refund is inferred.
5. `read_completed_native` is the normalizer-input boundary: a pending journal is
   not enough. It requires a completed exact DB outcome and current source/stop
   authority, validates full retained charge and rereads actual immutable bytes.

An exception, expired fence or uncertain SQL commit leaves pre-admission and all
retained bytes charged. Existing temporary files are recovery barriers. Exact
completed replay performs physical reads only. No cleanup or garbage-collection
capability is added. Missing outcomes independently block terminalization/resume.

## Separate journal format

The disabled candidate-intent v1 remains unchanged. Its journal is in parser
`staging`, which must remain a recovery barrier. Publication v2 instead uses
`factors/<owner>/native-journals/<publication>.json`, avoiding fake parser stop or
abort records. Legacy inventory recognizes this namespace and `.h5` only under an
explicit native schema capability. Legacy parser quiescence checks are unchanged.

The new intent contains only persisted source/start/version/code and exact
candidate input/cutoff/receipt/output references. It does not require a normalizer
result or successful evaluation outcome. `FactorEvaluationStart` is authenticated
as a pre-execution input ticket, never confused with successful evaluation.

## Cold closure

A separately versioned native closure must compose with validated research and
RD-source closures. It verifies the exact registered journal, source/cutoff,
process receipts, storage reservation/consumer, outcome, full charge and physical
HDF hash/size. Native bytes never enter a JSON/HDF parser on the host. Opaque
unregistered HDF orphans and uncertain temporary bytes remain retained, not
promoted to registered evidence. Generic older backups already retained opaque
`.h5` orphans; that never implied native semantic registration or writer support.

## Validation status

Local filesystem, contract, row-decoding and existing-factor regressions are
executed locally. Genuine PostgreSQL constraint/arbitration tests are written
for hosted execution; local collection is not a PostgreSQL pass. Composition with
017 numeric receipts and 018 real RD source/run identity precedes 019 acceptance.
Actual container execution, crash/fence/concurrency/cold-restore integration and
normalizer consumption must pass on the exact composed head before acceptance.
No paid provider, production deployment or additional repository is involved.

## Atomic normalizer consumption

`factor_numeric` is an additive scope with the identical established combined
lock order. It explicitly supplies process and factor-object capabilities;
`factor_objects` still cannot admit processes. In one retained-guard transaction,
the issuer persists a normalizer intent, calls `claim_native_normalizer`, then
claims the one-shot numeric launch. The append-only input row has immediate unique
publication/admission/reservation identities. It binds the exact completed HDF,
source/cutoff and full normalizer input inventory, including the distinct
`factor-normalization.v1` manifest. A fresh post-commit transaction must call
`require_native_normalizer_claim` before launch; a prior byte read is not a grant.
No implicit commit or retry of an uncertain one-shot process is introduced.

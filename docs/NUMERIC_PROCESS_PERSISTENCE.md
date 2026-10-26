# Numeric process persistence boundary (017)

This revision makes two exact no-IPC roles representable in durable storage:
`factor_cutoff_candidate` / `mineagent.factor-prefix-candidate.v2`, and
`factor_normalizer` / `mineagent.factor-numeric-hdf.v1`.
It does **not** register an executable image, mint launch authority, expose a
submission/API route, or turn the application dispatcher on.

## Obligation and evidence

`persist_numeric_intent` is flush-only under the existing current fenced
`processes` transaction. It consumes an exact reserved process budget, stores the
v2 admission and immutable full input/source-object/prefix/cutoff intent, and
projects `launch_intended`. Replaying the same logical launch never authorizes
another physical unit. Its input graph must be resolved by the upstream durable
source service, not asserted by a network caller.

Numeric records have their own v2 grammar; there is no dummy v1 ticket or role.
The protected journal links launch → kernel/container/image identity → MANU2
extraction and whole-unit stop. EOF, extracted bytes, elapsed wall time, and a
normalizer result are not stop proofs. Failed units may have a stop without an
extraction; that permits containment accounting but never native publication.

The process reservation floor is 64 MiB plus 64 KiB: bounded input projection,
retained opaque output and atomic-write peak/control allowance. The eventual
issuer must enforce these physical bounds; schema admission alone does not do so.

`record_numeric_stopped` ingests physical immutable records under a current
fence. `resolve_numeric_output(session, fence, admission_id, storage_root=...)`
requires the complete exact persisted receipt set and rereads the protected
journal. `read_numeric_output` repeats that lookup then reads opaque bytes using
no-follow directory/file descriptors, regular-file/single-link/private-mode
checks, a 16 MiB ceiling, hash verification and pre/post file identity checks.
The returned `NumericOutputReceipt` is a description, **not** a reusable authority;
callers cannot pass it back to gain file access. No HDF/Parquet parser runs here.
Native publication consumers must retain lifecycle admission and their declared
combined quota lock order across this read, use a fresh fenced transaction at
publication, and add their own unique consuming claim and terminal barriers.

## Legacy and restoration

V1 commitments, IPC authentication and the neutral issuer are unchanged.
The historical 015/016 migrations and closure v1/v2 inventories remain frozen.
Closure v3 adds numeric journal/opaque output validation without executing or
resuming anything. Numeric identity and receipt links are checked independently
of v1 rules. Unknown/incomplete/active units still block maintenance. Downgrade
refuses any numeric admission or extraction receipt. Scoped receipt uniqueness
supports downstream exact admission/kind foreign keys.

## Explicit remaining gates

The actual numeric launcher/stop-and-reconcile issuer is a separate successor:
it must produce these records from host observations and protected output,
commit the intent before Docker mutation, retain original deadline and exact
identity through process loss, and never treat a caller-declared image hash as
acceptance. Until then the legacy recovery entrypoint intentionally rejects
numeric rows rather than silently treating them as v1 or pretending they stopped.

Structural/FD and closure mutation tests use labeled invented journals. They are
not containment evidence. PostgreSQL tests persist numeric obligations without
invented stop receipts, and must run on real PostgreSQL. Actual composed
PostgreSQL + Docker execution, recovery and cold restore remain required before
any production registration or new-engine entrypoint can open.

## Pre-activation integration corrections

The normalizer's `manifest.json` is the separate normalization contract, not the
original candidate prefix. Its full inventory hash remains bound, while
`inputs.prefix_sha256` retains the original prefix identity. Issuance must resolve
that prefix and the completed native publication, rather than equating the two
manifest hashes. The 60,000 ms process reservation floor is deliberately a
conservative accounting reservation; the numeric executable's original absolute
wall deadline remains at most 10 seconds (fixed acceptance uses 8 seconds).

Numeric identity records include the launch root device/inode. The terminal DB
barrier reconstructs the complete launch hash from the immutable intent and those
fields, rather than accepting two mutually matching arbitrary hash strings.
Numeric control readers enforce a 16 KiB bound after reading and compare inode
metadata before/after the read. Both output resolver variants verify the retained
opaque output; neither can return a usable output binding after file deletion.

OpenAPI/client revision enums and exact no-skip PostgreSQL/legacy-restore
inventories now include 017. These are authored gates, not a claim that hosted
numeric execution or composed numeric cold restore has passed.

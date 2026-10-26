# Disabled native HDF physical closure proposal

This slice performs real, read-only filesystem verification. It does **not** add
an active object kind, migration, publication writer, launcher, route, registered
process profile, budget settlement or cleanup permission. Revision 016 continues
to reject every v2 process admission. Its frozen SQL and historical revisions are
unchanged.

## What is verified locally

`backend/app/factor_native_storage.py` inventories a closed proposed factor layout
through no-follow descriptors. It hashes opaque bytes (never HDF/Parquet parsing),
checks single-link regular inodes, stable read identities, canonical owner and
operation UUIDs, file/byte/time ceilings, and observable directory identity. It
returns every encountered directory and file, including all owners, orphan HDF
files and incomplete temporary files, so directory/file quotas are not conflated.

An existing candidate output filename is not proof of its format or provenance.
The proposed restore check verifies only exact registered owner/address/hash/size
against physical bytes. It returns unregistered native files for **retention**,
not deletion. Every temporary file and staging operation is an independent
recovery barrier, including empty staging directories and journals claiming
`recorded`. No journal text can grant cleanup or reserve/release a budget.

The reader does not acquire the maintenance/SQL/filesystem writer guards. It must
be called while those existing guards are retained by the eventual integration.
An unlocked sequential scan cannot detect every subsequent change to a file
already closed while another is being read. Detected races fail closed; this is
not a concurrent-write or power-loss certificate.

The existing active `factor_services.physical_inventory` and FactorObject SQL
kind constraints still reject native HDF **registration/publication**. Generic
operations backup may preserve an unregistered `.h5` orphan as an opaque file;
that historical retention behavior is intentionally unchanged and does not imply
registered semantic restore closure. No claim of a new registered 015/016 native
backup capability is made.

## Noncircular pre-normalizer contract

The older draft `PublicationJournalV2` describes a completed evidence bundle
(source, candidate HDF, normalized and stitched values). It cannot be reused for
candidate output publication before the normalizer runs: doing so would require
the future result before publishing its input.

`CandidateNativeIntent` is a separate pending-only declaration with one HDF. It
binds the exact v2/no-IPC candidate admission, owner/run/attempt/generation/cutoff,
image/profile/full input commitment, source version/hash and source-object ID,
distinct whole-unit stop and protected-extraction receipt IDs/hashes, immutable
HDF identity, temporary address, reservation ID and journal address. No future
normalizer or stitched snapshot is required.

`CandidateNativePublicationWitness` describes future persisted publication and
outcome references. `validate_candidate_publication_references` checks exact
intent/object/journal hash agreement only. Self-consistent DTOs or arbitrary UUIDs
are **not** evidence that the referenced rows, receipt bytes, stopped unit or
committed transaction exist. There is deliberately no authority-bearing issuer.
The future normalizer must resolve those records under its current fence and
reread the immutable registered HDF bytes rather than trust these declarations.

Separate footprint helpers calculate conservative final plus temporary object
bytes and final plus temporary journal bytes, plus possibly-new directory entries.
They never reserve, settle, refund, delete or ignore uncertain physical copies.
The single-candidate footprint has no completed-bundle dependency.

## Required integration before activation

1. Accepted exact v2 role/profile/image/launcher and full mounted-input inventory,
   plus real candidate quiescence and independently verified whole-unit stop.
2. Immutable persisted stop/extraction receipts resolving the full admission,
   source/cutoff/input and opaque output hash/length; no caller-issued success.
3. Separate per-candidate publication and outcome tables with scoped/kind-qualified
   FKs, append-only/deferred semantic constraints and immediate reservation-consumer
   arbitration. Direct SQL must not bypass role, scope, replay or accounting.
4. Durable pre-admission before I/O; retained storage guard across fresh fenced
   transactions; actual bytes/journal publication; exact object/outcome registration
   and settlement only after independently reconciled completion. Unknown writes
   retain the full peak charge. No transaction held across file/native I/O.
5. An atomic new schema capability for native_hdf media/path constraints, shared
   physical quotas, process/extraction bindings, backup relational closure and all
   retained files. Older semantic closures must not silently accept new records.
6. Real PostgreSQL upgrade/direct-SQL/concurrent/replay/fence tests and real cold
   restore/crash-left journal tests. Then candidate→HDF publication→normalizer
   input must run through the actual authenticated host path. No such integration
   or hosted PostgreSQL/Docker acceptance is claimed by this local slice.

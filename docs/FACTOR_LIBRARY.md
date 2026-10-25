# Independent factor library: Stage 1

This increment is feature-gated until its exact-head PostgreSQL, Docker,
Playwright and cold-restore gates pass. It does not enable RD-Agent or OpenEvolve,
execute submitted factor Python, prove market point-in-time correctness, or place
trades. The existing legacy optimizer and stored schema identities remain intact.

## Evidence and interfaces

Factors and their immutable versions are independent of strategies and research
runs. A source version retains exact UTF-8 bytes, parameters, preprocessing,
universe and input semantics. A successful feature snapshot binds an exact
version, dataset content, input inventory, availability contract, environment,
output digest and complete physical object inventory. Definitions without a
calculation remain visible as not materialized, not as zero-valued results.

The fixed fixture route computes momentum, risk-adjusted momentum and
cross-sectional rank from explicitly synthetic observations. Saved samples show
actual inputs and calculation states, including warm-up, missing data and zero
volatility. Uploaded Python is preserved as source evidence and never executed
by this route. Direct-factor IC, model prediction analysis and portfolio results
remain separate and are not fabricated when absent.

The workbench exposes exact version/snapshot URLs, immutable history, bounded
actual values, saved samples, bilateral comparisons, bundles and exports.
Observational alignment, unit/scale comparability and controlled-protocol
comparability are separate. A plotted correlation or a source explanation is not
causal evidence. Declared strategy dependencies are not proof of factor access.

## Feature and runtime boundaries

- `FACTOR_LIBRARY_ENABLED` enables factor endpoints and navigation.
- `FACTOR_IMPORT_ENABLED` additionally enables bounded raw ZIP admission.
- `FACTOR_PARSER_IMAGE_ID` must be a locally built immutable `sha256:...` image ID.
- Defaults remain false. The API never receives Docker access. Existing trusted
  research workers perform isolated parsing; the existing reconciler resolves
  interrupted import admission and owned runtime state.

Build the parser with the dedicated `runtimes/factor_parser/Dockerfile` from this
exact checkout. The dependency lock is the existing API closure; do not merge it
with the evaluator, data or research runtime locks. This is a development/runtime
configuration description, not authorization to deploy or expand credentials.

## Package import

The package transport is deterministic, strictly bounded ZIP_STORED with an
exact member inventory. It preserves the original archive, source code, manifests
and physical objects. Import creates an immutable source-to-local mapping. When
UUIDs change, the isolated parser produces derived canonical numeric values and
a new digest. Original and derived bytes are never represented as interchangeable.

Only the isolated numeric parser reads untrusted Parquet. The parser receives
read-only input, no API/model/database credentials, no network, a read-only root,
a bounded private output filesystem and a bounded output stream. Protected launch
and stop journals are not parser-writable. A process return code alone is not
proof of containment or a committed database transaction.

The initial import profile accepts at most 10 MB, two snapshots, one numeric
partition per snapshot and 5,000 rows per snapshot. Excess capacity is rejected,
never silently truncated. Only the closed synthetic fixture source format can
be transferred as a new dataset in this increment. Otherwise an exact owned
local dataset binding is required for materialized import; unresolved source
identity remains visibly unresolved evidence, not executable ready data.

Byte/schema validation does not establish the source's PIT claims. Known exposure
claims are preserved and admitted against verified local identities where
possible. Missing or unmappable history remains unknown, including code-only
imports and subsequent exports. Reusing a factor does not reset exposure history.

## Durability and limits

Physical factor quotas count registered objects, temporary outputs and orphan
bytes. Defaults are 512 MiB per owner, 2 GiB per installation and 10,000 files.
Publication is no-clobber and guarded across database and filesystem lifetimes.
Unconfirmed runtimes prevent new publication and cold maintenance. Successful
stop evidence and successful database publication are different records; recovery
reconciles immutable committed results before retrying an interrupted request.

Migrations 012 and 013 extend rather than rewrite historical migrations 001–011.
Cold backup/restore validates independent physical factor references, exact
manifest closure, original/local mappings and exposure history, while retaining
legacy note and strategy-package pins. Unsupported future capability schemas
fail closed. Disabling a feature never deletes its saved evidence.

## Acceptance

The dedicated factor workflow requires real PostgreSQL owner/tuple/immutability
checks, actual isolated parser tests, authenticated browser journeys, and cold
backup/restore at the new schema plus an old-schema upgrade. Unit tests and test
collection are supplementary evidence. All legacy full-application, Compose,
native baseline and capacity gates must remain green on the same head.

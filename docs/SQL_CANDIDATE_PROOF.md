# Isolated SQL candidate proof

The `mineagent-sql-candidates.yml` workflow tests experimental SQL alternatives in
fresh disposable PostgreSQL 18.6 schemas. It is not a production migration,
release gate replacement, native runtime result, or deadline acceptance. The
required schema revision remains `032f_shared_validation_cost`; no capability,
runtime admission, process budget, or production validator changes.

## Separate candidates

- Key-set comparison changes one exact generic-schema predicate from sorted
  aggregates to arrays of canonical JSONB keys. The frozen production function,
  recursive fallback and exception boundary remain intact. The rejected
  JSONPath alternative is not used.
- The minimal row candidate installs six fresh validated CHECKs before six
  function replacements. The independently selected expanded candidate has
  sixteen CHECKs and ten replacements. They start separately from exact 032f,
  never stack as successive production migrations. Relational reads, identity,
  receipts, measurement matching and physical evidence obligations remain.

The key-set harness binds JSON parameters instead of exposing JSON colons to
SQLAlchemy text parsing, selects the installed 030 configuration/request/candidate
grammars and 031 legacy-bootstrap rule with positive weights at most one, and
applies the frozen 027a input-rule projection that omits portable authoring. Real
DTO inputs and differential assertions stay unchanged. PostgreSQL statement
timeout remains 10 seconds. Database collation metadata comes from `pg_catalog.pg_database.datcollate`, not a removed configuration
setting.

The row corpus uses the real workflow DTO serializers, including the three
required workflow-context defaults. A separate full-schema control requires
both legacy and typed workflow OE values to pass the installed contract before
and after the candidate; untyped and missing-context-field variants must fail.
The nonconstant benchmark retains all 64 fetches and checksum 4096 in each phase.
Its prior baseline rejection of half the rows was invalid workflow fixture input,
not evidence of a candidate speedup.

The queued-row probe submits both legacy v1 and provider v2 declarations through
the real API. Before installing the candidate it requires the provider queued
validator to reject v1 and accept v2, then requires identical outcomes after.
Provider settings are metadata-only synthetic declarations using the existing
fixture image pins and an unused loopback endpoint. No worker, SDK, model call or
image is launched; this proves neither provider execution nor native timing.

## Exact inventory and evidence

The committed `tests/structured/sql_candidate_inventory.json` records all named
cases: 69 offline contracts, 11 key-set PostgreSQL cases including the enabled
component benchmark, and 34 row cases consisting of one pristine catalog export
followed by the 32 independent minimal/expanded probes and an installed-contract
baseline control. CI checks both collection
and JUnit identities, rejects duplicates or missing cases, and permits no
failures, errors or skips. Once source contracts and owned PostgreSQL startup
succeed, the row/catalog step can run even if the key-set step fails, provided
the job is not cancelled. A failed key-set result still fails the job.

The minimal and expanded component benchmarks each export six fixed numeric
samples into separate JSON files in the dedicated proof directory. The exporter
rejects unknown fields, nonfinite values and arbitrary text, caps each report at
16 KiB, and never overwrites an existing report. Their uncaptured console
summaries remain available too. Component timings and write overhead are not summed
into native end-to-end performance claims.

The dedicated workflow uses only the existing hash-pinned backend test closure
and the official PostgreSQL 18.6 image. Each test schema is generated uniquely;
the loopback-only service is unique to the job/run attempt, resource-bounded,
and removed by exact identity in an always-run cleanup step. No Qlib, evaluator,
native model image or live model is started.

Artifacts contain the exact commit/tree, tracked source SHA-256s, allowlisted
named test outcomes, server version/limits, and a bounded predecessor metadata
snapshot. Raw JUnit, credentials and table contents are not uploaded. Only the
actual CI result for the recorded source can establish PostgreSQL execution;
local collection alone is not such evidence.

## Pristine predecessor metadata

The catalog case first migrates a fresh isolated schema to exact 032f and rejects
any row-candidate CHECKs before reading metadata. Export is opt-in through
`MINEAGENT_SQL_CANDIDATE_CATALOG_DIR=var/sql-candidate-proof`. Each raw/normalized
JSON file is limited to four MB, with at most 4096 entries per category, and has
a SHA-256 in the companion manifest. Outputs are exclusive-created.

The eight tables are research_runs, research_model_operations,
oe_structured_specs, oe_structured_control_states, oe_structured_candidates,
oe_structured_executions, oe_structured_engine_outcomes and oe_structured_epochs.
The snapshot records columns/types/nullability/collations, constraint definitions
and enforcement/validation/deferral, trigger definitions/bindings/enablement,
and all schema-local functions, operators and types that could affect name
resolution. Function signatures, arguments/defaults, support attributes,
configuration, ACLs and body hashes are retained, not function body text or data.

Normalization only replaces the generated schema identifier with `@SCHEMA@`
and removes documented numeric OID fields while retaining their named identity
counterparts. The raw file preserves those OIDs. Function-body hashes containing
the disposable schema are retained only in raw output; separately labeled
schema-normalized body hashes appear in both. Predicates, collations, ordering,
argument defaults, validation and enforcement flags are not normalized away.

This is an observed, reviewable pristine catalog, not a self-approved production
fingerprint. Catalog/builtin-shadow hardening and genuine native graph and
original-deadline proof remain prerequisites for any later adoption.

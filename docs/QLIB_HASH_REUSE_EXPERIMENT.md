# Qlib full032c hash-reuse experiment — NOT production acceptance

This isolated experimental branch temporarily repurposes the existing
`mineagent-configured-analysis.yml` workflow ID so GitHub can dispatch its branch
ref. It is **workflow_dispatch only**, explicitly experiment-named, and must not
replace the normal workflow on master. The unchanged normal workflow remains in
parent commit `f87fcb54`.

## What runs

The existing locked SDK, frontend/Chromium, numeric, controller and Qlib image
builds and dependency checks remain. On the same runner, control runs before
candidate. Each separate pytest invocation executes exactly the two original
workflow-policy cases, each with newly created disposable drill databases and a
real upgrade through `032c_oe_provider_table_checks`. Browser and backup/restore
assertions remain in those cases. The unrelated 24-case gate and final packaged
Compose gate are omitted, not counted as accepted. Native deadlines, budgets,
capabilities and fixture behavior are unchanged. Provider HTTP remains localhost
synthetic, not live-provider evidence.

`MINEAGENT_QLIB_HASH_EXPERIMENT` must explicitly select `control` or `candidate`.
The helper does nothing when unset and rejects other values. Candidate replacement
is confined to the exact source URL/database returned by the existing disposable
backup fixture, with its random test-database name and actual schema revision
checked before mutation. It verifies pinned SQL from 029 through 032c, the actual
installed original function body and catalog properties, and the installed
replacement body plus unchanged OID/owner/ACL and all inspected properties.
Candidate replaces exactly `mineagent_rd_workflow_submission_check(uuid)` and
`mineagent_rd_workflow_config(jsonb)`, in one guarded fixture transaction. The first transformation
reuses config/input hashes within a single invocation (four/two evaluations become
one each); the inverse transformation exactly reconstructs every original
predicate, full validation call and exception. No persistent cache, migration,
provider/runtime capability or predicate removal is introduced.

Candidate provenance says `full032c_experimental_hash_and_canonical_reuse`, never
unmodified/coherent production032c. The Alembic version alone is not claimed to
identify the altered schema.

## Evidence and interpretation

Each policy records pinned migration hashes, verified function-body hashes,
closed catalog verification, elapsed fixture time, and original worker PG
function counters. Counters use natural flush: missing/untracked/baseline-missing
is not zero cost; inclusive times must not be summed. Closed artifacts retain two
case outcomes/times, both variants, and a comparison receipt. Raw pytest logs,
XML failures, SQL payloads, IDs and exception text stay in `var/numeric-private`,
not uploaded artifacts. Run IDs are internal SQL bindings only.

Before each first/seeded worker, a successful direct function baseline and three
rollback-only rejection probes run against the actual populated rows. Missing
submission directly tests the function; mutated config/selection commitments can
be rejected by earlier immutable triggers. No trigger or constraint is disabled.
These are narrow rejection checks, **not whole-graph equivalence proof**. Reports
pair fixed policy/phase labels, explicitly list unpaired phases, and retain
`whole_graph_equivalence: not_established`. Prior 32 prepared-plan/mutation/
rollback projection checks support only their documented narrower scope.

A failed control does not skip the candidate. `experiment_complete`,
`control_passed`, and `candidate_passed` are separate. The overall experiment job
is deliberately red if either variant fails, even when candidate improves; a
successful candidate does not make normal acceptance green. Missing cases,
skips, missing provenance or missing required probes cannot pass. A terminal
experiment is not CI resolution or authorization to migrate production.

Control-then-candidate order is not randomized: timing improvements are not a
causal performance guarantee. This must establish actual full-fixture success
under original deadlines before any production migration proposal.

## Local checks

The focused `tests/diagnostics/test_qlib_hash_experiment.py` tests cover exact
transformation/pins, env/fixture rejection, catalog drift, reporting privacy,
case completeness, unpaired phases and explicit workflow structure. A local
PostgreSQL single-user schema-alias typed-table projection verified original and
candidate body bytes, exact catalog defaults, unchanged OID and all non-body
`pg_proc` fields, then rolled back. That is **not** an execution of the full032c
SQLAlchemy installer or populated worker; those require the hosted experiment.

## Second local-only reuse candidate

The combined candidate retains the first hash optimization and reuses one
workflow-input canonical string for its unchanged 32768-byte bound and SHA256
comparison. Native encode/sha256/convert_to calls are explicitly pg_catalog-qualified
to preserve the original metadata helper's namespace resolution. Earlier rejection
conditions remain before serialization. All other
predicates and the false-on-error wrapper remain; the exact inverse reconstructs
the original function. The original control is unchanged. Actual installed
canonical/metadata helper bodies, immutable/strict properties, search paths,
arguments and default depth=0 are verified before and after replacement; source
pins include018 and021, alongside029–032c. Both function hashes and helper/source
hashes identify this combined experiment, not the earlier hash-only candidate.

Local evidence:135 real-PG predicate comparisons (including the original112 and
null/Unicode/escaped/control/byte-boundary cases) matched. Five rotated samples
of15 calls on each of two captured real-writer typed-table projections compared
original, hash-only and combined. Combined median improvement over hash-only was
6.59% /2.71% in the final explicitly pg_catalog-qualified run; this is not full-trigger/worker/concurrency or deadline-success
evidence. Helper catalog/body projection checks rolled back. Full physical
fixture and original deadline checks still belong to the hosted comparison.

Artifacts additionally retain strictly allowlisted existing `RD_QUEUE_FAILURE`
and `RD_RPC_TIMING` records (closed enums, bounded integers/tails, no payloads,
identities or exception text) so an unsuccessful comparison preserves its exact
classified failure boundary. The earlier run omitted these records; its exact
failure method cannot be inferred merely from the approximately131-second cases.

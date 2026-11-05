# Isolated validation-concurrency prototype continuation

**Offline preparation only. The PostgreSQL harness has not been executed.**

This continues the isolated prototype published as
[c993367](https://github.com/idkwhodatis/MineAgent/commit/c9933671c5f27fadf13b24365e3f26c28197a78f).
It does not change production code, migrations, deadlines, charges or receipt
durability. The original component algorithm and synthetic SQL model remain
unchanged. The source-only runner now makes stronger, phase-specific observations
and reports its remaining limitations explicitly.

## Offline verification

Use Python 3.10 or newer; the offline tests require only the standard library:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s . -p 'test_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python -O -m unittest discover -s . -p 'test_*.py' -v
```

The current suite passes **90 tests**: 39 component-model, 17 source/adapter,
23 phase-helper and 11 lifecycle checks. See RESULTS.json for measured results and
hashes. Phase/error and lifecycle checks use explicit doubles, not real database
connections. The component tests retain 34,496 finite routing comparisons and
2,904 two-transaction parameter cases. Those cases are not unique interleavings,
and their outcome oracle shares model helpers. No timing is a product benchmark.

## Why the runner changed

SQLSTATE 55P03 alone could mistake lock timeout for intended fail-fast admission.
Expected rejection now also requires the exact known synthetic marker, compared
internally without exposing arbitrary server messages.

Statement-time and commit-time failures are tested separately. A failure at
COMMIT cannot satisfy an IMMEDIATE-boundary assertion. Commit probes require an
idle connection before entry and after the expected rejected commit, so releasing
a nested savepoint cannot count as a transaction commit.

The 18 planned database case groups include all INSERT/UPDATE/DELETE routes and
no-op statements; distinct backend identity; exact state after rejected writes;
missing-parent, missing-receipt and orphan-receipt cases; pending work before
IMMEDIATE followed by new invalid work; both savepoint lock-lifetime cases;
post-commit/full-rollback/failed-commit progress; and a READ COMMITTED statement
that sees a later committed graph. These are plans, not observed passes.

Failure reports distinguish SQL attempts from confirmed execution, identify a
sanitized stage/phase, preserve the primary failure if cleanup succeeds, and
verify namespace absence after owned cleanup. An unconfirmed schema-creation
outcome is not followed by blind deletion. The generated schema name is retained
only as a cleanup target; DSN, arbitrary SQL/error payloads and row values are not
printed.

## Runtime boundary and unresolved proof

Execution requires a separately permitted disposable local PostgreSQL environment
with two independent connections and psycopg. The prior Unix socket denial has
not been retried or bypassed. There is no server start, installation, automatic
fallback to TCP, or alternate execution environment in this continuation.

The existing local-only connection guard still requires explicit socket directory,
port, database and user, bounded connect timeout, and no ambient nonempty PG*
settings or DSN service/hostaddr override. Technical flags do not confer permission.

Even a future successful run would cover only the synthetic global-gate reference.
It would not establish actual RD/OE predicate coverage, scoped SQL coordination,
trustworthy whole-transaction retry state, production lock ordering or performance.
Gate retention **during deferred commit validation** still needs an explicit
bounded validation barrier. Cancellation of deferred queue entries after a
savepoint is not directly observed; the existing case is deliberately limited to
rows and gate rollback. See CONTINUATION.md and DESIGN.md.

# Offline review publication checkpoint, 2026-11-05

This publishes the reviewed hardening prepared against `b9e36f0`. Changes remain
inside `experiments/validation_concurrency/`; the component algorithm and SQL
model are unchanged. No production code, migration, deadline, charge or receipt
durability change is included.

## What changed

- The runner verifies its complete ordered 18-case manifest before reporting
  success. Missing, duplicated, unknown, malformed or reordered case records
  cannot pass merely because cleanup succeeded. This is a completeness guard,
  not an independent claim that SQL executed.
- Seven new lifecycle regressions exercise that boundary.
- Twenty new direct-invariant regressions independently cover missing parents,
  missing receipts, orphan receipts, legal repairs/deletes, old/intermediate
  dependency scope and validation/savepoint behavior.

All **117 offline tests pass** in normal Python and with `python -O`. The latest
prepublication logs are `offline-review-tests.log` and
`offline-review-tests-optimized.log`; the summary is `OFFLINE-REVIEW-RESULTS.json`.

The independent tests catch an omission that the prior shared oracle missed:
removing missing-parent detection still passed all 39 original component tests,
but now fails four independent tests. Removing the missing-receipt or orphan
predicate fails five or two new tests, respectively. These deliberate mutants
demonstrate test sensitivity, not defects present in the unchanged algorithm.

Independent finite checks found no mismatch across 1,568 model graphs, 34,496
single-change transitions and 10,000 seeded two-transaction batch schedules.
They are abstract model cases, not live database interleavings or performance
measurements. Logical component scope also does not prove computationally scoped
work: the abstract validator still scans the full graph for each selected node.

## Historical notes and current limits

`REVIEW-CHECKPOINT.md` is preserved byte for byte from the saved overnight review
and describes its then-local, unpublished state. This note records publication.
Earlier README, RESULTS.json and PUBLICATION.md checkpoints retain the previous
90-test preparation/publication evidence, rather than replacing those records.

PostgreSQL has not started and **none of the 18 live database case groups ran**.
The existing private Unix-socket denial was not retried or bypassed during this
review or publication. A permitted disposable PostgreSQL 18 runtime with two
independent local sessions is still required. The current cases also need a
bounded, observable barrier to prove gate retention while deferred commit
validation is actually executing.

Actual RD/OE predicate coverage, complete writer participation, scoped SQL and a
trusted transaction-fatal retry latch remain separate gates. No production or
performance acceptance is claimed. Malformed model API inputs still have an
unresolved client-preflight versus server-error semantic distinction; this patch
does not adopt new transaction-error semantics.

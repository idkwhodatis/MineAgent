# Affected-component validation and coordination

Status: reconstructed isolated design, 2026-11-03. Actual PostgreSQL integration
and concurrency remain unproved. Source baseline: MineAgent
`83895f4efe4fc9062e59a2af571b36348ed51248`.

The accepted direction permits validation of affected dependency components
instead of opportunistically finding unrelated pre-existing corruption, and
fail-fast contention requiring retry from the transaction boundary. It does not
permit missing checks inside an affected component, stale authority, changed
charges/deadlines or changed physical receipt durability.

## Two separate proof layers

`model.sql` is a synthetic global writer gate plus always-full graph validation.
Its runner remains unexecuted. It is a correctness reference, not the proposed
production solution or a scoped validator.

`component_model.py` is an offline abstract specification of scoped coordination.
Its execution can falsify the algorithm under its assumptions, but cannot prove
PostgreSQL trigger order, lock acquisition, MVCC freshness, writer permissions,
rollback semantics, actual RD predicates or performance.

## Scope selection and locking

The model has parent resources P, workflow resources W and one required receipt
for each W. A W references P; every W sharing P belongs to the same connected
component, even across owners. Owner is only a field in this tiny model, not a
modeled authorization predicate.

1. For each proposed batch, retain typed identities from OLD, every intermediate
   state and NEW. An absent parent still contributes its P key; an orphan receipt
   still contributes its W key. Deletion must not erase old dependency identity.
2. Compute connected closure over those edges. Parent changes start from P,
   workflow changes from W and both parent endpoints, and receipt changes from W.
3. Acquire every selected key in stable typed order before mutation. A conflict
   rejects the transaction. Atomic all-or-none acquisition is an explicit model
   assumption; actual SQL must account for partial acquisition and release.
4. Recompute the closure using a fresh view after admission. New reverse edges
   that expand the required set cause a whole-transaction restart. Do not append
   new locks after protected writes.
5. Every later write must route entirely within held keys. Hold coordination until
   validation and commit. Reverse-reference insertion must acquire the referenced
   endpoint key, including a parent row not yet present.

Thus a parent mutation and a new dependent workflow both require P. A reverse
edge committed before acquisition is found by rechecking scope; one attempted
after P is held conflicts. Locking only existing W rows does not cover this
phantom. The finite tests exercise both cases, not all possible graphs or SQL.

## Generations, fixed points and savepoints

Each successful statement increments a transaction-local generation and marks
selected W nodes dirty. A successful check can be reused only for the exact
generation, while coordination remains held. Later writes, including writes
caused during deferred validation, invalidate earlier success.

The dispatcher visits pending W IDs in order and repeats until all generations
are checked. A bounded number of visits to each W rejects excessive rewrites; it
does not mark them valid or prove mathematical nontermination. The default 32
visits is a small model parameter, not a production setting. A valid component
with 100 distinct W nodes is covered and does not consume a single shared limit.

Immediate mode checks at the modeled statement boundary. Switching to immediate
first checks pending work; switching back to deferred does not cache later changes.
The synthetic error order is affected W ID, then its membership predicates. It is
not historical global first-error equivalence.

Savepoints restore writes, dirty generations, validated generations, constraint
mode and modeled post-savepoint locks. Earlier pending work survives. Ordinary
predicate failures can be recovered by rolling back a savepoint.

Scope expansion, writer conflict and incompatible preheld locks instead set a
fatal restart latch outside savepoint snapshots. A new transaction is required.
**A trustworthy PostgreSQL implementation of that latch, surviving savepoints
without caller spoofing, remains an open gate.** A session GUC is not such proof.
The preheld-lock Boolean is also an assumption, not database lock inspection.

## Actual RD dependency obligations

Prior pinned-source inspection established these routing limits. They are
preserved design inputs, not a complete catalogue reconstructed here:

- The 16 workflow deferred-trigger attachment tables are not complete closure.
  Analysis-plan checking also reads `dataset_versions`; workflow append admission
  separately reads `execution_attempts` and must not use a deferred-result cache.
- `mineagent_rd_workflow_submission_check` is frozen in migration 029. Migration
  031 calls it and changes OE reverse behavior; it is not the defining revision.
- The parent binding lives in `research_runs.config`. Factor-version, object,
  stage predecessor and feedback-terminal checkpoint references are independent
  typed edges, including JSON references. Null, missing endpoint, malformed UUID
  and wrong-owner endpoint are different conditions.
- OLD and NEW direct IDs, run/workflow identities, parent endpoints, reverse
  dependents, absence predicates and ordinal/insert ranges must all be routed.
  Missing intermediates must fail closed, not silently yield an empty set.
- Existing immutable guards reject workflow-child stage/revision/checkpoint
  UPDATE/DELETE and receipt UPDATE/DELETE. Abstract deletion tests falsify general
  routing; they do not demonstrate those mutations are reachable in production.
  Test reachability must be checked against each table's real guards and FKs.

Relevant pinned sources:

- [RD workflow predicates](https://github.com/idkwhodatis/MineAgent/blob/83895f4efe4fc9062e59a2af571b36348ed51248/backend/app/rdagent_workflow_sql.py)
- [OE reverse predicates](https://github.com/idkwhodatis/MineAgent/blob/83895f4efe4fc9062e59a2af571b36348ed51248/backend/app/oe_workflow_reverse_sql.py)
- [Workflow locking and immutable guards](https://github.com/idkwhodatis/MineAgent/blob/83895f4efe4fc9062e59a2af571b36348ed51248/backend/app/research_workflow_sql.py)

## Retained OE locks constrain concurrency

Frozen 031 OE reverse checking on shared tables, including RD budget controls and
workflow receipts, enumerates same-owner structured workflows. The workflow lock
helper takes `submit:<owner>` using try-advisory locking and workflow row locks
with NOWAIT. Disjoint RD components of one owner can still conflict while these
OE checks remain unchanged. The abstract disjoint-commit test cannot establish
production same-owner independence or throughput.

A transaction may already hold workflow/run/resource locks before admission.
Sorting newly requested component keys does not fix that earlier ordering. Real
writer entry points must account for owner submission, workflow/run/attempt,
dataset and quota/publication locks across both families.

## Gates before production work

1. Enumerate actual trigger/FK catalogue, direct SQL, cascades, bulk writes,
   inheritance/partition paths and maintenance. Statement timing alone cannot
   supply arbitrary OLD/NEW row identities or complete writer coverage.
2. Establish database-enforced admission, incompatible-preheld-lock handling and
   whole-transaction retry. Any privilege change requires separate review. No
   new role or SECURITY DEFINER interface is installed by this prototype.
3. Implement total typed routing for every real relational/JSON dependency, with
   legal-write reachability and missing-reference failure behavior verified.
4. Map dirty state and dispatcher behavior to real transactional storage and
   permissions. Prove immediate boundaries, savepoints and later-trigger writes.
5. In a separately permitted environment, execute bounded independent-session
   schedules with explicit barriers and positive transaction/cleanup evidence.
   Start with the global reference, then scopes, then actual pinned predicates.
6. Compare original and candidate on paired populated runs. Keep all existing
   deadlines, conservative charges, fresh lease/fence/peer checks, physical reads,
   receipt commits, replay and uncertainty handling unchanged.

No production activation, migration, receipt fusion or performance threshold is
implemented here. Unrelated-corruption detection is intentionally outside a
selected component; corruption connected through a shared parent remains fatal.

## PostgreSQL reference constraints

- [PostgreSQL 18 locking](https://www.postgresql.org/docs/18/explicit-locking.html)
  distinguishes advisory lock lifetimes and rollback behavior.
- [PostgreSQL 18 trigger behavior](https://www.postgresql.org/docs/18/trigger-definition.html)
  distinguishes row/statement execution and inheritance/partition behavior.

These references constrain a future implementation, not validation of this one.

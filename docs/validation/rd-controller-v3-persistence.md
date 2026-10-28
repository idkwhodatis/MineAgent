# RD controller v3 persistence boundary

Revision `027_rd_controller_v3` follows published026 without changing any
001–026 migration bytes. This is an opt-in **fixed controller** profile, not a
caller-supplied timeout and not a different child/model budget.

| Immutable configuration | Admission | Controller wall floor / maximum intent | Composed run floor |
| --- | --- | --- | --- |
| `controller_profile` absent | existing v2 | 60000 ms /60 s | existing240000 ms |
| `controller_profile: mineagent.rdagent-orchestrator.v3` | v3 | 120000 ms /120 s | 300000 ms |

Provider run floors additionally include the existing `32 * request_deadline_ms`.
New selection requires exactly one iteration. Explicit null, unknown profiles,
new configuration with an old admission, and new admission without explicit
selection are rejected. A larger old budget never upgrades its profile.
Legacy v1 historical admission handling is unchanged; it is not eligible for the
new selection. Child/call/token/cost/memory/kernel/stop/settlement guards remain.

The new config wrappers validate the exact frozen026 schema after removing only
the explicitly checked new field. They retain the complete1 MiB config limit.
Server profile hashes add the selection only when explicit; the absent-field
JSON object and canonical history remain unchanged. Source hash manifests record
both historical and exact reviewed successor source identities, rather than
pretending changed runtime sources still have their old hash.

## Migration and rollback

Upgrade uses the same advisory maintenance boundary and exclusive table locks.
The controller profile/config constraint is deferred, like the existing graph
constraints. Downgrade first refuses retained v3 admissions **or** RD runs with
an explicit selection. It never deletes these rows. With no new data it restores
every replaced026 function and the exact025 process-admission gate.

## Evidence and limits

The local PostgreSQL18 isolated single-user fixture executed fresh27 upgrades
and14 downgrades with no SQL errors. A separate existing026 database exercised
027 upgrade,39 config/profile predicate cases, and empty027→026 rollback.
The first26 emitted migration SQL statement hashes remain unchanged.

A same-schema TEMP relational fixture exercised the **actual production**
controller constraint function: complete old60 and new120 metadata accepted;
59999/119999 ms controller reservations rejected;121-second intent rejected;
retained-v3 downgrade guard rejected. All changed receipt commitments were
recomputed, while kernel/child/positive-stop evidence fields and full-charge
checks remained active. This isolated-function test does **not** claim all
production FK/trigger combinations, real kernel observations, containers, or a
full cold restore. Those remain separate integrated hosted acceptance gates.

`tests/integration/test_rd_controller_v3_predicates.py` provides migrated-DB
regressions for the new helpers; ordinary collection is not execution evidence.
The final combined runtime/backup tree must run all affected tests, including
fresh wheel proofs, actual controller execution, and cold restoration.

## Closure lineage and later unpublished work

The paired backup work assigns research closure **v11** and RD source closure
**v6** to027. Old research<=v10/RDsource<=v5 must reject the new field/profile;
new11/6 retains missing-field old60 semantics. There is no silent reinterpretation
of an older backup.

Previously prepared but **unpublished** structured/workflow work must be
rebased separately: migrations027/028 become028/029, and their research closure
v11/v12 becomev12/v13. Their RDsource inheritance must explicitly adoptv6.
The earlier225 sealed candidate is not edited by this stage. New version numbers
must not retain two different meanings across these branches.

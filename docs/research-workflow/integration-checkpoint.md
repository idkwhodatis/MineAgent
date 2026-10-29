# Three-source workflow assembly checkpoint (not activated)

The workflow successor is tentatively **028**, after normal OE 027. The installer
and shared schema tuple are not yet defined. No old 026/025 literal is regenerated.
This branch gathers the real components now; final assembly must start from the
actual coherent 027 base, carrying only these owned changes, not its older base.

## Owned component order

- Source contracts: `e2a250b`, `2a54dbf`.
- Stage foundation / finite plan / deferred child FK: `d3d91fd`, `7aa5aa7`, `94804f1`.
- Source storage, resolution and receipts: `75a3bef`, `18b6851`.
- Retained stage history: `ce2e04d`.
- Workflow create/future-selection CAS: `d0b9102`.
- Real raw intake and normalization runner / source DDL: `a2014f4`, `bab0949`.
- Default-off scoped release choice and current owner view: `2a0fde3`.
- Source HTTP/UI wrappers: `9a53b9e`, `ea086687`.
- Legacy manual text-byte compatibility: `984425e`.

These components are currently composed on `native-workflow-stages-draft`.
The 026 cold corrective `f561495` is an inherited compatibility prerequisite,
not a workflow feature. Its independent terminal SQL successor `c526af7` belongs
on coherent 026 and must not be replaced by the older base of this branch.

## Required before registration

1. Actual coherent 026/027 models, runtime profile and closure tuple.
2. Server-authoritative workflow preflight: derive owner/template/operator/runtime
   pins from normal RD preflight; never expose internal `CreateWorkflow` fields as
   a client-chosen launch declaration.
3. New workflow-bound RD child config/bootstrap/request and atomic new child
   submission. Old same-owner runs cannot be attached as stage outcomes.
4. Actual source-stage→child→analysis linkage and two-phase existing-ledger release
   adapter. Initial opt-in is immutable, default off, limited to this workflow's
   eligible development outputs; no global/retroactive/final-data grant.
5. Workflow mutation/CAS DDL, input namespace quota inclusion, cold graph/bytes
   closure, and installed PostgreSQL race/restore tests.
6. Durable safe checkpoint predicate. A normalization terminal receipt proves its
   recorded outcome, not a separately retained budget settlement or process stop.
   Existing runner returns only after quota guard exit and commit, but that return
   value alone must not be serialized as a trust marker or resume grant.
7. One normal user flow: raw intake → actual normalization → immutable selection →
   real new RD child → qualified feedback → next newly admitted stage. Existing
   physical deadlines remain unchanged. UI/source modules are not registered yet.

## Evidence boundaries

43 workflow unit tests passed on the policy/read checkpoint; source owner reports
89 source tests plus nine manual compatibility tests; UI owner reports 16 HTTP
and eight frontend tests plus bounded types. These are component results, not a
new combined full suite or actual PostgreSQL/Docker/browser acceptance.

## Local boundary and explicit content successor

The preserved branch now includes the source terminal verifier `f92647d`,
registered-object orphan rejection `eba1a15`, terminal IDs/content reader
`d9cb6c9`, and quota-context sole-commit correction `04c3979`. The local
checkpoint implementation records exact definition, frozen selection, actual
terminal-event and retained-object commitments. It does not fabricate a process
stop, refund, model-child submission, or release permission. Continuous success
remains blocked on the missing actual child adapter.

Normalization's incremental allocation is 262144 bytes of aggregate canonical
outputs plus one 32768-byte atomic temporary file (294912 bytes). The already
retained raw object, capped at 65536 bytes, belongs to the physical baseline and
its separate intake admission; it is not charged as a newly generated output.
This is a conservative bound, not a measured peak or a duplicate cost ledger.

The 19 local checkpoint/history tests passed after the commit-boundary fixes,
including exactly one guard-owned successful commit before acknowledgement and
no acknowledgement when the final quota check fails (`4acafbf`). The later
explicit owner content UI/API cut `4c181d6` is composed without main registration;
its owner reports 21 HTTP tests, 15 frontend tests and scoped strict types passing.
These results are separate component runs, not combined hosted acceptance.

Normal OE027's actual candidate-to-protected-engine bridge is maintained on the
separate `native-oe-structured-engine-027` branch (`127f502`), with the numeric
owner responsible for its authoritative candidate resolver and registration.
It is not a workflow028 child adapter and does not activate this branch.

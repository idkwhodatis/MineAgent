# Seeded provider controller v4

Revision `027c_seeded_controller_v4` adds an explicit **seeded-only** controller
profile. It does not reinterpret previously retained profiles or change ordinary
fresh research runs.

| Selection | Controller wall | Parent + three child wall floor |
| --- | --- | --- |
| Historical omitted selection | 60 seconds | 240000 ms |
| Composed v3 | 120 seconds | 300000 ms |
| Provider seeded v4 | 180 seconds | 360000 ms |

Configured provider reservations additionally include the existing model-request
wall allocation. Relative to v3, v4 reserves and conservatively settles **60000 ms
more wall time**, not more model calls, tokens, cost, memory, process slots, child
unit time, or stop grace. Each child still has its original 60-second bound and
strict stop verification remains unchanged.

The server provider runtime configuration must explicitly retain
`controller_profile: mineagent.rdagent-orchestrator.v3` and opt in with
`seeded_controller_profile: mineagent.rdagent-orchestrator.v4`. Fresh requests
project out the seeded field, preserving the original canonical runtime hash.
Seeded requests retain both fields in their server-profile binding and freeze v4
in the run configuration. Null/unknown selections and v4 without a retained,
owner-bound seeded parent are rejected. This is not permission to execute a
proposal, release an evaluation, or enable a live provider.

Cold closure research v12 / source v7 accepts the new profile only with its full
seed/parent and controller evidence. Older closure versions keep their prior
accepted domain. Downgrading 027c is rejected while v4 runs or admissions remain;
otherwise the historical SQL functions are restored exactly.

## Verification boundary

Local tests cover canonical bindings, fresh/seed separation, reservation and
settlement, retained-parent and control-file closure, and malformed/foreign/old
schema rejection. PostgreSQL checks cover migration upgrade/downgrade, profile
and wall-floor predicates, and the production seed-binding predicate on isolated
rows. Those isolated rows are **not** a complete nonempty research workflow.
The configured-provider hosted gate must still prove the actual seeded container
flow on the final published head. Its fixture provider has declared zero price;
this change does not enable paid or live provider calls.

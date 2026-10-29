# Observed numeric rejection (027 successor)

This successor does not change sealed 026 success semantics. Activation belongs to
`028_oe_structured_search`, assembled with the normal structured OE successor.
The public RD request/config remains unchanged; the configured controller has a
new pinned wheel and a distinct `mineagent.rdagent-numeric-rejection-frame.v1`
result message. Legacy fixture/success result decoders remain exact.

## Failure is an observation, not a root-cause diagnosis

Only the candidate's retained MANU2 kind-3 payload, complete EOF, exact original
launch/kernel identity, timely whole-unit stop and full conservative settlement
can produce a failure frame. Missing output, arbitrary exceptions, PID absence,
missing receipts or an unsettled hold cannot. `child_nonzero` means the protected
candidate child exited nonzero; dependency or environment faults remain possible.
`output_rejected` means no accepted output was produced despite child exit zero.
Neither outcome has a snapshot, IC, objective measurement or adoption receipt.

Actual upstream CoSTEER receives `(observed feedback, None)`, performs real critique
and final-decision model calls, and can raise its actual `CoderError`. The host
retains the exact model observations, including an erroneous positive vote, but
cannot promote rejected execution into a valid result or generation boundary.
The failure feedback binds source/input, evaluation/cutoff, protected controller,
actual closed final-decision call and both stop/rejection receipt identities.
Critique input must contain the exact observed feedback; its retained response
must equal the saved code critique. The failure result and membership commit
atomically. Failed feedback remains data, never authorization to execute again.

## Installation / restore ordering

1. Install the explicit generic rejection receipt capability, tables and predicates.
2. Register `FactorNumericFailureFeedback` and freeze its table literal in 027.
3. Install `rdagent_numeric_failure_sql.render_upgrade_sql()` with `@SCHEMA@`
   substituted, using raw-driver/no-parameters execution. This changes only the
   old closed evaluation-outcome insertion gate, admitting `execution_failed`
   with an exact new membership. Old valid/adoption/checkpoint gates stay closed.
4. Research v11/RD source v6 explicitly inventory the new membership. Earlier
   versions still reject these rows and the new typed feedback body.
5. Restore verifies all physical journals and provider objects/accounting first.
   Preserve the generic validator's internal admission-ID →
   `(NumericLaunch, NumericIdentity, NumericStop, NumericRejection)` map, then call
   `validate_failure_links(rd, research, native, execution, rejection_units,
   read_bytes, max_rows=...)`. Do not serialize that map as an authority flag.
6. Every registered failed-feedback body uses the strict new codec, including
   pending publications. SQL checks relational identity, not actual bytes or
   kernel facts. Downgrade rejects retained failure evidence.

## Evidence boundary

Local tests cover actual journal/body bytes with DB/session doubles, source/input
and actor rechecks, no-capability/no-stop/no-charge rejection, canonical object
identity, no-snapshot cold links and SQL construction. An actual installed CoSTEER
probe uses the production configured result decoder and real pinned SDK against
localhost: generation → critique → final decision → `CoderError`, three SDK calls,
no embeddings. Its candidate rejection frame is explicitly doubled, not execution
proof. Deterministic wheel construction is not image acceptance.

Required actual case:
`test_normal_provider_actual_rejection_preserves_failed_feedback_without_snapshot`
in `tests/integration/test_rdagent_provider_rejection.py`. It needs PostgreSQL,
027, the new controller image, real candidate runtime and explicit SDK Python;
`MINEAGENT_RD_REJECTION_FLOW_REQUIRED=1` forbids a skip. The case uses normal
owner-authenticated preflight/submit → outbox → normal worker, actual generated
failing source, two stopped Docker roles and three SDK calls. Local collection is
not a pass. It must be added to the 027 workflow's exact required inventory.

# Normal structured search on revision 028

Revision 028 explicitly inherits the existing normal structured-search path:
submission, queue, controller/SDK/native execution guards, immutable objects,
conservative quotas, cancellation, recovery and owner historical result reads.
Revision 027 remains accepted by the same fixed guards; unknown revisions do not.
The scoped historical reader uses the installed research closure pair (027/v11,
028/v12), never relabels an old whole-install backup, and retains the mandatory
normal physical graph validation.

## Source limitation

Normal OE accepts only its previously qualified factor-research v1/v2 sources,
with the existing explicit snapshot release and fresh retained-byte checks.
Workflow factor-research v3 sources are **not supported as normal OE inputs**.
Parsing a v3 config or finding a public snapshot release does not prove its actual
workflow child, combined inputs, consumption and parent closure. Preflight,
submission and historical result source loading reject that family before reading
snapshot values. Adding that complete qualification path is separate work; this
compatibility change does not claim it.

Local tests use explicit database doubles and retained files where applicable.
They do not constitute a hosted PostgreSQL/Docker/browser acceptance run.

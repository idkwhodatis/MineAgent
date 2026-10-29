# Explicit synthetic demo onboarding

The RD preflight page uses the registered authenticated
`POST /engine-runs/demo-dataset` API. A separate, initially unchecked synthetic
confirmation is required. The response is a normal owned DatasetVersion and a
server-returned preset, not a research run or a model grant.

A second explicit **Use returned demo preset** action replaces the dataset,
window, task and objective fields. It invalidates any previous preflight and
remounts the declaration form, leaving the source-policy acknowledgement
unchecked. It does not select a model, alter a budget or steer selection, submit
research, or release results. Pending/accepted research submissions lock both
creation and application; their existing exact-key retry behavior is unchanged.

The returned dataset remains selectable even when not on the current paginated
list. Eligibility is still unchecked until the normal retained-data preflight.
Reload/back never automatically creates another dataset or research run; repeat
creation uses the backend's same-owner content identity. Demo receipt/preset
state is not persisted as user approval. Authentication changes discard an
in-flight response. Root and SUBPATH requests use the existing deployment helper.

This is synthetic plumbing data, not live-market, PIT or performance evidence.
The returned preset is the maintained close-return demonstration, not a claim
that arbitrary hypotheses have been generated or validated.

Local validation with normal027 UI composition: actual main OpenAPI/client,
41 focused UI/transport tests, all 1191 frontend tests, full TypeScript and Vite
build passed.
These tests are not a real browser or PostgreSQL run. Actual demo creation →
normal preflight → explicit submission → released result → normal OE search
still requires coherent 027 hosted browser acceptance; no such pass is claimed.

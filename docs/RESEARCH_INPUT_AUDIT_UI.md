# Input derivation audit (unregistered successor)

The owner-authenticated `research_input_routes` module wraps the real
`read_derivation_history` service. It is deliberately not registered in `main`:
input/workflow DDL, central quota, cold closure and actual stage adapters must be
composed first. The service itself rejects an absent exact input capability.

The public projection exposes only actual receipt IDs, hashes, timestamps, bounded
output references and a finite public error category. It does not automatically
return raw external bytes or normalized text. Missing/foreign receipts remain 404;
contradictory scope or malformed projections are withheld, not partially returned.
`started_no_terminal_receipt` is not called running or successful. No ready input
is fabricated for a failed or unfinished derivation.

External normalization is format/bounds checking, not content verification,
instruction authority or human approval. Internal feedback keeps its own origin
and remains closed until the real source-stage/release reader is available. Manual
steer versions remain separate pinned inputs; an automatically created workflow
child must not inherit an authenticated-user origin just because it has an owner
or uses a familiar fresh-request schema.

The audit component displays actual lineage and refresh-stable metadata only.
It does not select outputs, modify an active stage, submit a model request or grant
result release. Workflow history/control components will consume the native
owner's authoritative read projection; they must distinguish prepared selection,
uncertain transport, confirmed provider response, stage outcome and physical stop.

Component-only types are generated from the single public Pydantic DTO with
`python -m script.export_research_input_contracts`, then the existing frontend
OpenAPI generator using `-f openapi-input-audit.config.ts`. The intermediate schema
has no paths and claims no API activation. Use the main SDK at coherent registration.
Local HTTP transaction doubles and rendered component tests are not PG, browser,
worker or cold-restore acceptance.

The normal-entry raw-intake step now has an unregistered authenticated HTTP
wrapper over the factory-owned publisher. Streaming stops at 64 KiB before the
factory is called; empty or malformed JSON is retained unchanged for the actual
normalization stage to reject. The factory exits quota checks and commits before
returning id/hash/size. It does not create a workflow. The UI defaults disabled,
submits exact UTF-8 bytes, verifies the returned SHA/size, preserves SUBPATH and
never automatically stores raw text in browser storage. The public workflow form
must use the forthcoming server-derived plan adapter, not the internal owner/image/
Qlib-pin-bearing CreateWorkflow ABI.

## Explicit retained input/output content

The unregistered input router now also exposes owner-authenticated bounded
external-object and normalized-version `/content?expected_sha256=...` reads.
They call the source owner's factory reader, which checks actual retained bytes,
owner, immutable version and source reconstruction. Feedback content remains
closed: owner possession or a reference is not a release grant.

Audit metadata includes the actual raw object and terminal event identifiers.
Lists still do not automatically fetch raw or normalized text. Explicit View
verifies a bounded response and its exact hash before escaped text rendering;
non-UTF8 input is download-only. Downloads are fixed-name attachments with
no-store/nosniff. No raw text enters browser storage, automatic logs or artifacts.
Close, identity navigation, and authentication changes discard the in-memory
view. None of these actions selects an input, approves a proposal or changes a
running stage. Main/schema/capability registration and real browser acceptance
remain pending coherent workflow integration.

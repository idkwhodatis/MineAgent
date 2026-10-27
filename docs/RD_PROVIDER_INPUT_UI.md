# Explicit configured-model RD input UI (026 integration slice)

The existing research preflight page now distinguishes the maintained fixed
close-return fixture from an explicitly selected server-configured model.
Configured-model creation uses both `configured_model_can_create` and the exact
catalog option's `can_create`, never the old fixture `can_create` flag. No model
endpoint, secret or runtime image is accepted from the form. Prices remain
operator declarations and billing status remains unknown, not inferred zero.

User-selected immutable steer versions retain their explicit order. Changed or
disabled current choices must be removed/reselected for a new preflight, never
silently upgraded. Preflight shows the exact owner-bound version/text/hash
snapshot, model commitment and optional retained parent commitment. This records
input, not model compliance or human approval of a generated proposal.

Factor Workbench Evolution links the exact version to the same preflight page.
The reference is validated and selects configured-model mode, but neither selects
a model nor submits a request. Invalid references fail closed instead of becoming
fresh unseeded requests. Backend owner/retention/prompt checks remain authoritative.

Pending submission envelopes keep their original protocol, key, request/preflight
hashes, ordered version IDs and model/parent commitments across reload. Exact
receipts must match all applicable commitments and the response family. Provider
failures clear a pending envelope only for the documented post-key-lookup
`preflight_changed`, `engine_request_ineligible` and `runtime_profile_changed`
rejections; unknown/model/schema/auth/network failures retain the original retry.
Editing or disabling a steer after acceptance never changes that run snapshot.

The existing genuine create-browser case supports explicit provider selection
and ordered steer IDs as well as the original fixture. It checks real preflight,
receipt hashes, pinned authenticated input history and reload, without route
mocks or extra runtime units. **Provider-mode browser execution is pending the
coherent 026 backend/SQL/worker gate**, including the version-dispatched receipt
validator and owner history projection. Collected browser cases and unit tests
are not hosted execution acceptance. This narrow contract/UI worktree does not
contain all provider writer/runtime dependencies and is not a standalone release.
Regenerate the client/OpenAPI after final backend composition.

Subsequent three-source/between-stage steering is a separate integration:
normalized external proposals, actual experiment-feedback proposals and pinned
manual input must bind when the next stage atomically starts. It will not rewrite
this accepted 026 config or a running/completed stage. This slice does not claim
that future stage/CAS behavior is already implemented. No TrendRadar fetching or
external feed adapter is included.

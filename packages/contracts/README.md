# API contracts

`openapi.json` is generated from `backend.app.main.create_app` plus the typed
SSE `EventPublic` envelope. Generate from the repository root:

```sh
.venv-api/bin/python -m backend.app.export_openapi
```

No running database, server or runtime credentials are required for generation.
Frontend client/types should be generated with the locked @hey-api toolchain.
Do not hand-edit this schema. Public request/response source lives in
`backend/app/schemas.py`; SSE remains an authenticated fetch stream.

## Self-hosted and hosted implementations

The open backend remains a complete self-hostable implementation. A future
separate hosted-server repository must conform to this public HTTP contract;
it is not a build/runtime dependency of the open frontend or backend. The
frontend SDK generator consumes the committed `openapi.json`, not Python source.
Use the same-origin API proxy for both deployment modes, including `SUBPATH`.
Visiting a hosted installation and signing in there requires no cross-origin
server selector and must not send credentials to another deployment.

`GET /api/v1/server-info` is a public, database-independent discovery endpoint:

```json
{
  "protocol_version": "1",
  "deployment_mode": "self_hosted",
  "display_name": "MineAgent",
  "authentication_methods": ["password_bearer"],
  "capabilities": ["authenticated_sse", "idempotent_run_submission"]
}
```

All five fields are required. Missing protocol/authentication/capability metadata
must not be silently populated by a client. Clients should tolerate additive
fields and unknown capability values, but an unknown protocol version must not
be interpreted as version 1.

Operators may pass `DEPLOYMENT_MODE=self_hosted|hosted` and `DEPLOYMENT_NAME`
into the API process environment. Defaults preserve self-hosted operation.
These are display metadata, not switches for authentication, authorization,
quotas, registration, billing or feature access. Capabilities describe transport
support, not enabled engines or permission to execute a particular operation.
Do not serialize provider configuration, secret keys, storage paths, database
URLs or internal readiness diagnostics into this endpoint.

Older servers may return 404 for discovery; existing same-origin password login
remains the compatibility path. Never redirect credentials based on discovery
metadata. Readiness remains `/health/ready`; discovery success does not mean the
database/workers are ready. This foundation adds no signup, password reset, SSO,
enterprise policy, or production-hosting security claim.

Run the public-contract and existing authorization regression checks with:

```sh
.venv-api/bin/python -m pytest backend/tests/test_server_info.py backend/tests/test_api_boundaries.py
```

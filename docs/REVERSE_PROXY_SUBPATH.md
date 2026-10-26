# Reverse proxy at a subpath

Set the same runtime `SUBPATH` on the API and frontend (the full Compose file
passes `${SUBPATH:-}` to both). Empty or `/` preserves root deployment. For
example, `/research/tools` mounts the application without rebuilding either
image. Use an ASCII path composed of letters, digits, `_`, `-` and `.` within
segments; dot-only segments, trailing slash, repeated slash, encoding, query,
fragment and prefixes longer than 200 characters are rejected at startup.

## Strip-prefix contract

The trusted outer reverse proxy must redirect the bare mount to a trailing
slash and **strip the configured prefix** before forwarding every request to
the frontend service. The frontend then routes `/api/` internally to the API.
For example, an illustrative Caddy handler inside an existing site block is:

```caddyfile
redir /research/tools /research/tools/ 308
handle_path /research/tools/* {
    reverse_proxy frontend:8080
}
```

Use your existing proxy network/address rather than exposing additional ports.
This example does not provision DNS, TLS, firewall or a deployment. At root,
proxy directly without stripping a mount. The frontend's `/healthz` and API's
`/api/v1/health/ready` container checks remain internal unprefixed paths. Do not put
SUBPATH into internal upstream addresses.

The API reconstructs ASGI's public `path` and `root_path` from this explicit
configuration, including redirect and OpenAPI URLs. Same-origin redirects under a non-root
mount use relative Location paths, retaining the browser’s outer scheme and
port without trusting forwarded protocol headers. Do **not** also set Uvicorn
`--root-path`, mount the ASGI app under another root, or forward unstripped
paths. `X-Forwarded-Prefix` is not consulted. Existing forwarded host/protocol
trust is unchanged; prefix configuration does not authorize arbitrary forwarded
headers. API bearer authentication is unchanged.

The same frontend image renders its HTML base and prefix metadata into `/tmp`
at startup, leaving immutable assets and the read-only root filesystem intact.
The router, assets, API requests, authenticated SSE and downloads use this runtime
prefix. Deep links must fall back to the frontend index, as the bundled nginx
configuration already does. Restart both services together when changing it.

There are no authentication cookies, CSRF callbacks, OAuth redirect callbacks or
WebSocket endpoints to rewrite: login uses a password bearer-token endpoint and
live events use authenticated fetch/SSE. Tokens never appear in stream/download
URLs. Session storage keys are namespaced for non-root mounts to prevent
accidental cross-app key reuse; this is **not** a security boundary between apps
on the same origin. Root keeps its existing session key. Changing the prefix
requires signing in again. Use distinct origins when origin isolation is needed.

See [the acceptance gate](SUBPATH_ACCEPTANCE.md) for real Caddy, PostgreSQL and
Chromium coverage at root and a nested prefix with identical images. Local unit
checks alone are not a claim that hosted proxy acceptance passed.

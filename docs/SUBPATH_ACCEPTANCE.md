# Real reverse-proxy acceptance

`.github/workflows/mineagent-subpath.yml` runs a disposable, loopback-only HTTP
Caddy stack on hosted CI. It is not a deployment recipe and does not change DNS,
TLS, host firewall settings, trusted forwarded headers, or provider accounts.

The harness builds the API and frontend images **once** and uses the same image
IDs first at `/`, then at `/lab/research-7`. Only runtime `SUBPATH` changes.
A disposable derivative pinned to the pulled official Caddy repository digest removes its executable’s
unused `cap_net_bind_service` file capability, avoiding file-capability exec
failures with `cap_drop ALL`. No runtime capabilities are added; the proxy
listens on 8080 with no-new-privileges and a read-only root filesystem. Both
upstream and derivative image IDs and the pinned upstream repository digest are
recorded; the derivative build does not re-resolve a mutable tag.

Caddy 2.11.7 Alpine strips the nested prefix with `handle_path`, redirects its
bare mount to a trailing slash, and rejects requests outside the mount. The
application uses its normal non-root Compose role checks and real PostgreSQL,
with a job-local synthetic account and fixture data. No evaluator or paid
provider is necessary: a genuinely queued run supplies durable SSE events.

Each mount requires exactly two Chromium cases, without retries or skips:

- Login, protected deep links, reload, loaded scripts/styles, logout and re-entry;
  all same-origin browser requests remain under the selected mount.
- A persisted factor deep link and real ZIP export/download, authenticated SSE
  through Caddy, unauthenticated SSE rejection, and the UI's live stream URL.

The strict JUnit check must pass at **both** mounts before producing
`var/subpath-acceptance.json`. The uploaded artifact contains only the tested
head SHA, image IDs, prefixes and outcomes, not credentials, response bodies,
traces or screenshots. Container cleanup is restricted to exact IDs with the
invocation's ownership label. All resources and command durations are bounded.

Pure guard tests can run without Docker:

```sh
python -m pytest tests/mineagent/test_subpath_acceptance.py -q
```

The real harness requires the explicit hosted opt-in and masked ephemeral
credentials established by the workflow. Passing pure tests or collecting
Playwright cases is **not** evidence that the actual proxy stack passed.

Caddy reference: <https://caddyserver.com/docs/caddyfile/directives/handle_path>.

The upstream file capability is visible in the
[official Caddy image source](https://github.com/caddyserver/caddy-docker/blob/9234f5785658cae8129bfae366e196d0670468ac/2.11/alpine/Dockerfile).

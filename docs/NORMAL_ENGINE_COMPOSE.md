# Packaged normal RD startup (explicit operator opt-in)

This follow-on packages the isolated SDK and operator configuration needed by the
normal 026 branch. It does **not** enable it by default, deploy anything, supply a
credential, or claim the full normal026/027 Compose browser gate has passed.
Normal027 provider image/Settings/worker integration remains a separate required
seam; the sealed fixture OE image must never be substituted for it.

## Application images and configuration

`infra/application.Dockerfile` builds a separate hash-locked SDK interpreter at
`/opt/mineagent-provider/bin/python` and copies it into API and research images.
Their existing system dependency closures remain separate and unchanged. API
admission checks the interpreter locally, so copying it only to the worker is
insufficient. The locked child checks OpenAI 2.54.0/httpx 0.28.1; the old research
OpenAI 2.6.0 is not a fallback. No host `.venv` is mounted.

The full Compose profile forwards the same `RDAGENT_*` declarations only to API,
research-dispatcher, research-worker and recovery reconciler. Dispatcher requires
the private credential environment for declaration validation, but makes no
provider HTTP call. All research/live/synthetic switches
remain false by default, runtime profile null and model profile list empty.
Use the exact server declarations documented in `RD_CONFIGURED_PROVIDER.md`.
There are no public owner, secret, interpreter or runtime image form fields.

An operator who has separately accepted the gates may supply a private env file
outside this repository. Set `MINEAGENT_PROVIDER_ENV_FILE` to its absolute path,
pass that same file with Compose `--env-file` for interpolation, and add
`-f infra/compose.providers.yml` after `-f infra/compose.full.yml`. The overlay
injects the explicitly named secret environment variables only into these four
trusted services. It adds no sockets, ports, capabilities or candidate credentials.
It also opts the research worker and evaluator reconciler into the host PID
namespace, as detailed below.
Do not print resolved Compose config or commit the private file. This is an
optional code path, not authorization to enable a live provider.

Enabled startup checks the actual isolated interpreter with `-I`, a ten-second
bound and a credential-free environment. Only trusted workers with their existing
Docker access inspect runtime images using the existing exact controller,
numeric and analysis inventory verifiers. API never gains a Docker socket.
Missing closures, identities or enabled credentials fail startup closed; no
provider request or candidate execution is performed by readiness checks.

## Build-only runtime preparation

The five targets are the existing `runtimes/rdagent_operational/Dockerfile`,
`runtimes/factor_execution/Dockerfile`, `runtimes/factor_normalizer/Dockerfile`,
`runtimes/qlib/Dockerfile` and `runtimes/qlib/Dockerfile.direct`.

Before invoking the wrapper, reproduce the exact existing source/wheel inputs
from the current `.github/workflows/mineagent-configured-analysis.yml` build
steps: pinned RD source commit, `.runtime/rdagent-wheels`,
`.runtime/rdagent-operational-wheels`, pinned Qlib materialized source and
`.runtime/qlib-wheels`, and `.wheelhouse-factor-numeric` from the normalizer lock.
Do not reuse a different revision's report or regenerate pinned proofs to accept
unknown bytes. This wrapper intentionally does not fetch an arbitrary upstream.

With the normal host backend tool closure installed and the existing authorized
local daemon available, explicitly run:

```
python -m script.build_rd_compose_images --output /absolute/new/private/build-evidence
```

It verifies frozen wheel proofs and bytes before building, records each actual
`--iidfile`, builds analysis as a no-network derivative of the just-built Qlib
parent, and reuses exact image inventory checks. Only afterward does it write
`rd-provider-runtime-profile.json` containing exact IDs and shipped dependency
locks. A failed/partial build leaves evidence but produces no accepted profile.
Tags, metadata-only fixture IDs and host interpreter paths are not accepted.
The application build context explicitly includes the existing numeric verifier's
non-Python manifest/seccomp/Dockerignore files; no blanket JSON/state inclusion
or weakening of source hash checks is used.

## Remaining acceptance

Local source/unit checks are not a container smoke test. The existing hosted
fullCompose gate must build these actual application images and exercise normal
queue/outbox/worker execution using a synthetic private test provider, browser
created demo data, explicit result release and normal OE once its real worker
image is ready. Root/SUBPATH, readonly/nonroot, cold recovery and existing gates
remain mandatory. Host PostgreSQL tests with a temporary SDK venv do not prove
this packaging path. The default legacy Compose acceptance remains unchanged.

The build wrapper pins the existing local Unix Docker socket explicitly and uses
`--config /nonexistent` with a minimal environment. It does not inherit a remote
Docker context, private registry authentication or provider credentials. A timeout
or interrupted build is not successful readiness; inspect retained partial build
evidence before any deliberate retry.

## Required hosted packaged-runtime subset

The configured-analysis job retains its existing exact 20 PostgreSQL cases,
required browsers/cold restore, and 112 isolated SDK cases. After those proofs
and exact disposable-PG cleanup, a separate sequential phase uses the real
`infra/compose.full.yml` API, dispatcher, research worker and reconciler images,
plus database/migration/bootstrap. It builds the genuine legacy image targets
required by ordinary worker startup and reuses the four previously measured RD
runtime images; no placeholder image identity or host worker is substituted.

The SDK HTTP double runs at localhost **inside the trusted research-worker
container**, preserving the normal HTTP-host restriction. Only maintained
synthetic fixture files are explicitly copied to that container's `/tmp`; no
host venv, checkout, Docker access for API, or provider secrets are added to a
candidate. The packaged SDK interpreter is checked in the actual image. Normal
authenticated HTTP submission, durable outbox, actual worker, four isolated units,
SDK request/identity/result/stop records, retained close values and Rank IC, and
exact request replay must all succeed. Only finite counts/booleans are uploaded.

This is an **application subset packaging acceptance**, not full frontend/browser
or live-provider acceptance. The synthetic retained dataset is test setup, not a
claim that ordinary dataset upload was exercised. Existing independent full
Compose/browser gates remain required. Local source tests do not establish that
this hosted phase has passed.

### Scoped trusted-host PID boundary

The optional provider overlay uses `pid: host` **only** for `research-worker`
and `evaluator-reconciler`. These already-trusted Docker supervisors must observe
host PIDs, start ticks and exact container membership, and match the controller's
actual kernel `SO_PEERCRED` to its retained child receipt. Private worker PID
namespaces cannot supply that proof for sibling native containers.

This increases trusted-service visibility into host processes; it is not an
isolation improvement or a blanket security recommendation. Both services retain
nonroot users, read-only root filesystems, all capabilities dropped,
no-new-privileges and the default seccomp policy. No privileged mode, host network,
host root/proc mounts or additional Docker permissions are added. API and
dispatcher remain in private PID namespaces with no Docker socket. Generated
candidates retain private PID namespaces, network-none and existing restrictions.
The protected numeric PID1 retains its existing exact SETUID/SETGID/KILL bootstrap
capabilities; the generated child is separately observed as UID/GID65533 with
zero effective/permitted/inheritable/ambient capabilities and no-new-privileges.
The base overlay-free deployment remains unchanged.

The required packaged smoke checks these actual container settings and runs a
read-only observer inside the real trusted worker. It compares the production
kernel peer receipt to fresh host PID/start-tick/container observations, rejects
an unrelated live host PID and verifies a real numeric candidate's private PID
namespace. The synthetic source pauses three seconds solely to permit this
observation, inside its unchanged ten-second numeric deadline; no parser exception
or timeout extension is allowed. No host PIDs or raw diagnostics enter artifacts.
Local tests do not prove Docker acceptance; this boundary still requires the
hosted packaged gate. This code/configuration change does not authorize deployment.

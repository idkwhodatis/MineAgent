# Configured RD model profile (026)

This is the new model-backed branch, not `fixture.rdagent.v1`. Its migration and
complete HTTP → queue → controller → SDK → numeric/analysis gate must pass before
an operator enables it. Local SDK-double tests are not live-provider acceptance.

## Operator-owned configuration

- `RDAGENT_RESEARCH_ENABLED` controls research submission.
- `RDAGENT_PROVIDER_RUNTIME_PROFILE` is the strict
  `mineagent.rdagent-provider-server-profile.v1` declaration: exact accepted
  controller, candidate, normalizer, analysis image IDs; all four dependency
  locks; installed Qlib parent image ID. It is not supplied by the user.
- `RDAGENT_PROVIDER_PROFILES` is a JSON array of strict
  `mineagent.rdagent-operator-model.v1` declarations. Each contains the existing
  validated connection profile (ID, owner allowlist, host endpoint, chat model,
  secret **environment variable name**, temperature 0, maximum output <=4096,
  request timeout <=8 seconds and 262144-byte request/response limits), plus
  embedding model/dimensions, JSON-object support and declared price bounds.
- `RDAGENT_PROVIDER_PYTHON` must identify an explicit absolute executable host
  interpreter. The SDK child checks installed OpenAI 2.54.0 and httpx 0.28.1
  after containment and before SDK/network work. No fallback to the API Python.
- `RDAGENT_SYNTHETIC_PROVIDER_ENABLED` and `RDAGENT_LIVE_PROVIDER_ENABLED` are
  separate, default-false transport switches. Synthetic declarations and usage
  must remain labelled synthetic. Enabling one does not enable the other.

Endpoint, secret value/reference and interpreter path never enter user request
bodies, controller bootstrap, model catalog or generated candidate environment.
No credential is supplied or enabled by this implementation. Missing credentials,
embedding declaration, SDK interpreter or price bounds blocks a new request.
Model declaration is not a guarantee that an external service is reachable.

## Normal user path

Use the existing authenticated `/engine-runs` catalog, preflight and submission
routes, selecting an owner-authorized model profile and ordered immutable steer
versions. The server returns the exact model binding, steer snapshot and preflight
hash. Submit the same normalized body/hash with a durable `Idempotency-Key`.
Accepted replay is checked before today's enabled flags or active steer heads.
Editing/disabling a steer affects future submissions, not an accepted queued run.

The initial profile supports one output session and one iteration, with declared
lookback history and a separately retained forward-label horizon. Generic bounded
OHLCV factor hypotheses are not replaced by the maintained momentum source.
Each generated source is derived from retained model responses, recomputed in the
isolated numeric process, and evaluated against the predeclared objective. Invalid
code or undefined analysis is not reported as success. Guidance consumption does
not prove that the model obeyed it; availability remains declared, not PIT-certified.

## Bounds and accounting

The controller retains its original 60-second deadline. Each SDK call has at most
8 seconds within that deadline; there are no automatic provider retries. The SDK
receives its private request only after its exact identity receipt is retained, and completion requires
positive whole-process-group termination. Missing/late evidence remains uncertain.
Actual provider tokens, operator-declared conservative charge and unknown external
billing are distinct. Missing prices are not zero. Missing usage cannot complete.
The installation also limits unresolved SDK concurrency and accounts for pipe and
retained receipt peaks; a run's token budget is not a substitute for that limit.

The future between-stage steering/workflow extension does not hot-edit this
immutable run configuration or extend these physical deadlines.

### Failed numeric execution is not a fabricated code diagnosis

The existing protected numeric stop receipt proves whole-unit containment, not an
exit code or an ABI-error cause. An absent output or arbitrary host exception must
not be converted into a model-facing assertion that generated code was wrong.
Such runs retain their generated source and recorded process/model obligations,
but cannot produce a completed snapshot, invented Qlib measurement or adopted
factor. A protected versioned failure report is required before automatic critique
can rely on a numeric failure cause. Actual upstream CoSTEER itself raises
`CoderError` after negative evaluation feedback and need not issue knowledge
embeddings on that path; the success-only returned boundary is not fabricated.

Terminal attempt status and `sandbox_stopped` require both the Docker inventory
and every admitted host SDK transport's retained intent, started identity, positive
whole-group stop and conservative settlement. Recovery re-reads the same immutable
receipt bytes; PID absence/reuse or a stopped controller cannot replace that proof.
A missing SDK receipt leaves the attempt nonterminal and its obligations held.

## Explicit next integrations

This cut does not activate arbitrary existing-upload compositions: the separately
implemented declared-upload adapter still needs its explicit normal preflight,
source-policy and cold-closure dispatch. Nor does it apply guidance between stages.
That successor must atomically allocate a new workflow-bound child run with the
actual combined manual/external/eligible-feedback snapshot; it cannot attach an old
run or change the already accepted config.

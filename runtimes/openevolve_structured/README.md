# Proposed 027 structured-factor search runtime

This is a **separate, unregistered** JSON/full-rewrite controller profile, not an
extension of 024 `factors.none` or its Python source/candidate/checkpoint ABI.
The fixed controller invokes installed OpenEvolve; it never imports a generated
candidate. Candidates are canonical closed-program JSON and only the declared
four operator parameters may differ from the seed.

The image requires the measured 024 protected controller as its explicit build
parent. The build checks its complete phase8 package inventory, changes only four
upstream import seams, and checks the new full installed-source inventory before
running. It does not claim the predecessor's wheel/source checksum. Dependencies
and upstream search algorithms are unchanged. No dependency install occurs.

New observer/broker/evaluation/checkpoint protocols are distinct. Each native
save gets a separate ordinal (1–4), including repeated final-frontier saves.
Opaque native JSON checkpoint files are transferred only after a complete held
inventory, in bounded chunks, with exact ACKs and no uncertain retry. Initial
seed-plus-up-to-two-mutation operation only: checkpoint resume is unsupported.

An evaluator response must bind the candidate/program/execution/frozen input
binding and declared objective. The host must independently resolve a real
persisted candidate and stopped structured engine outcome; a DTO, digest, parsed
candidate, or controller ACK is not launch or positive-stop authority.
The objective projection reads the complete validated structured result and uses
fixed-window `total_return_pct - max_drawdown_pct` in percentage points, with an
explicit optional minimum closed-trade count. It is neither annualized return nor
a claim of causal financial improvement.

## Still required before registration

* 027 persisted fixed snapshot/market/domain/objective/budget binding and actual
  candidate/parent → structured version/execution/outcome relations;
* fresh current-fence/peer host handlers, reservations, controller/evaluator
  issuers, cancellation/recovery, and exact shared-schema dispatch after 026;
* normal owner preflight/submit/idempotent outbox/worker/UI/result workflow;
* new image build and actual installed controller + spawned worker hook test;
* real seed and generated JSON child reaching the protected AKQuant engine,
  measured outputs/parent linkage, full stop and cold/read/public-release gates.

Local config/import and actual-file tests do **not** satisfy these gates. The
existing environment was not patched or reinstalled during implementation.

## Normal027 packaged startup (local implementation, actual gate pending)

The normal runtime has its own `Dockerfile.provider`; the initial fixture recipe
is not renamed or treated as operator-backed. With already-reviewed installed OE
and trusted AKQuant parents available locally, the explicit build-only command is:

```
python -m script.build_oe_structured_images --output NEW_PRIVATE_DIRECTORY \
  --controller-parent sha256:EXACT_INSTALLED_OE_IID \
  --engine-parent sha256:EXACT_TRUSTED_ENGINE_IID
```

Both derivatives build with `--pull=false --network none`. Verified local tags
are temporary build aliases; the emitted profile retains only exact image IDs.
This command performs metadata checks, not the required actual controller/engine
acceptance, and never starts services or enables a model.

`infra/compose.full.yml` passes the same optional `OE_STRUCTURED_*` settings to API,
dispatcher, worker and reconciler. Research and both provider modes default false;
profiles default null/empty. The SDK interpreter is the isolated pinned
`/opt/mineagent-provider/bin/python`. Explicit enabling requires an operator
runtime/profile and its named credential through the existing private provider
env-file overlay. Credentials remain on the trusted host. Worker/reconciler
startup verifies actual controller/engine image identities and parent closures;
API/dispatcher do not gain Docker privileges. Native children retain private PID
namespaces and disabled network. No automatic provider call is a startup check.

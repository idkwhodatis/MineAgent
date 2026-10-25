# OpenEvolve patch series 1: process and thread primitives

This patch is intentionally incomplete as an engine integration. It changes only:

1. Require global `spawn` before controller construction and use the explicit
   spawn context for both the controller event and ProcessPoolExecutor.
2. Require one worker with `max_tasks_per_child=None`, including revalidation at
   startup. No private CPython process-creation hooks are added.
3. Carry the caller's captured Context into the evaluator ThreadPoolExecutor.

The native selection, evaluation, score, population and search algorithms are not
replaced. This series does **not yet** implement durable observer callbacks,
control-abort propagation, population fail-stop, safe checkpoint frontiers or the
full orchestration process profile. It must not be enabled as production search.

`apply.py` validates the exact source commit identity declared by the manifest,
every original file hash, patch bytes, allowed paths, exact hunk positions/context
and resulting hashes before writing any file. It accepts no fuzzy matching. Use
only after the baseline materializer/verifier has checked the complete upstream
source tree. A repeated application rejects already-patched source; do not retry
against an uncertain/partially written build tree. Discard that private build
candidate and prepare a fresh verified tree instead.

```sh
python runtimes/openevolve/patches/apply.py /private/verified/source
SOURCE_DATE_EPOCH=1792818759 /isolated/build/bin/python -m build \
  --wheel --no-isolation /private/verified/source --outdir /private/patched-wheels
```

The patched wheel has its own hash; it is not the unmodified baseline wheel and
must never be installed under the baseline verifier's original-source claim.
Install into a separate environment with the same frozen dependencies and run
`python runtimes/openevolve/patches/runtime_smoke.py` there. This smoke starts one
real local spawned pool worker (returns only its PID), rejects absent/incompatible
bootstrap policy, and exercises the installed evaluator's executor method to
observe a ContextVar. It does **not** run a strategy, return financial scores,
prove Docker containment, or claim actual OpenEvolve search acceptance.

Local evidence: all nine patch/hash/drift tests pass; separately installed patched
wheel passed the real spawn/thread primitive smoke. Docker and full engine tests
remain separate mandatory gates. Upstream retains Apache-2.0 licensing; modifications
are the MineAgent-specific process/context boundary changes listed above.

## Optional second series: partial controller fail-stop

`control-manifest.json` / `0002-controller-fail-stop.patch` is a separate opt-in
series applied **after** the first series using `apply.py --control SOURCE`. It
requires the exact first-series output hashes. It changes the native controller's
submission/result-application swallowing handlers, uncertain future timeout, and
rejected-candidate checkpoint error into the installed MineAgent observer's
terminal `SearchControlAbort`. It preserves the native algorithm and does not add
arbitrary configurable callbacks.

`control_smoke.py` fault-injects the actually installed native controller methods:
failed database insertion, rejected-candidate checkpoint publication failure,
uncertain future timeout, and failed selection. Assertions require the epoch to
be marked untrusted and prohibit replacement submission after failure. These are
control-flow fault tests with injected collaborators, **not** actual search or
financial evaluator acceptance. The source/context smoke remains applicable.

The second series still does not cover seed/load guards, durable proposal/context
history, safe completed-frontier checkpoints, or complete process containment.
Keep research admission disabled until those independently tested boundaries exist.

## Optional third series: host observer lifecycle seams

`observer-manifest.json` / `0003-host-observer-lifecycle-seams.patch` requires both
prior series and is applied with `apply.py --observer SOURCE`. It calls only the
fixed installed `packages.openevolve_adapter.observer` module, never a user-named
factory. It passes actual selected parent/inspiration order and a deep copy of the
native snapshot before submission, adds the host descriptor to the worker call,
and enters worker scope before lazy initialization. A wrapper emits one ordinary
worker outcome for every return, including parse/rejection paths. Raw response,
applied source and enforced source are separate observation callbacks. Candidate
scope precedes temporary-file creation/evaluator dispatch; the actual file-loaded
seed is checked before its first evaluation. Callback errors fail-stop.

`observer_smoke.py` exercises these seams in the installed patched upstream with
an explicitly in-memory recorder and injected component probes: malformed and
no-match proposals, applied/enforced source, candidate scope, snapshot mutation
isolation and seed rejection before evaluation. This is **not** durable host/PG
history, a real financial evaluator, or whole-loop acceptance. No provider calls
are involved. Full rendered prompt references, population receipts, seed/load
cleanup and authoritative checkpoint frontiers are not implemented by this series.

## Optional fourth series: actual prompt render-selection capture

`prompt-manifest.json` / `0004-rendered-prompt-reference-seams.patch` applies with
`apply.py --prompt SOURCE` after the observer series. It records references in the
native rendering loops, after native previous-list trimming/reversal, top slicing,
random diverse selection and inspiration deduplication. It does not rerun selection
or change its order. Program dictionaries passed to observers are copies, so a
recorder cannot alter the renderer's inputs through those arguments.

Roles are `previous_summary`, `top`, `diverse`, `inspiration`. **Previous summaries
contain changes/metrics, not the program's source code**; their source identity is
provenance only. Current source and the actually rendered/truncated parent artifact
section are recorded separately. `prompt_scope` brackets each whole build and
`prompt_complete` runs only after successful rendering; callback failure is terminal.
Only the fixed supported templates are admissible. Arbitrary templates that omit
computed sections are not supported by this evidence contract.

`prompt_smoke.py` uses the actual installed native sampler with an in-memory
recorder. It checks exact role/order, stochastic selection called once, deduplication,
artifact truncation, observer input isolation, scope reset across repeated builds,
and failure before completion. With `--baseline-sampler PATH` it first verifies the
original source hash then compares final prompt bytes against the original sampler
under identical RNG state. The local differential probe passed. This proves these
rendering seams, not durable host registration or model-call acceptance.

## Phase 5: contiguous frontier and native checkpoint seams

Apply `apply.py --frontier SOURCE` after phases 1–4. This patch requires the
fixed observer checkpoint APIs and `checkpoint_native.py`; absent host callbacks
fail closed. It does not implement host persistence or make admission operational.

- Before result application, require the next contiguous native iteration. A
  surprising out-of-order completion aborts instead of changing native scheduling.
- Acknowledged error/accepted/clean-rejected terminal outcomes establish K, then
  checkpoint before replacement submission; early exits use the same boundary.
- Seed admission establishes K=0 and checkpoints before pool startup. Existing
  cleanup now also covers seed and load failures. A supplied missing checkpoint
  never silently falls back to a fresh seed.
- Final native saves use the actual processed frontier. Native serialization is
  unchanged; setting and verifying `database.last_iteration == K` also makes the
  upstream zero-argument fallback safe for seed K=0.
- Fixed host callbacks supply fresh private output directories; all native files
  close before after-checkpoint publication acknowledgement. Strict inventory and
  schema validation precede native load; postload projection equality rejects
  native reconstruction repairs. The initial profile is Python, one island,
  inline artifacts only, no native prompt logs, and a bounded state projection.

`frontier_smoke.py` exercises actual installed native controller/save/load methods
with explicitly in-memory callback recorders: ordinary failure/accepted/rejected
frontiers, early exit, configured-max final-save regression, out-of-order result,
partial mutation, publication failure, seed/load cleanup, native roundtrip and
postload semantic drift. These are method probes, not model/financial execution,
process containment, host persistence or cold-restore acceptance. The strict
helper does not authenticate score receipts or external ancestry: the host must
validate those bindings, exact byte inventory, lease, event/application frontier
and retained prefetched tail before committing a resumable pointer. The previous
phase-specific smoke scripts describe their own wheel versions, not automatic
acceptance against this stricter later callback contract.

## Actual API/spawn loop probe (synthetic, not financial acceptance)

Run `PYTHONPATH=. PATCHED_VENV/bin/python runtimes/openevolve/patches/full_loop_smoke.py`.
This invokes the pinned public `run_evolution` twice: fresh seed plus two mutations,
then native checkpoint continuation from K=2 to K=3. It uses `build_config`, the
original importable `broker_client.init_client` (including Config pickle identity),
real spawn workers, native prompt/diff/application logic, native save/load and
real MineAgentLLM request/context/reply validation. Two distinct model-worker PIDs
and four candidate scopes are asserted. Both socket connection methods are
blocked and attempted calls recorded; the successful probe requires zero.

Explicit test-only doubles are installed only when this script is run directly
or imported by multiprocessing as `__mp_main__`: observer callbacks and broker
`_exchange` are local recorders/fixture responses. The temporary evaluator reads
an integer from source and returns a synthetic control-flow score; it does not
execute a strategy, perform a backtest or create a financial receipt. The temporary
checkpoint directories and JSONL records are not authoritative persistence. This
proves API/startup/spawn/config/loop/native-serialization compatibility, not host
IPC authorization, budget enforcement, Docker containment or production readiness.

## Phase 6: mandatory objective receipt gate before metrics return

Apply `apply.py --evaluation SOURCE` after phase 5. Inside the actual candidate
scope, `Evaluator.evaluate_program` now awaits its original implementation,
requires `observer.evaluation_complete(metrics)`, then returns unchanged metrics.
Missing host receipt/ACK is terminal. Only an explicitly recorded ineligible
objective with exact empty metrics raises `CandidateEvaluationRejected`: native
worker handling records an ordinary failed mutation, whereas the enclosing seed
admission guard makes the same seed failure terminal. An unverified upstream
`{"error": 0.0}` fallback is **not** an eligible zero score or a clean rejection.

`full_loop_evaluation_smoke.py` supersedes the phase5 probe for this wheel. It keeps
the real `observer.evaluation_complete` and supplies explicit synthetic typed
EvaluationResponse records plus a test-only host ACK. Run normally and with
`--reject children`, `--reject seed`, `--reject fallback`. These four actual API/
spawn probes respectively verify eligible execution+continuation; ineligible
children advancing failure frontiers with seed-only population; ineligible seed
stopping before model/checkpoint; and native error-zero fallback aborting without
advancing K=0. An eligible combined_score of zero remains accepted. Financial
execution, authoritative host receipts and containment are still not established.

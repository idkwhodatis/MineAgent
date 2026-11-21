# OpenEvolve v0.4.1 qualification (not a runtime image)

This is an opt-in local dependency and feature proof. It is not an alternate
worker entry point and is not included in any runtime Dockerfile, CI gate or
execution admission. The existing MineAgent 0.4.0 source, wheel hashes, eight
patch phases, historical provenance, closed checkpoint schema, leases and
deadlines remain unchanged.

The [upstream release](https://github.com/algorithmicsuperintelligence/openevolve/releases/tag/v0.4.1)
and [PyPI version](https://pypi.org/project/openevolve/0.4.1/) identify v0.4.1.
This proof builds from the exact tagged Git commit, not the PyPI binary. The
official release's `pyproject.toml` declares the same dependency closure as the
previous pin; the two existing lockfiles are pinned by digest here and stay
separate from the application. The builder materializes committed blobs only
with a closed build-input set and verifies 66 entries against a SHA-256
inventory before any build backend runs. The wheel checksum covers the local
reproducible build, not the upstream PyPI wheel.

## Reproduce locally

Use the pinned Python 3.12.14 and fresh paths for source, build and runtime:

```sh
work=$(mktemp -d)
src="$work/upstream"
git init "$src"
git -C "$src" fetch --depth 1 \
  https://github.com/algorithmicsuperintelligence/openevolve.git \
  73448d715968b3578d8458b16295e827edd2b6bf
git -C "$src" checkout --detach FETCH_HEAD
proof=runtimes/openevolve_candidate/proof.py
python -m venv "$work/build"
"$work/build/bin/python" -m pip install --require-hashes \
  -r runtimes/openevolve/build-requirements.lock
for n in one two; do
  python -I "$proof" materialize "$src" "$work/source-$n"
  SOURCE_DATE_EPOCH=1793601078 "$work/build/bin/python" -m build \
    --wheel --no-isolation "$work/source-$n" --outdir "$work/wheels-$n"
  python -I "$proof" verify-wheel "$work/wheels-$n/openevolve-0.4.1-py3-none-any.whl"
done
wheel="$work/wheels-one/openevolve-0.4.1-py3-none-any.whl"
cmp "$wheel" "$work/wheels-two/openevolve-0.4.1-py3-none-any.whl"
python -m venv "$work/runtime"
"$work/runtime/bin/python" -m pip install --require-hashes \
  -r runtimes/openevolve/requirements.lock
"$work/runtime/bin/python" -m pip install --no-deps "$wheel"
"$work/runtime/bin/python" -m pip check
"$work/runtime/bin/python" -I "$proof" verify-installed "$wheel"
```

The installed smoke checks the version/dependency inventory and installed
package bytes, then uses an upstream `OpenEvolve` constructor with an
incompatible mixed-mode configuration and confirms rejection before output is
created. It checks the new actionable `llm.model` rejection, deterministic
mixed-mode choices in an isolated fixture, preservation of the structured
full-rewrite mode/defaults, and value/deep-copy equivalence for a representative
plain native `Program` record. Python socket attempts are counted (expected
zero); this is not OS/container confinement evidence. No strategy module is
executed, no evaluator is started and no provider/credential is configured.

## Promotion boundary

The upstream changes to controller, process pool, database and checkpoint
scheduling are **not** a drop-in update. The current first spawn/context patch
does not apply cleanly to v0.4.1. The new worker mode also enters program
metadata/trace evidence, and upstream novelty/migration behavior changed.
Rebase all exact patch phases and regenerate reviewed patched artifacts before
considering a new production pin. Require unchanged native process/containment,
child receipts, closed checkpoint save/restore and configured workflow gates.
Do not enable mixed prompts, alternate per-island seed paths, best-improvement
checkpoints, or other upstream execution/transport modes on the existing
structured profile.

### Rebase sequence and required evidence

Rebase against the exact candidate source in order; regenerate each phase's
before/after, patch, and wheel digests before using any downstream phase. This
table identifies source loci and validation, not already-rebased patches.

| Phase | Rebase locus | Required proof |
| --- | --- | --- |
| 1: spawn and context | 0.4.1 adds controller outcome counters beside the constructor hunk; the process-pool file fails a stock `git apply --check`. Preserve the new counters and worker event-loop cleanup, but explicitly require spawn before any primitive and again before pool start, one worker and no recycling. Stock still calls `mp.Event()` and does not pass an explicit spawn context on Python 3.12. The separate evaluator hunk applies cleanly; stock still lacks the authenticated `copy_context().run` hop into its thread. | Installed `runtime_smoke.py`: real spawned worker, context propagation and startup rejection; extend to pre-start worker-count drift and recycling rejection. Later native containment must verify stop, not just loop cleanup. |
| 2–3: fail-stop, lifecycle | Result application and timeouts in `process_parallel.py`, plus the new worker-wrapper/mode split, evaluation wrapper and `controller.py` seed path. Retain fixed observer, scope-before-init and uncertain-state abort, regardless of upstream no-candidate counters. | Control and observer probes: fault during selection/result/timeout, actual one worker outcome, applied/enforced bytes, no dispatch after rejection. |
| 4: render references | `prompt/sampler.py` hunk applies against stock 0.4.1, but upstream's worker picks a mode-specific template and records it. Keep MineAgent's exact render identities; mixed mode stays off. | `prompt_smoke.py`: compare rendered bytes and reference order against exact candidate sampler, including RNG and scope reset. |
| 5–6: frontier and objective | Upstream interval/improvement checkpoint scheduling and no-candidate terminal behavior intersect controller checkpoint/load and result application. Keep ordered, acknowledged terminal frontiers; retain objective ACK before evaluation returns. | Frontier, evaluator bridge and full-loop probes: seed/child/fallback rejection, no partial success, saved-quiescent restore and exact receipt ordering. |
| 7–8 and structured derivative | Rebase sampling and submitted-worker placement around the actual `_submit_iteration` worker invocation; regenerate the structured hook import and installed inventories. Worker routing/program metadata and novelty/migration changes require review against closed evidence schema. | Sampling and placement probes, then installed structured pair, configured populated workflow, cold restore and real process absence/stop receipts; no looser deadlines or caps. |

The stock qualification wheel does not contain MineAgent's pool, observer,
frontier, objective or placement seams and cannot substitute for the
fenced runtime. The upstream worker event-loop reuse and cleanup do not prove a
single owned worker, evaluator ContextVar propagation, or confirmed shutdown.

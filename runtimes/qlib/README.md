# Qlib analysis — disabled integration slice

Exact Qlib: `54355232463878d2eebb91fe0ee5fa7fa1f5976c` / 0.9.8.
Exact RD-Agent runner: see `rd-source-inventory.json` (same audited upstream pin
as the independent CoSTEER profile). MIT notices are retained here.

## Implemented / checked locally

- `packages.qlib_adapter.direct`: actual Qlib `calc_ic`, pairwise-valid Pearson
  and Spearman, explicit undefined constants/singletons, defined-day means.
  `calc_long_short_return` supplies fixed half-quantile forward-label group
  spreads. These are **not** portfolio returns. Sorted listing ties and Qlib's
  half top-minus-bottom convention are explicit in the typed result.
- Versioned direct protocol binds separately frozen labels, calendar sessions,
  horizon, timezone, dataset/universe, snapshot and projected feature hashes.
  It does not re-fit preprocessing or alter factor protocol v1.
- Closed native binding and data-only evidence roles separate native prediction
  IC from actual portfolio turnover and compounded daily gross-minus-cost return.
  No direct metric can be presented as a native prediction/portfolio result.
- `verify_native.py`: real Qlib LGBModel fit/predict and TopkDropoutStrategy on a
  tiny generated binary provider. Training/validation/test windows are disjoint;
  generated factors are not renamed predictions. LightGBM model is native text,
  never a Python pickle. Real selected DatasetH feature matrices are captured.
- `verify_rd_runner.py`: real QlibFactorRunner executes a real baseline first,
  followed by `based_experiments=[baseline]`, normalized factor subworkspace and
  actual combined-factors branch. No `base_feature_codes`, fabricated baseline,
  normal upstream shell/Docker workspace, or default QlibFactorScenario discovery.
  Public workspace seam invokes only the fixed synthetic proof. This seam is
  **not** a production supervisor; it has no run/broker/result admission authority.
- Offline proofs deny socket connections; MLflow telemetry is disabled **before
  imports** using its supported environment switch. File tracking only; no
  `save_objects`, `read_pickle`, or imported model-object artifact.

## Packaging

`requirements.lock` records all 191 resolved declared Qlib dependencies for
CPython 3.12/Linux amd64, with archive hashes. `source-inventory.json` binds the
Qlib source package/build files and exact git archive. Qlib's compiled extensions
are built separately from that verified source with gcc/g++, NumPy, and the
separate `build-requirements.lock`; use `--no-build-isolation` after installing
those exact build dependencies. Runtime and build dependencies are not API or
legacy-engine dependencies. Gym is an upstream sdist dependency; release builds
must build it with the same separately locked build closure, not fetch an
unrecorded isolated build environment.

`build_runner_profile.py` emits deterministic, explicitly reduced wheels:

1. audited original RD core/CoSTEER/runner/Qlib modules plus template resources;
2. runner adapter/probe modules, requiring the separate runner closure;
3. Qlib-native adapter/probe modules, requiring Qlib and its own closure.

It changes packaging only, not upstream algorithms. `runner-requirements.lock`
uses **pandas 2.3.3 + pandarallel 1.6.5**: pandas 3.1 in the standalone CoSTEER
profile cannot import pandarallel's ExpandingGroupby API. Do not silently share
one incompatible lock. Original upstream RD packaging at this pin produces an
empty wheel, so these named reduced wheels are not called pristine upstream.

Local installed-only `-I` runner plus `-I -m runtimes.qlib.verify_native` proof
passes. Due to disk capacity, the runner's local proof environment references the
verified standalone RD dependency directory read-only, shadowing pandas and
adding pandarallel; it is **not** proof of a fresh isolated container installation.
The separate runner lock and wheels are the intended fresh-build inputs. A
shared old profile's metadata reports its pandas requirement in that local
overlay; this is not concealed as `pip check` success for the overlay. The Qlib
native environment itself passes `pip check`.

## Still required before activation

No public/DB engine admission, process confinement, installed image digest,
control/generation fencing, immutable object storage, label/holdout authorization,
restore closure, migration or route is activated by this slice. Parent owns
those integrations. Local socket-denial tests do not establish kernel network
isolation. No Docker, Postgres, hosted CI, paid/live provider or deployment proof
is claimed. The synthetic performance numbers have no investment meaning and do
not establish AKQuant equity or incremental factor benefit.

All production artifacts must be same-owner, same run/attempt/generation and
same dataset as every bound factor snapshot, with exact baseline/config/input/
model/prediction hashes. Restoring these artifacts must never execute Python
objects. Labels used for sealed holdout analysis must not feed RD development.

### Fit boundary and native closure

`selection.select_fit_labels` verifies calendar horizon and actual label availability
against a frozen training cutoff and validation-selection cutoff, both before the
next evaluation window. A disjoint observation window alone is insufficient.
It freezes accepted training/validation rows and all exclusions. The native proof
injects one late-arriving training label and excludes it **before** DatasetH;
actual consumed train/validation/test feature matrices are separately hashed.

Native manifest closure has eight roles: configuration, model text, predictions,
combined input, consumed learner feature matrices, actual daily report, baseline
receipt and canonical fit selection. `validate_native_results` closes both native
result bindings to the manifest, recomputes label selection and rejects report,
run, horizon, cutoff, or accepted/excluded-row substitution. This is validation
logic, not database authorization or storage-byte admission.

The final local Qlib build also rebuilt its compiled source wheel, gym and
pandarallel from hash-verified sources using the separately pinned build tools
with build isolation disabled. Local wheel hashes/compiler/tool-lock receipts are
in `../mineagent-evidence/qlib-local-build-receipt.json`; deterministic reduced
profile wheels were built twice and matched. Compiled native wheel byte-for-byte
reproducibility across machines is **not** claimed.

The fixture's RD experiment result carries **validation-only** IC/rank IC.
Native test-window prediction/portfolio evidence is kept separately in the
receipt and is not passed as a CoSTEER development score.

## Dedicated Docker proof candidate (not yet executed)

`.github/workflows/mineagent-qlib-runtime.yml` and this directory's Dockerfile
prepare two **fresh independent** environments, eliminating the local dependency
overlay. Build source and profile wheels are verified before execution. Source
sdists are built using the locked build environment with isolation disabled;
every resulting wheel is hash-locked again for offline final installation, and
both final environments must pass `pip check`. Signed Debian compiler/runtime
packages are recorded; their cross-machine binary reproducibility is not claimed.

The fixed container entry point uses installed modules only with `-I`. The job
requires nonroot, read-only root filesystem, network none, dropped capabilities,
no-new-privileges, bounded CPU/memory/PIDs and a bounded scratch tmpfs. Actual
baseline/combined Qlib results, direct metrics, late-label exclusion and separate
validation-only RD feedback are checked against the proof schema. There is no
image push, deployment, provider or production engine admission.

The repaired installed-only wrapper has passed locally and its actual JSON
passes the dedicated proof validator. A lifecycle regression now restores the
original working directory before removing RD's temporary workspace, including
exception paths; otherwise the following Python subprocess could not start.
The local runner still uses the documented dependency overlay, so this is not a
fresh independent-environment proof. Local Docker remains unavailable. Do not
treat the new workflow's presence as a passed Docker or hosted gate.
The frozen profile uses an explicit runtime-file list, excluding in-progress RD
assembly/checkpoint/persistence drafts. Any included runtime-file change requires
rebuilding and reviewing `wheel-proof.json` before the dedicated job is enabled.

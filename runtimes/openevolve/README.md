# Pinned OpenEvolve build baseline

This directory verifies **upstream dependency/import compatibility only**. It does
not enable search admission, execute a model, or claim real evaluator/containment
acceptance. The research adapter's hooks, context/broker wiring and hosted end-to-end
gates are separately required before an orchestration profile can use this image.

- Upstream: `algorithmicsuperintelligence/openevolve` at
  `8c65f74aa906abc0b0e9cae948ddf4c4c6570bdc` (source version `0.4.0`).
- License: Apache-2.0, original text retained in `LICENSE.upstream`; no upstream
  NOTICE exists at this pin. MineAgent's own build utilities are separate code.
- Python: 3.12.14, Linux amd64. All seven declared upstream dependencies are
  included; there is no reduced or silently optional runtime closure.
- `upstream-source.json` records every wheel-build input, its byte count and hash,
  plus exact commit/tree and deterministic source timestamp. Upstream is **not**
  vendored into this repository.
- `runtime-manifest.json` records the full installed dependency set, lock hashes,
  exact reproducible upstream wheel hash and immutable base-image digest. The
  digest was observed in the accepted N1 Compose provenance artifact (IDs and
  artifact hash are recorded); this new Docker image still requires hosted build
  and execution proof.

## Reproduce the baseline

Use a separate external upstream checkout at the exact commit. Never point the
build at user-uploaded Python or an arbitrary branch. With a fresh destination:

```sh
python runtimes/openevolve/materialize_source.py \
  --source /path/to/exact/openevolve-checkout \
  --destination .runtime/openevolve-source
python runtimes/openevolve/verify_build_inputs.py .runtime/openevolve-source
python -m venv /tmp/openevolve-build
/tmp/openevolve-build/bin/python -m pip install --require-hashes \
  -r runtimes/openevolve/build-requirements.lock
SOURCE_DATE_EPOCH=1792818759 /tmp/openevolve-build/bin/python -m build \
  --wheel --no-isolation .runtime/openevolve-source --outdir /tmp/openevolve-wheels
```

The unmodified upstream wheel must hash to
`b5d822abd49e9a977228838de593c1460dd61ae9bad21e44042bd53cf3b32e98`.
The build utility validates all input bytes before any backend is executed. The
Dockerfile repeats that verification and checks the wheel hash before installation.
A subsequent reviewed source patch must have its own patch/wheel identity; it must
not pass itself off as this unmodified baseline.

Create a separate runtime venv, install `requirements.lock` with `--require-hashes`,
then install the verified wheel with `--no-deps`. Run `pip check` and
`python runtimes/openevolve/verify_runtime.py`. The verifier requires exactly the
locked distribution set (apart from the installer pip), checks every installed
upstream package file against its source hash and imports all 47 modules while
blocking socket connections. It does not invoke any provider or search loop.

For Docker, the prepared source location above is the only external source input;
`docker build -f runtimes/openevolve/Dockerfile .` uses the sibling restrictive
Docker ignore file. Its default command is import verification only, not a
production orchestration entrypoint. No Docker daemon was available for the local
baseline proof; CI must build and run this image before claiming image compatibility.

## Local verified evidence

A fresh build venv and runtime venv installed successfully; two independent wheel
build invocations produced the same hash. `pip check` passed and all 47 modules
imported with networking blocked. The ten source/provenance contract tests pass.
These are build/import results, not proof of actual `run_evolution`, financial
observations, kernel isolation, or paid-model quality.

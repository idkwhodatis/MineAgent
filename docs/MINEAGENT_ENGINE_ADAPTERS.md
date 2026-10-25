# Engine adapters: disabled contract and build baseline

This slice follows accepted [N1](MINEAGENT_NEUTRAL_FOUNDATION.md). It introduces
pure adapter contracts and an independently locked upstream build baseline. It
**does not** admit new research purposes, register an engine runtime profile,
execute a factor candidate or run an OpenEvolve search. Existing legacy database
constraints, queues, request DTOs and runtime codecs remain unchanged.

## RD-Agent input and prefix contracts

`packages/rdagent_adapter` pins the audited RD-Agent and Qlib commits, separate
runtime lock identities, frozen OHLCV inputs, static membership and a bounded
factor implementation/refinement task. It does not claim autonomous hypothesis
research. The pure prefix projection produces exact canonical listing and
observation files, keeps unavailable members in the full calculation universe,
excludes observations unavailable at the host cutoff and records the actual
mounted lookback window. Membership and preprocessing readiness are explicit;
lagged publication cannot be backdated. Mounted inputs are recorded as **provided,
not proven read**. Source availability declarations do not establish market PIT.

The caller must first verify the frozen dataset and calendar against their hashes.
Pure projection cannot grant a process capability, prove source authenticity or
publish a feature row. Generated Python, native HDF output normalization, actual
FactorCoSTEER execution, Qlib analyses and their persistent closure remain separate
acceptance work.

## OpenEvolve source, execution and objective contracts

`packages/openevolve_adapter` defines a native-only search specification, exact
seed preparation identity, development-window alignment and a separately typed
objective. Raw observation metrics remain distinct from `combined_score`.
Negative and zero scores are valid; missing/nonfinite metrics are ineligible,
not silently replaced by zero. A minimum-trade constraint applies only when
explicitly declared. Metrics use the immutable objective definition and unrounded
observation values.

Source admission requires explicit UTF-8/LF bytes and exactly one standalone
mutation region. Descendants must retain protected bytes. Marker insertion can
prove AST preservation but **not** execution parity; actual isolated parity and
source-version publication remain required. The proposed operation context is
not accepted by the neutral fixture broker. An observation hash alone is not
host provenance: actual execution must bind the complete admitted dataset,
window, source, parameters, image and evaluation receipt before publication.
Native strategies still share their evaluator process with the engine; this
slice makes no stronger execution-trust or causal-exposure claim.

## Separately locked upstream import baseline

`runtimes/openevolve` builds the exact unmodified upstream commit
`8c65f74aa906abc0b0e9cae948ddf4c4c6570bdc`. Its build input inventory, Apache license,
wheel digest, complete declared dependency closure and immutable base image are
recorded separately from all existing environments. The source is materialized
from a verified external checkout; upstream code is not silently relicensed.

The dedicated workflow builds the local image, checks the installed distribution
and source inventories, and imports all upstream modules with no network. This
is **build/import compatibility only**, not `run_evolution`, broker/evaluator
integration, checkpoint recovery, generated-code containment or research quality.
The image has no production admission. Adapter patches require separate identities
and actual-loop tests; they cannot claim the unmodified wheel's provenance.

## Remaining activation gates

Each engine activates independently, only after its immutable domain schema,
exact owner/run/attempt references, typed broker contexts, fixed runtime manifests,
physical media closure, recovery, API/browser journeys and cold restore are tested.
Factor activation alone changes strategy nullability. New purposes never reuse
legacy strategy checkpoints or image fields. Generated candidates receive no
orchestrator IPC/tickets and require separately reviewed hostile-child containment.
Default new-research cutover remains the later migration stage.

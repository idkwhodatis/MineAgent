# Phase7 sampling-state observation (separate, disabled candidate)

This directory is intentionally outside the frozen `runtimes/openevolve/**` cut.
The patch requires the complete exact phase6 package source inventory and wheel
identity, then inserts full native state snapshots immediately before/after the
single existing `sample_from_island` call. It preserves sampling, RNG, native
population algorithms and worker descriptor format.

The fixed `packages.openevolve_adapter.sampling_control.before_submit` receives
iteration, selected parent, inspiration IDs, copied worker snapshot, before state
and after state. A host ACK is required before executor submission. Unexpected
snapshot/sampling/ACK failures take the existing submission fail-stop path; there
is no rollback or blanket feature-stat exception. The helper/host own causal
cursor validation separately from processed terminal frontier K.

Apply from repository root with `PYTHONPATH=. python
runtimes/openevolve_phase7/apply.py PRIVATE_PHASE6_SOURCE`. Baseline runtime and
phase6 proofs are not modified. The full-source inventory guard rejects drift,
extra package files, symlinks and repeat application before writing.

`full_loop_sampling_probe.py` wraps the existing actual API/spawn probe with
explicit synthetic sampling ACKs. Run normally, `--reject children`, `--zero`,
and `--ack-failure`. It records actual prefetch state changes separately from K,
requires error/rejected terminal state to equal the acknowledged sampling cursor
except last_iteration, checks surviving Program bytes, and refuses dispatch on
ACK failure. These are synthetic compatibility probes, not durable host,
financial evaluator or production containment acceptance.

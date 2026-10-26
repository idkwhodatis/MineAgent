# Disabled numeric maintained-fixture proof

This is **not** production engine activation or arbitrary-source containment
acceptance. Only the maintained `abi_fixture.py` source is admitted by the proof
workflow; the ordinary process registry still rejects these profiles.

The first hosted case uses four exact read-only inputs: source.py,
observations.json, listings.json, manifest.json. A separate candidate image runs
Python `-I /input/source.py` with fixed numeric thread limits and disabled HDF
plugins. Its primitive result.h5 is opaque to the host. A fresh, separate
normalizer image consumes result.h5, listings.json and manifest.json; it performs
strict primitive HDF grammar validation and emits values.parquet. The fixed output
is compared to an independently installed/hand-calculated three-row fixture digest.
The host never opens HDF or unpickles upstream/native artifacts.

Both images retain the audited protected-PID1 lifecycle from d36cce6, with a
**distinct** fixed numeric child/exporter seam. No proof-marker observer is forked;
no result.bin fallback is reachable. The exporter runs only after actual ECHILD,
with no capabilities, and reads only the fixed role's nonempty readable regular
nlink1 output. The root-owned0733 output directory cannot be chmod'ed by the child;
the maintained fixture and normalizer explicitly set files0644. Numeric framing
uses MANU1 and schema mineagent.numeric-quiescence.v1, max16MiB; its separate
Docker attach parser retains64KiB frames and fixed17MiB aggregate. The original
proof's4MiB/result.bin ABI and5MiB attach cap are unchanged.

PID1 never parses native content or waits/writes on the exporter pipe. Its original
absolute8second deadline remains active under a dead/stalled reader. The driver
requires pre-start HTTP101 attach, exact inspected namespace/resource/mount/image
policy, all mounted input byte hashes, launcher termination, protected quiescence
frame received while original unit is alive, complete EOF drain with no trailing
frames, independently observed original kernel exit, DockerStopped124
and all observed PID identities gone within the unchanged one-second tolerance.
Cleanup is not counted as positive stop.

Every runtime source, exact dependency lock and shared lifecycle file is frozen in
runtime-draft-inventory.json; verify_inventory.py fails on drift. Candidate wheels
are NumPy+h5py; normalizer additionally PyArrow, installed only from the existing
hash-locked wheelhouse. No RD/Qlib/provider/database credentials or IPC mount are
present. Native dependencies are never imported by privileged PID1.

## Acceptance boundaries still open

The dedicated workflow initially requires exactly two successful protected units
and all focused contract tests without skip. Its success proves only this fixture.
Before any registry activation, separate numeric adversarial cases must prove:

- native-loaded child cannot signal/ptrace PID1 or inspect protected FDs; detached
  grandchildren cannot survive the original deadline;
- full numeric output and killed/stalled primary reader, with actual live protected
  exporter observation and kernel-exit-before-reader-close ordering;
- oversized, fifo, symlink, unreadable, malformed HDF, external/soft links, VDS,
  filters/plugins, variable-length/reference/object dtype rejection inside the
  fresh normalizer, not in an application/orchestrator process;
- stop/extraction/publication/storage-budget conservation and versioned evidence
  persistence, real PostgreSQL isolation/restore and complete RD adoption flow.

Pure/mock tests, exact hashes and prompt descriptions do not establish those facts.

Before the two-unit gate, each installed image runs a separate nonroot65533,
network-none, read-only, capabilities-empty dependency import probe. It reports
actual Python/distribution/module/native-library identities, HDF5 version and
`pip check`; missing shared libraries fail, with no compiler/apt fallback. The
numeric fixture supplies actual candidate/normalizer image and lock hashes. Other
RD/Qlib fixture pins are not exercised and are not accepted by this proof.

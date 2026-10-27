# Phase8 placement observation proof — disabled, separate from phase6/7

One hash-guarded addition after actual `executor.submit` calls the new
`placement_observer.observe_submitted`. It observes the exact pinned one-worker
`executor._processes` inventory, requires process.is_alive/exitcode, reads that
worker namespace PID's `/proc/<pid>/stat` start_ticks twice, then asks a host
placement handler to acknowledge the epoch/intent/PID/start tuple. No native
sampling, RNG, selection, evaluator or algorithm is replaced. A failed/uncertain
placement poisons the epoch; a submitted task is not assumed undone.

The default `send_placement` deliberately aborts. There is no enabled host codec,
listener, expected identity issuance or registry entry. Existing v1 observer
EVENT allowlist is unchanged. The next host codec must authenticate the controller
and bind its committed namespace/cgroup; worker IPC SO_PEERCRED then supplies the
host PID, whose kernel NSpid/start/ns/cgroup must equal the controller-attested
expected tuple. Never accept worker-supplied start ticks or first-ticket arrival.
This avoids a global /proc scan and distinguishes resource tracker from the
controller's exact ProcessPoolExecutor worker inventory.

`placement_probe.py` executes actual pinned run_evolution/spawn. Explicit local
host doubles verify proc identities, parent lineage and NSpid, publish immutable
placement observations, and use a bounded two-second filesystem waiter in workers
before synthetic work. The waiter holds no SQL session/lock. Reused pool identity
requires prior explicit assignment closure. Denied placement takes the fail-stop
path. Local proc checks are NOT production SO_PEERCRED/namespace/SQL/containment
acceptance; the normal worker assignment/phase boundary remains disabled.

Apply only to a private full exact phase7 source inventory:
`PYTHONPATH=. python runtimes/openevolve_phase8/apply.py PRIVATE_PHASE7_SOURCE`.
Build using the already pinned build environment and SOURCE_DATE_EPOCH=1792818759.
Phase6/7 files, sources and wheels are untouched. Probe invocation uses the
separate installed phase8 wheel plus explicitly supplied local platform modules:
`PYTHONPATH=. PHASE8_PYTHON runtimes/openevolve_phase8/placement_probe.py`
and `--deny-placement`. This is not the installed-only Docker proof cut.

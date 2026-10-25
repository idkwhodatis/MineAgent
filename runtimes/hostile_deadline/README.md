# Disabled hostile-child deadline and quiescence draft

**Not runtime admission or accepted containment.** Only pure contract/control-flow
checks have run locally. `acceptance.py` must run on an approved Docker CI host;
it never executes the hostile fixtures outside Docker. No existing launcher,
manifest, purpose, database schema, or account security setting is changed.

## Distinct privilege and namespace contract

This is not the N1 same-UID trusted-fixture profile. A fixed, installed Python
module is PID 1 of a private PID namespace, UID/GID 0. Docker drops every capability
and adds exactly SETUID, SETGID and KILL. Before fork the wrapper validates PID 1,
all root IDs, bounding/active/ambient/inheritable capability sets and
no-new-privileges, sets itself nondumpable, and fixes a monotonic deadline from a
host-supplied absolute timestamp with at most ten seconds remaining. No extension,
child-controlled ACK, stdout write, or provider/IPC call can postpone this limit.

The trusted fork branch creates a separate session, clears supplementary groups,
permanently drops all IDs to 65533 and all active capabilities to zero, verifies
those settings, closes every inherited non-stdio FD, redirects stdio to /dev/null,
and executes only `/input/candidate.py` using installed Python `-I` and a fixed
small environment. No candidate code runs before privilege reduction. The child
cannot signal the differently owned parent, inspect its memory/FDs, regain root,
or inherit a writable host socket. Child nondumpability is set before exec, but
is **not** relied on after exec (exec/kernel rules and hostile code can change it).
Parent nondumpability and different credentials protect the trusted wrapper.

The parent retains only CAP_KILL, never execs, and never parses candidate output.
When the direct child finishes it repeatedly sends SIGKILL to the remaining
private-namespace processes and reaps adopted descendants until ECHILD. Only then
it writes a fixed, root-only `/status/quiescent.json` receipt. It holds the same
bounded tmpfs output until the original deadline or an authenticated host stop;
it does not extend time for extraction. Timeout or any wrapper failure exits
PID1, causing the kernel to kill the entire namespace. A blocked kernel task can
delay physical teardown; the host must still positively verify stopped state,
never assume a timeout is a stop receipt or refund reservations prematurely.

Kernel basis: [PID namespace init and orphan adoption](https://man7.org/linux/man-pages/man7/pid_namespaces.7.html),
[Linux kill(-1) semantics](https://man7.org/linux/man-pages/man2/kill.2.html).
The quiescence argument additionally assumes **no host docker-exec, namespace
injection, or foreign peer process** during the attempt. This is a host launcher
contract, not something candidate code can grant. The source-only mount is
readonly; /tmp is 4 MiB, /output 16 MiB, and the root-only status tmpfs 64 KiB.
Memory+swap are both capped at 256 MiB, PID count 16, CPU 1, network none, IPC none,
readonly rootfs, no-new-privileges, the byte-pinned official Moby seccomp profile, and checked masked/readonly paths.
The vendored profile is from `moby/profiles` commit
`4b8214fc4971e06172938a1f1554ccff6647ee80`, SHA256
`6416b47770785a41ac59073cdc77d9fe98517df2799dc83ef207e622de3053f6`;
its Apache-2.0 license is retained as LICENSE.moby-profiles. Source:
https://github.com/moby/profiles/blob/4b8214fc4971e06172938a1f1554ccff6647ee80/seccomp/default.json.
The host masked/readonly inventory follows Moby's pinned default algorithm at
`b711d21792db6db488de976eb3c4a653c92c3bc3/daemon/pkg/oci/defaults.go`, including
existing per-CPU thermal_throttle paths. Any different Docker configuration fails
rather than silently accepting a weaker policy. The parent and candidate also
verify actual kernel Seccomp=2. The built image and effective container ENV must
match the inspected immutable Python base digest exactly, and WorkingDir must be
`/`; the resulting image-bound inventory is saved and hashed in the proof.

## Output protocol requirements for future integration

The trusted root-only receipt proves no candidate descendants remain at that
instant, not output validity or a financial score. The host must bind it to the
exact retained container identity, image, attempt, generation and absolute
deadline, prohibit peer exec, bounded-copy only allowlisted output after receipt,
validate every byte in a separate parser, then positively stop the whole unit.
Receipt absence, deadline races, partial extraction or unknown stop are failures;
never use a candidate-writable host output bind as a substitute for bounded tmpfs.
Production inspect inventory, retained input/mount authority, durable process
registry and accounting, recovery, and typed artifact publication remain absent.
The proof projection verifier is not an admission capability.

## Explicit hosted proof (not executed locally)

```sh
docker build --iidfile /tmp/deadline.iid -f runtimes/hostile_deadline/Dockerfile .
python -m runtimes.hostile_deadline.acceptance --run-hostile-docker-tests \
  --image "$(cat /tmp/deadline.iid)" --output /tmp/deadline-proof.json
```

The maintained malicious fixture checks denied UID/capability escalation,
SIGSTOP/SIGKILL/SIGTERM and ptrace/memory/FD access against PID1, then leaves
session-escaping grandchildren. The host kills both the disposable launch owner
and attach client, observes namespace PIDs, and checks their eventual death while
an unrelated host sibling survives. This proof requires the local default Docker
Unix socket: remote contexts or DOCKER_HOST overrides are rejected. Every observed
host PID must have a readable, non-null start-time identity; the inspected
State.Pid must be among them and its local NSpid chain must end at private PID1.
Missing observations are failures, never silently excluded from the stop claim. A second case checks early completion,
root-only quiescence, no remaining descendants, and retained output extraction
before the unchanged deadline. The proof requires a fixed completed-attack marker written after all adversarial
assertions; bootstrap failure or missing marker cannot pass. The Docker inspect
contract is checked against the reviewed base-image configuration and pinned host
policy. Positive stop must be observed within the **original** eight-second
absolute deadline plus one explicit second of scheduling/teardown tolerance,
including launch, readiness, extraction and polling time. No fresh relative wait
can extend this acceptance window; actual elapsed time is saved. The workflow
uploads only bounded proof JSON (including public image ENV and policy hashes)
and the local image ID, not arbitrary candidate output or logs.
Unexecuted scripts are not evidence these kernel/container properties passed.

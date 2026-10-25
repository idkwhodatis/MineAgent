# Disabled hostile deadline proof — protected output ABI v2

Not launch authority, production activation, or an acceptance claim. The v1
Docker `cp` extraction assumption was invalid for tmpfs. V2 deliberately replaces
it with a protected stdout channel; no host-root read, namespace injection,
`docker exec`, extra container capabilities, daemon setting, or provider is used.

## Preserved isolation

Pinned Python and Moby seccomp bytes, private PID namespace, readonly rootfs,
network/IPC none, no-new-privileges, 256MiB memory+swap, 16 processes, and one CPU
remain unchanged. Root PID1 starts with only KILL/SETUID/SETGID, is nondumpable,
and retains only KILL after its trusted forks. Candidate IDs permanently drop to
65533, groups clear, active/ambient/inheritable capabilities become zero; its
stdio is `/dev/null`, other inherited FDs close, and only fixed Python `-I`
executes `/input/candidate.py`. Candidate code cannot write the protected channel
or signal/read trusted-root helpers. No application launcher admits this profile.

PID1 fixes the original monotonic deadline from the absolute host timestamp,
never reads candidate output, and never writes or waits on the output channel.
Direct-child exit triggers kill(-1)/reaping until ECHILD; observer descendants
are included. Only after ECHILD does PID1 fork an exporter. PID1 then holds until
the original deadline without waiting for exporter completion. Kernel PID1 exit
kills the remaining namespace, including a blocked exporter. The host requires
positive stopped state and all observed identities gone; time elapsed alone is
not a stop receipt or authority to refund reservations.

## Explicit v2 data ABI

`/output` is root-owned mode0733: the candidate can create files but cannot chmod
the directory. Fixed outputs must be world-readable regular single-link inodes
owned by65533. Inaccessible0600 files are rejected; no DAC capability is added.
Helpers traverse an exact directory and basename using NOFOLLOW, open files
NONBLOCK, reject FIFO/device/directory/symlink/hardlink/oversize, cap reads at4MiB
plus one overflow byte, and require exact size/EOF and stable pre/post stat.
Opaque bytes are never interpreted as HDF, pickle, code, or native-format data.

Both trusted helpers drop all active capabilities, remain nondumpable root, and
close non-stdio FDs. The fixture-only live observer reads only the fixed marker;
the maintained attack publishes it by atomic rename after protection assertions
and forks. Observer emits a predetermined marker frame, **not** quiescence or
artifact authority. Post-ECHILD exporter emits trusted completion metadata plus
bounded opaque `result.bin`, or an empty rejection frame. Helpers may block on
stdout; PID1 cannot. They are not additional candidate processes or deadline
owners.

The container is created with log-driver none, TTYfalse and OpenStdinfalse. A
bounded Unix-socket Docker attach101 handshake establishes the subscription
before a separate owner starts the container. The owner and an extra attach
client are killed. Candidate stdout remains redirected; only trusted code owns
the proof channel. Docker multiplex frames and protected frames have separate
strict length/type/aggregate limits. The host validates complete evidence while
the original unit is still running and before its original deadline, then drains
EOF and verifies stop within the existing fixed1s tolerance. This tolerance does
not extend candidate/exporter lifetime. Missing/truncated/duplicate/trailing
frames cannot pass.

## Required hosted cases

Live hostile marker + four live unprivileged fixture processes; early ECHILD
receipt and retained output; non-reading primary collector with an observed
**non-zombie** exporter then whole-unit stop; killed collector; and explicit
oversized/FIFO/symlink/0600-output rejection. The no-reader evidence establishes
live exporter survival with an unread stream and deadline termination, not a
kernel-wchan proof of a particular blocked syscall. Pure tests are not hosted
containment acceptance.

Sources: [Docker cp tmpfs limitation](https://docs.docker.com/reference/cli/docker/container/cp/),
[Moby attach subscription before upgrade](https://github.com/moby/moby/blob/v28.3.2/daemon/attach.go),
[PID namespace init semantics](https://man7.org/linux/man-pages/man7/pid_namespaces.7.html).


### Unread-reader terminal observation order

For the deliberate no-reader case, Docker metadata may wait for daemon stream
finalization. The host first observes every captured exact PID/start identity as
absent/replaced or terminal Z/X (kernel exit, not yet reaping), without inspecting
Docker or reading/closing the attach stream. Only then it closes that unread
stream to release daemon plumbing. Docker stopped/exit124 and disappearance of
all original identities are still mandatory within the **same original deadline
plus fixed tolerance**. Every in-attempt Docker command uses only the remaining
absolute observation budget; no extra30s metadata wait is allowed. Case/phase
progress records contain only maintained case names, never candidate bytes.

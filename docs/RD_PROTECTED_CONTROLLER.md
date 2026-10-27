# Protected RD controller — revision 020

This is a persistence/runtime integration prerequisite, not completed RD engine
acceptance. The default submit/API/dispatcher remains closed. No paid provider,
legacy request codec relaxation, or legacy strategy identity substitution is added.

## Fixed process and authority

`mineagent.rdagent-orchestrator.v2` uses a root protected PID1 with only KILL after
bootstrap and one fixed UID/GID65533 Python controller child. The child has zero
capabilities, no-new-privileges and the fixed seccomp policy. Its original maximum
60-second deadline is not extended by RPC, callback, output or Docker failures.
Both child hard file limit262144 and nofile256 match the immutable Docker profile.
Only `/input/bootstrap.json` and scoped read-only `/ipc` are mounted. The host
operation directory is0700; the inner socket mode0666 is not authorization:
every request requires exact ticket plus SO_PEERCRED and current leader/child
PID-start/cgroup/namespace identity bound to immutable persisted receipts.

PID1 never parses or emits output. Separate zero-capability helpers report the
child identity and post-ECHILD quiescence. Complete bounded attached-stream EOF is
required; trailing/duplicate frames fail. No-reader containment observes exact
kernel exit before closing the reader and then confirms Docker stopped. No
unknown create/start/stop is silently retried or converted into fabricated stop.
A retained real stop may replay only its journal/fixed cleanup/DB callback.

## Versioned persistence and cold closure

020 adds the closed controller profile and controller_child receipt, without
replacing revision015 validators. It separately versions the018 RD profile
predicate and enables only authenticated first-generation observations/returned
boundaries. Final generation, legacy valid evaluation, checkpoint and generic
candidate-input phase gates remain closed. New evaluation snapshots are021 work.

Research closurev5 pairs exactly with RD source closurev2. Actual retained raw
launch/identity/child/stop bytes are independently inventoried and reread; an IPC
socket, ticket or incomplete external process cannot enter a cold backup.
Missing-child startup is a failed/cancelled contained unit, never source/evaluation
success. Positive stop permits full conservative_unknown charge only; no invented
measured usage or refund. Actor/source/model-call/boundary links are rechecked on
cold restore in addition to shared accounting and source byte/parser closure.

Historical018 static SQL remains SHA256
`db70246b55d6f4c95386d45be036fa1b1e019934405cfdaef3910f20252a5408`.
Its regression projects out exactly the one020 boundary unique constraint, then
requires byte-for-byte equality of the entire historical composition. The source
fragment current-model pin is reviewed separately; its rendered SQL is unchanged.
The historical generator refuses successor metadata rather than rewriting018.

## What is and is not verified

Local tests cover real bounded socketpair framing, kernel read grammar, actual
retained bytes, strict typed codecs, fixed Docker-inspect expectations, authority
and lifecycle doubles, migration literals and cold graph negatives. PostgreSQL
negative cases are collected but require hosted execution. Docker is unavailable
locally: the new protected controller profile has not yet been run in Docker.
Previously accepted hostile/numeric profiles do not constitute this profile's
acceptance. Operational wheel import checks do not constitute container proof.

Remaining functional chain: real host controller issuer and fixed RPC handler,
actual fixture broker→retained source→complete single declared cutoff→native HDF→
fresh normalizer primitive report→021 execution snapshot→truthful feedback→actual
CoSTEER adoption/finalization, followed by positive whole-controller stop and cold
restore. Initial maintained single-output-day evaluation retains two prior plus
current input sessions and must cover its whole declared window. General data
providers, multiple generations and resumed/RAG source reuse are not implied.

Local freeze checkpoint: 1,809 distinct RD/research-runtime/operations tests pass;
151 PostgreSQL cases collect (149 predecessor +2 new020 bypass negatives), not
executed locally. Operational wheel builds twice identically with SHA256
`8feb79cb461e8ccb1a67116cf373f9e89ebca13a25a859f11bf03b53ca74759e`.
Installed-only `python -I` imports of the actual upstream adapter, operational
entrypoint/client/deadline pass with the checkout absent from sys.path. This is
not execution of the protected controller, RPC ledger or full CoSTEER loop.

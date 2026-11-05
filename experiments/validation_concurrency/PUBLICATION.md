# Harness publication checkpoint, 2026-11-05

The 15 files prepared on 2026-11-04 are preserved byte for byte from
`MineAgent-validation-harness-continuation-20261104.zip` (SHA-256
`5abd7b9afc9672c19e469ee921fab1c17cf1f00484a4c367da5ac30cc2d2449f`).
Their recorded results, source hashes and preparation-time statements are
historical snapshots. This note records the later publication checkpoint.

All 90 offline tests were rerun successfully in normal Python and with
`python -O` immediately before publication. The published component algorithm
and synthetic SQL model remain unchanged from baseline `c993367`.

Since the preparation snapshot, a separately authorized private Unix-socket
check was attempted. Socket creation still failed with `PermissionError`,
errno 1 (`Operation not permitted`). PostgreSQL did not start, and none of the
18 planned live database case groups ran. No TCP fallback or security-setting
change was attempted. Statements in README.md, CONTINUATION.md and RESULTS.json
about no socket retry or no publication describe the earlier preparation run,
not this later checkpoint.

This update is isolated to `experiments/validation_concurrency/` on the existing
experimental branch. It does not establish production concurrency correctness,
scoped SQL coordination, commit-time validation gate retention, deferred-event
cancellation, or performance. Passing offline tests is not live PostgreSQL proof.

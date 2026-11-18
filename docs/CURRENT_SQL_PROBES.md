# Current-head isolated SQL probes

These probes are not migrations and are not imported by application startup,
workers or native runtimes. The installed schema remains 032g. The existing
isolated-candidate CI now runs an additional exact named `current` inventory:
14 source contracts and 32 PostgreSQL cases, with no failures, errors or skips
accepted. Original source, keyset and row groups remain unchanged.

The RD probe adds one freshly scanned workflow-v3 config CHECK and substitutes
only three matching same-row pure predicates. Other versions, payload checks,
relational queries, owner identities and commit boundaries remain intact.
The OE probe freshly scans fourteen CHECKs before substituting pure predicates
in seven OE graph readers. Restoration puts original readers back before
removing the new checks. Guard/catalog tests reject stale prerequisites.

## Local evidence

On an isolated PostgreSQL 18.0 built from checksum-verified official source:
- All 46 current cases passed together.
- Original source inventory: 69 passed.
- Existing 032g migration regression: 18 passed. Eleven GitHub-owned Docker
  admin/LATIN1 tests were intentionally not run locally; their environment
  identity guards were not bypassed.
- A real API-admitted initial queued RD child had identical reader outcomes
  before, during and after the probe, with exact catalog restoration. Five
  sample medians were 46.3/27.5/49.8 ms per full transaction and
  39.8/19.0/41.2 ms per commit (baseline/candidate/restored).
- One OE synthetic component experiment reduced repeated-read median from
  338.4 to 2.36 ms but increased write median from 7.64 to 53.70 ms. The later
  combined run also observed this tradeoff. These are component measurements,
  not end-to-end throughput claims or timing acceptance thresholds.

## Remaining boundaries

The RD populated graph contains no model operations or process admissions and
is not the seeded second-child graph. OE content fixtures do not constitute a
completed physical graph. Neither probe establishes receipt latency, native
SDK deadlines, containment recovery, or full workflow/browser/cold restore.
The local rollback/savepoint/catalog checks are not native crash-recovery
acceptance. Existing component preflights are not a complete production
migration catalog/security review. Production adoption requires that review,
seeded/populated graph and concurrent-write adversaries, and unchanged native
workflow/recovery gates. Do not promote these probes merely because CI passes.

## CI storage envelope

The expanded current-head inventory exhausted the old 1 GiB PostgreSQL tmpfs
in CI before completing the OE cases (DiskFull followed by recovery errors).
The isolated job now uses a 2 GiB tmpfs within a 3 GiB memory limit and the
existing CI checkpoint settings: max_wal_size 256 MB, min_wal_size 80 MB and
checkpoint_timeout 60 seconds. WAL limits are checkpoint targets, not hard
storage caps. Normal fsync, synchronous_commit and full_page_writes remain
enabled and their effective settings are recorded. Identity-checked teardown
also records bounded filesystem capacity and container OOM/exit status without
printing database contents, environment variables or credentials. Statement/lock deadlines,
proof inventories and exact-container teardown are unchanged. This resource
change requires a fresh full CI run; it is not evidence of candidate acceptance.

Current-head component measurement artifacts use a `current-` filename prefix.
The original row proof retains its existing filenames, and both groups retain
exclusive creation so duplicate evidence in one group still fails. This avoids
collision when the same reviewed benchmark is invoked against both schemas.

## Per-event OE trigger dispatch experiment (not activated)

`oe_trigger_target_probe_sql.py` is a separate, opt-in experiment against exact
032g. It changes only `mineagent_oes_consistent`: after a target's graph check
succeeds, another occurrence of the same target in that **one trigger invocation**
does not repeat the graph call. Both OLD and NEW reverse queries, reverse bounds,
membership checks, READ COMMITTED checks and NOWAIT run locks still execute in
the original order. There is no transaction- or session-wide cache. Unknown/NULL
membership is not treated as a cache hit. Rollback restores the original body.

The independently named `trigger` CI group contains six source contracts and
27 PostgreSQL cases. The latter include installed-catalog roundtrip/preflight
checks and baseline/candidate/restored **synthetic dispatch** cases: target
routing, rejection/savepoint rollback, per-event scope, isolation/reverse bounds,
and contention between two database connections. The synthetic checker records
calls instead of validating the real graph; it is installed only in a separate
empty test schema. It is not a production substitute or populated-graph proof.
The existing source/keyset/rows/current named inventories remain unchanged.

This experiment is motivated by structured-native logs where repeated deferred
graph validation dominates durable receipt commits. No measured end-to-end
speedup, Qlib fix, provider-deadline fix, or native acceptance is claimed here.
Before any runtime migration, prove equivalence on populated actual RD/OE
workflows, concurrent graph-changing transactions (including reverse-source
changes), crash/recovery and unchanged native gates. The original deadline,
receipt boundaries, installed schema requirement and all acceptance gates remain
unchanged. A passing dispatch group alone cannot authorize promotion.

Hosted dispatch evidence: commit `6d418ac7a7c436a70c95aae233b2dabe34d06107`,
[isolated proof run 44244165171](https://github.com/idkwhodatis/MineAgent/actions/runs/44244165171),
passed all 33 trigger-group cases on PostgreSQL 18.6, with no skips. The unchanged
69-source, 11-keyset, 34-row and 46-current groups also passed. This does not
change the limitations or authorize runtime activation described above.

The separate `MineAgent independent populated workflow baseline` workflow runs
that existing actual finite-workflow/complete-restore test without waiting for
the earlier normal-demo gate. It retains the original setup, test, deadlines,
containment and sanitized-artifact steps, plus a source-equality guard. It does
not activate any SQL experiment, alter the original acceptance workflow, or
claim full acceptance. Its narrow path filters serve this diagnostic baseline,
not comprehensive runtime regression coverage. Actual completed-graph evidence
is pending its result.

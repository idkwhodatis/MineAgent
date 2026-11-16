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

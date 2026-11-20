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
not comprehensive runtime regression coverage. The first independent baseline on `5e49c8c` reached the actual native test,
then failed before the first RD stage reached `waiting_release`
([run 44244844374](https://github.com/idkwhodatis/MineAgent/actions/runs/44244844374)).
OE and complete restore were not reached; no completed graph was established.
Containment and cleanup passed. This result does not identify the queue-failure
cause, so bounded existing RD queue/RPC/state diagnostics are added around only
the first drain without changing its acceptance assertions or execution policy.

The historical diagnostic rerun on `d6481a1`
([run 44246639295](https://github.com/idkwhodatis/MineAgent/actions/runs/44246639295))
localized failure to the third RD `submit_chat`: durable commits consumed most
of its remaining controller budget, and a provider exception retained a domain
exception from the transaction boundary. The then-closed vocabulary reported
these codes as unknown; it did not prove a specific fence/deadline error. Five
fixed existing provider/fence codes are now allowlisted, never arbitrary text.

An additional current-032g native baseline uses the same pre-admission control
history and actual bridge against a separate fresh database. It runs even if the
historical gate fails, but only after every setup step and all six original
process-absence checks succeed. It does not activate an SQL candidate. Only
successful real RD/OE completion permits its read-only post-run graph checks.
The historical gate remains mandatory/red on failure, and current graph success
would not replace its historical restore proof or establish an optimization's
performance or concurrency safety.

On `33b816c`, both historical031 and current032g baselines failed before the
first RD child reached release; no OE/completed-graph postcheck ran
([run 44248740303](https://github.com/idkwhodatis/MineAgent/actions/runs/44248740303)).
The closed error chain was `provider_stop_receipt_unconfirmed` →
`provider_identity_receipt_unconfirmed` → `attempt_fenced`. Current032g's fourth
`submit_chat` exhausted its remaining controller budget during durable commits.
This establishes the failing boundary, not which fence condition first failed.
All process-absence checks and final cleanup passed. Test-only lease observation
and read-only installed trigger inventory are the next evidence steps; neither
changes lease policy, deadlines, graph predicates or runtime schema.

`heartbeat_inventory` is a separate six-case group: five source checks and one
read-only installed-catalog observation on disposable PostgreSQL 18.6/032g.
It records all attempt triggers (including internal triggers), function hashes
and bounded lexical observations, including row-lock/whole-row/dynamic-SQL flags.
It explicitly does **not** establish dependency closure, frozen-source manifest
agreement, timestamp-update equivalence, or concurrency safety. No function body,
application data or SQL text is exported. Existing candidate inventories remain
unchanged. The native lease observer likewise preserves original pulse behavior;
its successful-return count may include throttle no-ops, not durable renewals,
and sampling is explicitly best-effort. It emits only after the worker scope.

Hosted run [44251736436](https://github.com/idkwhodatis/MineAgent/actions/runs/44251736436)
passed the six inventory cases and all prior groups for `10b29f7`. Its source
artifact records merge commit `dfcef880` and tree
`3faa895b4dd354e21d626ea08532164a6dffe7a2`, matching the branch tree. The actual
inventory contains eight user attempt triggers, 186 internal FK triggers and247
local functions. The baseline lease diagnostics on the same branch showed the
first pulse failing with a database error, zero successful returns and lost
signals set; the then-closed SQLSTATE vocabulary did not identify that error.
Only fixed `P0001` and two exact public workflow-lock error tags are added next.

The opt-in completed provider-v2 graph hook runs **after** every original native
parent/seeded-child assertion. It upgrades that owned quiescent028 database to
032g, checks genuine completed provider graphs, and rolls back timestamp,
identity-rejection and two-connection lock probes. Every installed guard stays
active; the original24-case gate, selection and deadlines are unchanged. A fixed
proof document is required only after all probes preserve the catalog and rows.
This terminal-v2 evidence is not active-lease, workflow-v3, OE or optimization
acceptance. Native execution of the new hook remains pending.

### Independent heartbeat lock-order diagnostic

The independent populated-workflow job also declares a separate two-case
`heartbeat_order` group for exact031 and032g. It creates and claims a genuine
finite-workflow RD child through the existing API/worker admission helpers, but
never dispatches a controller, creates physical admissions, or calls an SDK.
Two real connections exercise the crossed owner and Run/Attempt lock order and
require the original deferred `workflow owner is busy` rejection, alongside
uncontended controls and the unchanged fail-closed `AttemptLease.pulse` behavior.
All adversarial writes roll back and private row/catalog fingerprints must remain
unchanged. This is metadata/concurrency diagnosis, not completed graph acceptance,
a runtime fix, or evidence that any deadline may be relaxed. Hosted execution is
required before reporting the diagnostic as proved.

The first hosted `heartbeat_order` run (44256242127 at4292173) passed both
revision cases. Its unchanged native baselines also demonstrated the opposite
contention direction: successful heartbeat returns alongside a main
`neutral_busy` rejection before any model operation. Earlier0d7796f baseline
44254386892 recorded first-pulse `P0001`/`workflow_owner_busy` on both revisions.
These observations establish the contention problem, not a safe runtime fix.
The genuine completed-provider-v2 hook passed within the unchanged24-case
Qlib gate in44254387009; its later workflow-v3 gate still failed.

A separate16-case `heartbeat_admission` group now exercises a TEST-ONLY local
FIFO admission prototype around complete original transactions. It uses a
fixed conservative scheduling cutoff derived from the existing observed lease
expiry, never refreshed authority. Cases cover both acquisition directions,
external contention from an exact owned spawned process, and expiry/token
changes while waiting. All original SQL guards, live fences, pulse revocation,
and deadlines remain; no native worker imports or activates this prototype.
This does not establish full native callback integration or an operational fix.

Installed-catalog evidence v2 additionally records bounded noninternal trigger
bindings, enabled/event/update-column metadata, table constraints and column
ordinals across the owned schema. Definitions and predicates remain hashes,
never raw SQL or application rows. Existing six inventory cases are preserved.
This enables a coverage audit; catalog metadata alone is not equivalence proof.

Hosted3bd41fa baseline44259341284 passed all16 isolated admission cases, with
original two lock-order cases also green. This establishes the stated metadata
schedules, including separate-process contention, not native integration.
A separate8-case `heartbeat_observation` group checks a scheduling-only observer:
one READ COMMITTED/read-only exact-fence observation using database time and a
pre-query monotonic sample; later tickets may see committed renewal but an old
ticket never refreshes. Tests retain original heartbeat/deferred checks, verify
uncommitted/rolled-back renewal invisibility, token/cancellation rejection and
NullPool physical connection closure. They do not prove pooled-connection reset,
complete callback participation, or native execution/cleanup phase integration.

The first catalog-v2 extraction hit its closed4096-row schema bound. Only the
supplement now allows8192 rows per category with an8193 sentinel; the original
attempt/function4096 limits remain. The final metadata export remains capped at
8MB, with fixed-label numeric counts on overflow and no silent truncation. Mixed
OLD/NEW trigger predicates use explicitly labeled full-trigger-definition hashes
because PostgreSQL cannot deparse them with pg_get_expr. Native runtime behavior
and acceptance thresholds are unaffected by these evidence-tool corrections.

Hostedc823214 baseline44261181034 passed all8 committed-observation cases and
all16 admission cases; the unchanged historical/current native baselines still
failed and remain required. The next separate4-case heartbeat-role group preserves
original cancel_requested/pause_requested pulse semantics (cancel flag without
lost authority), while neutral observation remains strict and role-bound.

The isolated one-case native scheduling trial invokes the unchanged complete
current032g baseline, including its authenticated controls, actual RD/OE bridge
and final graph assertions. Test-only wrappers coordinate only exact factories
and full fences claimed within that scope, enclosing original SQL transactions
and pulses through commit without spanning external I/O. It runs alone in a
fresh pytest process only after the16/8/4 metadata prerequisites and repeated
six-unit absence checks pass. Original native baselines and all final containment
checks remain separate and unchanged. Live lease threads must stop before patches
restore; failure is red, not permission to continue. No production module imports
or enables this candidate, no SQL guard is removed and no deadline is replaced.

A separate7-case `reverse_heartbeat` experiment installs a single guarded reverse
function ONLY in an owned disposable current032g database, then restores it. It
pins60 installed functions and the selected64-relation catalog (353 triggers,
1692 constraints,826 columns) to the verified v2 artifact. Only exact
execution_attempts UPDATE events with whole OLD/NEW rows equal after removing
the two lease timestamps bypass that one reverse validator. This deliberately
changes owner-contention outcomes; it is NOT original-outcome equivalence or a
production optimization. Cases exercise original/candidate/restored contention,
non-timestamp token/status events, mutate/revert, immediate constraints, savepoint
rollback and original FK rejection. Normalization preserves unrelated identifiers;
full function-definition verification retains all header attributes. Hosted
execution is still required, and these metadata cases do not replace populated
native, multiprocess or complete dependency-equivalence proof.

Native trial counters now report fixed bounded aggregates even when the original
baseline fails. Reporting cannot replace the original failure, and successful
pulse returns are explicitly not claimed as durable renewals.

# PostgreSQL proof preparation, 2026-11-04

Baseline: published isolated prototype c993367. This continuation preserves the
published component algorithm and global SQL model and improves the experiment
harness. No runtime or production acceptance has occurred.

## False-positive paths removed

1. 55P03 from lock timeout no longer qualifies as writer admission rejection.
   The marker must also be exactly model_writer_busy. Raw database messages are
   never included in result records.
2. A transaction-exit error cannot satisfy an expected statement error. The
   immediate probe catches the rejection at SET CONSTRAINTS inside a savepoint,
   then repairs the earlier pending graph and observes the final committed state.
3. A statement error cannot satisfy a deferred commit probe. Every body statement
   must succeed. IDLE pre/postconditions prevent nested savepoint release or an
   incompletely exited transaction from masquerading as a rejected COMMIT.
4. The immediate/deferred invalidation case now queues valid work first, checks
   it immediately, returns to deferred, creates new invalid work and requires
   rejection at commit. Changing an otherwise unchecked owner field alone was
   not adequate evidence.
5. The savepoint case no longer claims observation of cancelled deferred events.
   It checks restored rows and post-savepoint gate release. A complementary case
   retains pre-savepoint gate ownership and pending invalid work.
6. Rejected transactions are followed by exact synthetic graph comparisons.
   Successful repair/delete/recreate paths remain included to reject overblocking
   implementations that fail every write.

## Error and lifecycle evidence

proof_assertions.py separates statement/commit phases and known synthetic
markers. Failure records retain only bounded categories, stages and SQLSTATEs.
Rollback, context-entry, missing-error and context-suppression failures cannot
be reported as successful proof. The marker itself is never taken as authority
over model validation; it only identifies the observed rejection path.

test_proof_assertions.py uses error and transaction-context doubles. It proves
the Python helper refuses selected false-positive transcripts, not that the
server emits those transcripts or obeys the intended locking protocol.

test_runner_lifecycle.py uses explicit connection doubles for setup failure,
same-backend rejection, uncertain first statement/creation, case failure, cleanup
failure, absence verification and connection-close failure. An unconfirmed create
does not trigger a speculative DROP. Successful mocked orchestration is expressly
not a live database result.

## Remaining real-session gates

- Execute the synthetic global reference on a permitted PostgreSQL installation
  with independent sessions and bounded commands, preserving the exact report.
- Add a bounded barrier inside commit-time validation to observe that another
  writer is rejected while validation is actually executing, not merely while
  the transaction is open. This is not implemented or inferred from source.
- If deferred event cancellation itself is required, use separately reviewed,
  transactional test observations that distinguish cancelled events from
  surviving events which simply revalidate an already-restored graph.
- Only then design/execute the actual scoped SQL layer and production dependency
  graph. The abstract fatal restart latch and preheld-lock predicate are still
  assumptions without an enforced database implementation.
- Preserve existing OE reverse owner/workflow locks. Different RD components can
  still contend on these retained locks. No same-owner throughput benefit follows
  from the offline model.
- Run paired populated original/candidate measurements only after correctness
  gates. No timeout increases, receipt fusion, suppressed validations or changed
  charges are part of this continuation.

The current executable environment has no verified PostgreSQL server/client or
psycopg installation. Prior socket restrictions remain unresolved. No server,
socket, installation, security-setting change or alternate route was attempted.
Obtaining a permitted runtime is an explicit next step, not something these
offline checks can substitute for.

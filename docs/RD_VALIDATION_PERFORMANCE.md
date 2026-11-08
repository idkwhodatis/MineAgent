# RD validation cost (revisions 032d and 032e)

## Observed failure and limited remedy

The configured workflow at `2c782f0f6504e3e2d1cee40e806da5e37333b9c9`
failed two actual workflow cases in
[run 42355976927, job 128354040422](https://github.com/idkwhodatis/MineAgent/actions/runs/42355976927/job/128354040422).
The failures reached the existing deadline at generation-boundary observation
and embedding submission. Recorded PostgreSQL diagnostics showed 6.2–6.5 million
canonical serializer calls and approximately 24–25 seconds of serializer self
time per case. Schema interpretation separately consumed approximately 18 seconds
of self time. Submission validation's approximately 39 seconds of inclusive time
overlaps these callees and must not be added to their self time.

Revision `032d_rd_validation_cost` reduces recursive PL/pgSQL calls for container
keys and scalar children. It preserves the original Unicode/DEL path, UTF-8
requirement, integer grammar, key ordering, depth checks and all input, child,
key and aggregate byte bounds. The function remains `IMMUTABLE STRICT` with
`search_path=pg_catalog`. No result is cached, and every caller still performs
its original validation. No schema interpreter, graph predicate, trigger, CHECK
constraint, deadline or process profile is changed.

The append-only migration takes the existing maintenance advisory lock and
nonblocking exclusive locks on all 187 application tables. It requires quiescent
READ COMMITTED maintenance. Downgrade restores the exact frozen predecessor
body. Historical migration files are unchanged. Closed revision allowlists,
backup capability declarations and generated API types explicitly admit 032d.

## PostgreSQL evidence, 2026-11-07

Bounded probes used PostgreSQL 18.6 (Debian 18.6-1.pgdg13+2), a cached official
image, disposable loopback-only databases, and rollback-only transactions. The
original checkout remained at the exact commit above. Independent predecessor
recursive functions were used for differential comparison.

Three canonical batches used eight varying fixture configurations from table
columns and 128 calls per batch. Old/candidate times were 178.967/87.474 ms,
159.472/68.354 ms and 152.722/68.450 ms: 51–57% faster in this small workload.
Recursive canonical calls fell from 35,904 to 3,648 per batch. This serializer
body is identical to the final 032d body; its SQL SHA-256 is
`3b511de1b313f8f043ad2b1d7b651486e78224e6cccca17f2d160766681e1a63`.

Those initial probes also included an experimental schema-wrapper split. Schema
interpretation was 2.6–6.4% slower, so that experiment was removed. Its combined
workflow-config timings are not evidence for the final canonical-only patch.
The initial 3,994 comparisons had zero differences, including 3,640 canonical
cases, 249 schema cases and 105 config-predicate cases.

The permanent mandatory PostgreSQL gate separately exercises the 1,639-value
escaped corpus in both ASCII modes, exact UTF-8 output, rejected numeric tokens,
SQL NULL arguments, byte/depth boundaries, schema recovery and full generated
config schemas. An actual Alembic 032d → 032c → 032d roundtrip checks exact
canonical bodies, unchanged other function definitions and CHECK constraints,
the final revision, and transactional rollback. Five tests passed in 7.69 seconds
with no failures, errors or skips on the final canonical-only SQL. The final test
file SHA-256 is `e8e14b0830e7e976711ad60b934fb6aa8814deaafbd9c61f2e8b2e0357a215dc`.
The optional timing test is excluded from that gate. Test schemas, disposable
containers and volumes were removed after the probe.

## Follow-up revision 032e

At `7ddf621ec4ce64c8229f7b31c0c2ab8317a8fe6d`, the configured workflow
[again reached its original controller deadline](https://github.com/idkwhodatis/MineAgent/actions/runs/42425613267/job/128563124900).
Schema interpretation consumed approximately 20–22 seconds of self time per
case. Structured OE separately exhausted its eight-second SDK admission budget
before sending request bytes: its intent receipt took 4.2 seconds and its started
boundary took 8.2 seconds, including a 4.2-second started receipt, in
[the normal-flow job](https://github.com/idkwhodatis/MineAgent/actions/runs/42425613262/job/128563124607).

Revision `032e_rd_workflow_cost` replaces the schema interpreter's per-character
SQL scan with an equivalent C-collated control-character regex and inlines the
same scalar validation grammar for ordinary object/array children. References,
unions, containers and special model validators retain recursive validation.
It also computes identical workflow configuration and input hashes once within
each invocation, after the original configuration validation. OE request and
controller receipt canonical text is reused only within the same invocation or
receipt iteration. No result is cached across calls.
Every predicate, original deadline, reservation and physical receipt remains in
place. The additive migration restores the exact predecessor function bodies on
downgrade and leaves historical migrations unchanged.

New mandatory differential cases cover control characters, DEL, Unicode, long
text, malformed rules, SQL NULL, real configurations and negative mutations. A
032e → 032d → 032e roundtrip checks unchanged unrelated functions and constraints.
These tests do not by themselves establish that either actual runtime now meets
its deadline.

### PostgreSQL evidence, 2026-11-08

The final composed migration passed all nine mandatory PostgreSQL 18.6 tests in
12.53 seconds, with zero failures or skips; only the two diagnostic timing tests
were deselected. Both historical and current migration roundtrips passed. All
four replaced function bodies and unrelated functions/CHECK constraints were
verified. Six unit/reversibility tests and an additional 360 independently
expected scalar cases passed (168 accepted, 192 rejected).

Three nonconstant batches of 12 table-backed values showed consistent reductions
in schema-dependent work. Median baseline/candidate execution times were
99.409/59.147 ms for workflow schema checks, 144.766/90.819 ms for complete
workflow configuration checks, and 42.867/24.453 ms for OE configuration checks:
37–43% less elapsed time. Schema calls fell from 2,604 to 912, 4,224 to 1,464,
and 1,488 to 432 respectively. Every input was checked for acceptance before
timing. Raw commitments and canonical serialization showed no consistent
improvement and their call counts remained unchanged.

The composed 120,341-byte migration SHA-256 is
`4b1de91e86edd98dcd8d636bdded1a09c867e55a413fdc97bb22e1afa1dcd14d`.
The schema body measured before composition is byte-identical to the final
body. These probes do not measure whole receipt transactions, relational graph
execution, or the runtime benefit of OE text reuse. The eight-second admission
deadline and full RD/OE runtime acceptance still require the exact-head CI run.
Disposable databases/containers were removed after validation.

## Reproduction and limits

With repository dependencies and compatibility assets materialized, point
`TEST_DATABASE_URL` at a disposable PostgreSQL database. Tests create and remove
unique schemas; never use a production database for this probe.

```sh
python -m pytest tests/integration/test_rdagent_validation_performance.py \
  tests/integration/test_rdagent_workflow_cost.py \
  -k 'not nonconstant_server_loop_probe and not text_loop_probe' -q
```

The configured-analysis workflow requires exactly nine passing mandatory cases:
the five original 032d cases and four new 032e cases.
For a separate nonconstant serializer diagnostic, without timing assertions:

```sh
MINEAGENT_SQL_PERF_PROBE=1 python -m pytest \
  tests/integration/test_rdagent_validation_performance.py \
  -k nonconstant_server_loop_probe -q -s
```

This optional synthetic workload is distinct from the recorded configuration
batches. Neither microbenchmark proves end-to-end deadline success. The full
native/browser/Qlib workflow has not been rerun for this patch, and its exact-head
hosted acceptance remains required. No production throughput or SLO is claimed.

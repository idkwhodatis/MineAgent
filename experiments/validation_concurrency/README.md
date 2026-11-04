# Isolated validation-concurrency prototype

**Reconstructed experimental work. Not production integration or PostgreSQL proof.**

The original workspace and ZIP were unavailable during recovery on 2026-11-03.
This directory was rebuilt from retained source and design context and tested
again. It is not claimed byte-identical or behaviorally equivalent to the lost
artifact. See [RECONSTRUCTION.md](RECONSTRUCTION.md).

## Contents

- `component_model.py`: executable abstract parent/workflow/receipt graph,
  dependency closure, coordination, generation validation and savepoints.
- `test_component_model.py`: 39 offline model tests, including 34,496 finite
  routing comparisons and 2,904 two-transaction parameter cases. Parameter cases
  are not unique interleavings; the outcome oracle shares predicate helpers.
- `model.sql` and `run_two_sessions.py`: reconstructed **unexecuted** synthetic
  PostgreSQL global-gate reference. No actual RD predicates or scoped SQL mapping.
- `test_source_model.py`: 17 offline source/adapter checks; errors are doubles.
- `DESIGN.md`: assumptions, dependency routing, retained locks and unsolved gates.
- `RESULTS.json` and `offline-tests.log`: fresh results for these reconstructed
  files, with hashes. Historical test runs are not reused as current evidence.

## Run offline tests

From this directory, using Python 3.10 or newer and the standard library:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s . -p 'test_*.py' -v
```

No installation, network, database, server or socket is required. Fresh result:
**56 tests passed**, with no failures or skips. This is not the product test suite.

## Runtime boundary

Do not execute the SQL runner without a separately permitted disposable local
multi-session PostgreSQL environment. The technical opt-in flag and explicit DSN
are not permission to bypass a denied socket route. Prior socket creation was
denied; this reconstruction makes no server/listener attempt or configuration
change. No credentials are bundled or discovered. The runner never starts a
server and must not target production.

The runner requires explicit local socket directory, port, database and user.
It rejects inherited nonempty `PG*` environment settings and DSN service/hostaddr
or other unapproved parameters so connection routing cannot silently change.
Its connection timeout is bounded to 1–5 seconds. Visibility checks use explicit
exceptions and remain present under Python optimization.

The model assumes atomic try-lock acquisition, fresh committed views, complete
writer participation and trustworthy transaction-fatal retry state. Their real
PostgreSQL implementation remains unresolved. Existing OE owner/workflow locks
also limit actual concurrency. Neither benchmark improvement nor deadline margin
is demonstrated.

These files intentionally make no changes to application imports, migrations,
provider behavior, CI configuration, receipts, charges, replay or deadlines.

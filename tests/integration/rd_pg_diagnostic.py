"""Test-only PostgreSQL function counters, not an execution/authorization gate.

Each sample uses its own read-only transaction against the fixture's public
schema. Counters are cumulative per database, so only before/after differences
are emitted. A first observed row has absolute counters with delta=null, never
a fabricated zero baseline. Natural backend flush can lag; untracked (including inlined
SQL) is not zero cost. Inclusive total and self times must not be added together.
No reset, forced writer flush, extra model execution, or per-commit query occurs.
"""

import json
import math
from contextlib import contextmanager

from sqlalchemy import bindparam, text

FUNCTIONS = (
    "mineagent_rd_source_deferred",
    "mineagent_rd_source_violation",
    "mineagent_rd_shared_consistent",
    "mineagent_rd_seed_consistent",
    "mineagent_rd_seed_check",
    "mineagent_rd_provider_graph_trigger",
    "mineagent_rd_provider_graph",
    "mineagent_rd_provider_request_binding",
    "mineagent_rd_provider_reservation",
    "mineagent_rd_provider_queued_config",
    "mineagent_rd_provider_config",
    "mineagent_analysis_plan_check",
    "mineagent_rd_canonical_json",
    "mineagent_rd_schema",
    "mineagent_rd_ascii_metadata",
    "mineagent_rd_workflow_consistent",
    "mineagent_rd_workflow_submission_check",
    "mineagent_rd_workflow_config",
    "mineagent_workflow_check",
    "mineagent_workflow_consistent",
)
QUERY = text(
    "SELECT funcname, calls, total_time, self_time "
    "FROM pg_catalog.pg_stat_user_functions "
    "WHERE schemaname = 'public' AND funcname IN :names"
).bindparams(bindparam("names", expanding=True))


def _snapshot(engine):
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            if connection.exec_driver_sql("SHOW track_functions").scalar_one() != "pl":
                return None
            rows = connection.execute(QUERY, {"names": FUNCTIONS}).all()
        result = {}
        for name, calls, total, own in rows:
            if (
                type(name) is not str
                or name not in FUNCTIONS
                or name in result
                or type(calls) is not int
                or not 0 <= calls <= 10**12
                or any(
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or not 0 <= value <= 10**12
                    for value in (total, own)
                )
            ):
                return None
            result[name] = (calls, total, own)
        return result
    except Exception:  # noqa: BLE001 -- diagnostics may not replace original failure
        # Never retain or render SQL, connection details, parameters or errors.
        return None


def _difference(before, after):
    if before is None or after is None:
        return {"status": "unavailable"}
    values = []
    for name in FUNCTIONS:
        if name not in after:
            values.append({"function": name, "status": "missing_or_untracked"})
            continue
        if name not in before:
            calls, total, own = after[name]
            values.append(
                {
                    "function": name,
                    "status": "baseline_missing",
                    "delta": None,
                    "observed_calls": calls,
                    "observed_total_ms": round(total, 3),
                    "observed_self_ms": round(own, 3),
                }
            )
            continue
        delta = tuple(new - old for old, new in zip(before[name], after[name]))
        if any(value < 0 for value in delta):
            return {"status": "counter_reset_or_invalid"}
        values.append(
            {
                "function": name,
                "status": "sampled",
                "calls": delta[0],
                "total_ms": round(delta[1], 3),
                "self_ms": round(delta[2], 3),
            }
        )
    return {"status": "sampled_natural_flush_may_lag", "functions": values}


@contextmanager
def diagnose_provider_functions(engine, *, stage):
    if stage not in ("parent", "seeded_child", "workflow_first", "workflow_seeded"):
        raise ValueError("invalid diagnostic stage")
    before = _snapshot(engine)
    try:
        yield
    finally:
        # Diagnostics must never replace the original worker/assertion failure.
        try:
            report = _difference(before, _snapshot(engine))
            print(
                "RD_PG_FUNCTIONS:"
                + json.dumps({"stage": stage, **report}, sort_keys=True),
                flush=True,
            )
        except Exception:  # noqa: BLE001, S110 -- no raw diagnostic exception output
            pass

"""Test-only PostgreSQL function counters, not an execution/authorization gate.

Each sample uses its own read-only transaction against the fixture's public
schema. Counters are cumulative per database, so only before/after differences
are emitted. A first observed row has absolute counters with delta=null, never
a fabricated zero baseline. Natural backend flush can lag; untracked (including inlined
SQL) is not zero cost. Inclusive total and self times must not be added together.
No reset, forced writer flush, extra model execution, or per-commit query occurs.
Function tracking adds overhead: attribution evidence, not improved-latency proof.
These are whole populated-worker deltas, not receipt-only function timings.
"""

import json
import math
from contextlib import contextmanager

from sqlalchemy import bindparam, text

# Fixed 032c graph/reverse helper closure, audited from frozen upgrade SQL.
# Includes SQL helpers which may be inlined/untracked under track_functions=pl.
FUNCTIONS = (
    "mineagent_oes_consistent",
    "mineagent_oes_graph_check",
    "mineagent_oes_checkpoint_check",
    "mineagent_oes_provider_check",
    "mineagent_oes_provider_contract",
    "mineagent_oes_receipt_extension",
    "mineagent_oes_contract",
    "mineagent_oes_contract_before_upload",
    "mineagent_oes_contract_before_workflow",
    "mineagent_oes_contract_v030_shape",
    "mineagent_oes_spec_check",
    "mineagent_oes_epoch_check",
    "mineagent_oes_population_check",
    "mineagent_oes_candidate_check",
    "mineagent_oes_application_check",
    "mineagent_oes_controller_check",
    "mineagent_oes_controller_completed",
    "mineagent_oes_controller_payload",
    "mineagent_oes_execution_check",
    "mineagent_oes_iteration_check",
    "mineagent_oes_iteration_contract",
    "mineagent_oes_measurement_matches",
    "mineagent_oes_native_check",
    "mineagent_oes_outcome_check",
    "mineagent_oes_placement_check",
    "mineagent_oes_sampling_check",
    "mineagent_oes_worker_check",
    "mineagent_oes_control_check",
    "mineagent_oe_workflow_reverse",
    "mineagent_oe_workflow_shape",
    "mineagent_rd_shared_consistent",
    "mineagent_rd_schema",
    "mineagent_rd_canonical_json",
    "mineagent_execution_metadata_sha",
    "mineagent_analysis_plan_check",
    "mineagent_analysis_raw_commitment",
    "mineagent_analysis_raw_json",
    "mineagent_budget_vector",
    "mineagent_numeric_rejection_payload",
    "mineagent_oe_context",
    "mineagent_oe_request",
    "mineagent_oe_shape",
    "mineagent_oe_workflow_attachment",
    "mineagent_oe_workflow_checkpoint",
    "mineagent_oe_workflow_consumption",
    "mineagent_oe_workflow_rendered",
    "mineagent_rd_ascii_metadata",
    "mineagent_rd_controller_kernel",
    "mineagent_rd_controller_profile_matches",
    "mineagent_rd_controller_selection",
    "mineagent_rd_fixture_request_026",
    "mineagent_rd_json_bytes",
    "mineagent_rd_provider_config",
    "mineagent_rd_provider_config_026_frozen",
    "mineagent_rd_provider_payload",
    "mineagent_rd_provider_queued_config",
    "mineagent_rd_seed_reference",
    "mineagent_rd_source_context",
    "mineagent_rd_source_request",
    "mineagent_rd_workflow_config",
    "mineagent_rd_workflow_submission_check",
    "mineagent_structured_contract",
    "mineagent_structured_json_value",
    "mineagent_structured_payload",
    "mineagent_structured_raw_commitment",
    "mineagent_structured_raw_json",
    "mineagent_structured_stopped",
    "mineagent_upload_basket_check",
    "mineagent_upload_oe_qualification",
    "mineagent_workflow_checkpoint",
    "mineagent_workflow_consistent",
    "mineagent_workflow_lock",
    "mineagent_workflow_payload",
    "mineagent_workflow_payload_before_oe",
    "mineagent_workflow_raw",
    "mineagent_workflow_refs",
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
        if delta[0] == 0:
            values.append(
                {"function": name, "status": "unchanged_unknown", "delta": None}
            )
            continue
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
def diagnose_receipt_functions(engine, *, stage):
    if stage not in ("normal",):
        raise ValueError("invalid diagnostic stage")
    before = _snapshot(engine)
    try:
        yield
    finally:
        # Diagnostics must never replace the original worker/assertion failure.
        try:
            report = _difference(before, _snapshot(engine))
            print(
                "OE_PG_FUNCTIONS_V1:"
                + json.dumps({"stage": stage, **report}, sort_keys=True),
                flush=True,
            )
        except Exception:  # noqa: BLE001, S110 -- no raw diagnostic exception output
            pass

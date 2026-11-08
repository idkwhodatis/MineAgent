"""Closed OE function counters sampled only outside the actual queue execution.

Reuse the RD sanitizer and read-only sampling semantics. Natural statistics
flush can lag. Inclusive and self times overlap; missing counters are not zero.
"""

from contextlib import contextmanager

from rd_pg_diagnostic import _difference, _snapshot

FUNCTIONS = (
    "mineagent_oes_consistent",
    "mineagent_oes_graph_check",
    "mineagent_rd_shared_consistent",
    "mineagent_neutral_model_consistent",
    "mineagent_oes_spec_check",
    "mineagent_oes_epoch_check",
    "mineagent_oes_controller_check",
    "mineagent_oes_control_check",
    "mineagent_oes_population_check",
    "mineagent_oes_sampling_check",
    "mineagent_oes_placement_check",
    "mineagent_oes_iteration_check",
    "mineagent_oes_provider_check",
    "mineagent_oes_candidate_check",
    "mineagent_oes_execution_check",
    "mineagent_oes_outcome_check",
    "mineagent_oes_worker_check",
    "mineagent_oes_application_check",
    "mineagent_oes_checkpoint_check",
    "mineagent_oes_native_check",
    "mineagent_oes_contract",
    "mineagent_oes_provider_contract",
    "mineagent_structured_raw_commitment",
    "mineagent_structured_raw_json",
    "mineagent_structured_stopped",
    "mineagent_rd_schema",
    "mineagent_rd_canonical_json",
)


@contextmanager
def diagnose_oe_functions(engine, emit):
    before = _snapshot(engine, functions=FUNCTIONS)
    try:
        yield
    finally:
        try:
            emit(
                "OE_PG_FUNCTIONS_V1:",
                _difference(
                    before, _snapshot(engine, functions=FUNCTIONS), functions=FUNCTIONS
                ),
            )
        except Exception:  # noqa: BLE001, S110 -- retain original worker failure
            pass

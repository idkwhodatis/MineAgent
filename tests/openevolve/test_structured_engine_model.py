"""027 model metadata only, not installed SQL/engine acceptance."""

from sqlalchemy import ForeignKeyConstraint, Text, UniqueConstraint

from backend.app.oe_structured_models import OEStructuredEngineOutcome


def test_engine_outcome_uses_distinct_scoped_execution_and_receipts():
    table = OEStructuredEngineOutcome.__table__
    assert table.name == "oe_structured_engine_outcomes"
    targets = {
        tuple(element.target_fullname for element in constraint.elements)
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    scope = ("owner_id", "run_id", "attempt_id", "generation")
    assert (
        tuple("oe_structured_executions." + name for name in (*scope, "id")) in targets
    )
    assert (
        tuple(
            "research_process_receipts." + name
            for name in (*scope, "admission_id", "id", "kind")
        )
        in targets
    )
    assert not any(
        any("structured_factor_executions" in part for part in target)
        for target in targets
    )
    uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert {
        (key,) for key in ("execution_id", "candidate_id", "admission_id")
    } <= uniques


def test_float_result_and_measurement_keep_original_canonical_text():
    table = OEStructuredEngineOutcome.__table__
    assert isinstance(table.c.result_canonical.type, Text)
    assert isinstance(table.c.measurement_canonical.type, Text)
    assert {
        "result_sha256",
        "result_size_bytes",
        "measurement_sha256",
        "objective_sha256",
    } <= set(table.c.keys())

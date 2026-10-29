"""Literal/schema regression; actual PG is a separate installed integration gate."""

import ast
import json
from pathlib import Path

import pytest

from backend.app import declared_upload_revision as revision
from backend.app.oe_structured_domain_sql import render_contract_sql
from backend.app.rdagent_provider_revision import function
from script.generate_declared_upload_migration import document

ROOT = Path(__file__).resolve().parents[2]


def test_frozen030_document_reproduces_exactly():
    assert (
        ROOT / "backend/alembic/versions/030_declared_upload_basket.py"
    ).read_text() == document()


def test_legacy028_contract_stays_byte_identical_with_new_python_union():
    assert function(
        render_contract_sql(), "mineagent_oes_contract"
    ) == revision.predecessor("mineagent_oes_contract")


def test_predecessor_hash_drift_fails_closed(monkeypatch):
    monkeypatch.setattr(revision, "PREDECESSOR_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="Exact028"):
        revision.render_upgrade_sql()


def test_no_current_application_imports_in_frozen_migration():
    tree = ast.parse(document())
    assert [
        (n.module, [v.name for v in n.names])
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom)
    ] == [("alembic", ["op"])]


def test_new_qualification_requires_explicit_input_schema_pair():
    from packages.openevolve_structured.provider_run import FixedExecutionInputs
    from tests.openevolve.test_structured_persisted_identity import graph

    old = graph()[1].config["fixed_inputs"]
    for schema in (
        "mineagent.oe-structured-retained-inputs.v1",
        "mineagent.oe-structured-retained-inputs.v2",
    ):
        for qualification in (
            "synthetic_by_construction",
            "user_declaration_unverified",
            "unknown",
            None,
            False,
        ):
            raw = dict(old, schema_version=schema, market_qualification=qualification)
            expected = (schema, qualification) in [
                (
                    "mineagent.oe-structured-retained-inputs.v1",
                    "synthetic_by_construction",
                ),
                (
                    "mineagent.oe-structured-retained-inputs.v2",
                    "user_declaration_unverified",
                ),
            ]
            if expected:
                assert (
                    FixedExecutionInputs.model_validate_json(
                        json.dumps(raw)
                    ).market_qualification
                    == qualification
                )
            else:
                with pytest.raises(ValueError):
                    FixedExecutionInputs.model_validate_json(json.dumps(raw))


def test_all_reviewed_numeric_deltas_are_explicit_and_other_schema_changes_refuse():
    from backend.app.declared_upload_numeric_schema import (
        contract_sql,
        epoch_sql,
        only_numeric_bounds,
        workflow_claim_sql,
    )

    _, contract_changes = contract_sql(revision.predecessor("mineagent_oes_contract"))
    _, bootstrap_changes = epoch_sql(revision.predecessor("mineagent_oes_epoch_check"))
    _, claim_changes = workflow_claim_sql(
        revision.predecessor("mineagent_workflow_payload")
    )
    assert len(contract_changes) == 8
    assert len(bootstrap_changes) == 3
    assert claim_changes == [
        (
            ("properties", "expected_revision"),
            {"minimum": 1, "exclusiveMaximum": 1000000},
            {"minimum": 1, "maximum": 999999},
        )
    ]
    with pytest.raises(ValueError, match="nonnumeric"):
        only_numeric_bounds({"type": "string"}, {"type": "number"})


@pytest.mark.parametrize("maximum", [0.5, 2, 1000000000])
def test_future_numeric_drift_is_not_silently_accepted(maximum):
    from backend.app.declared_upload_numeric_schema import (
        require_reviewed_float_changes,
    )

    with pytest.raises(ValueError, match="bounds"):
        require_reviewed_float_changes(
            [
                (
                    ("$defs", "StructuredProgram", "properties", "maximum_weight"),
                    {"minimum": 1, "maximum": 1},
                    {"exclusiveMinimum": 0, "maximum": maximum},
                )
            ],
            [("StructuredProgram", "maximum_weight")],
        )


def test_downgrade_rejects_fractional_configs_before_restoring_old_predicates():
    sql = revision.render_downgrade_sql()
    assert "mineagent_oes_contract_before_upload('config',config) IS NOT TRUE" in sql
    assert (
        "mineagent_oes_contract_before_upload('candidate',candidate_canonical::jsonb) IS NOT TRUE"
        in sql
    )
    assert sql.index("prevents downgrade") < sql.index("CREATE OR REPLACE FUNCTION")


def test_signed_child_exit_is_domain_scoped_and_retained_negative_blocks_downgrade():
    from backend.app import declared_upload_numeric_sql as signed

    assert signed.render_upgrade_sql() in revision.render_upgrade_sql()
    assert signed.render_downgrade_sql() in revision.render_downgrade_sql()
    assert (
        "kind='rejection' AND payload->'child_exit_code' < '0'::jsonb"
        in revision.render_downgrade_sql()
    )

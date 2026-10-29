"""031 declarative metadata only; no installed database acceptance claim."""

import importlib

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from backend.app.oe_workflow_models import OEWorkflowSubmission


def test_every_foreign_key_resolves_to_real_unique_predecessor_columns():
    for module in (
        "research_workflow_models",
        "oe_structured_models",
        "execution_snapshot_models",
        "factor_models",
        "factor_analysis_models",
    ):
        importlib.import_module("backend.app." + module)
    table = OEWorkflowSubmission.__table__
    assert "spec_id" not in table.c
    assert all(not c.nullable for c in table.columns)
    for fk in table.foreign_key_constraints:
        target = fk.referred_table
        columns = tuple(element.column.name for element in fk.elements)
        keys = {
            tuple(c.name for c in target.primary_key.columns),
            *(
                tuple(c.name for c in k.columns)
                for k in target.constraints
                if isinstance(k, UniqueConstraint)
            ),
        }
        assert columns in keys, (target.name, columns)
        assert fk.ondelete == "RESTRICT"


def test_unique_child_and_stage_cannot_be_reused():
    table = OEWorkflowSubmission.__table__
    uniques = {
        tuple(c.name for c in k.columns)
        for k in table.constraints
        if isinstance(k, UniqueConstraint)
    }
    assert {
        ("stage_id",),
        ("run_id",),
        ("submission_id",),
        ("owner_id", "run_id", "id"),
    } <= uniques
    checks = [
        str(c.sqltext) for c in table.constraints if isinstance(c, CheckConstraint)
    ]
    assert "submission_origin='workflow_stage'" in checks
    assert any(
        "stage_id <> rd_stage_id" in c and "run_id <> source_run_id" in c
        for c in checks
    )
    assert not any("authenticated_user" in c for c in checks)


def test_release_is_one_real_exposure_not_a_caller_approval_boolean():
    table = OEWorkflowSubmission.__table__
    fk = next(
        c
        for c in table.constraints
        if isinstance(c, ForeignKeyConstraint)
        and tuple(c.column_keys) == ("release_exposure_id",)
    )
    assert [e.target_fullname for e in fk.elements] == ["data_usage_exposures.id"]
    assert not {"released", "approved", "consumed", "spec_id"} & set(table.c.keys())
    assert len([column for column in table.c if column.name.endswith("_sha256")]) == 9

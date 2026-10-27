"""Offline022 relational shape only; not installed PostgreSQL authority."""

from sqlalchemy import PrimaryKeyConstraint, UniqueConstraint

from backend.app.factor_analysis_models import (
    FactorAnalysisPlan,
    FactorDirectAnalysisIntent,
)


def test_analysis_plan_is_unique_per_run_and_bound_to_exact_spec():
    table = FactorAnalysisPlan.__table__
    assert any(
        isinstance(c, UniqueConstraint)
        and tuple(c.columns.keys()) == ("owner_id", "run_id")
        for c in table.constraints
    )
    assert any(
        tuple(e.target_fullname for e in fk.elements)
        == (
            "factor_research_specs.owner_id",
            "factor_research_specs.run_id",
            "factor_research_specs.id",
        )
        for fk in table.foreign_key_constraints
    )
    assert {
        "objective",
        "objective_sha256",
        "source_policy",
        "source_policy_sha256",
        "label_source_dataset_id",
        "label_source_manifest_sha256",
    } <= set(table.columns.keys())


def test_analysis_requires_completed_snapshot_not_legacy_manifest():
    refs = {
        e.target_fullname
        for fk in FactorDirectAnalysisIntent.__table__.foreign_key_constraints
        for e in fk.elements
    }
    assert "factor_execution_snapshot_outcomes.publication_id" in refs
    assert not any(ref.startswith("feature_snapshots.") for ref in refs)


def test_analysis_foreign_keys_have_exact_unique_targets():
    for table in (FactorAnalysisPlan.__table__, FactorDirectAnalysisIntent.__table__):
        for fk in table.foreign_key_constraints:
            target = fk.elements[0].column.table
            names = tuple(e.column.name for e in fk.elements)
            assert any(
                isinstance(c, (PrimaryKeyConstraint, UniqueConstraint))
                and tuple(c.columns.keys()) == names
                for c in target.constraints
            ), (table.name, target.name, names)

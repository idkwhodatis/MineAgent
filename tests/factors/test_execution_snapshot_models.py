"""Offline exact schema graph checks, not installed PostgreSQL evidence."""

from sqlalchemy import PrimaryKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from backend.app.execution_snapshot_ddl import TABLE_SQL
from backend.app.models import Base

TABLES = (
    "factor_execution_snapshot_publications",
    "factor_execution_snapshot_partitions",
    "factor_execution_snapshot_objects",
    "factor_execution_snapshot_outcomes",
    "factor_execution_evaluation_outcomes",
    "factor_evaluation_result_consumers",
)


def test_frozen_tables_match_current_explicit_models():
    rendered = (
        ";\n".join(
            str(
                CreateTable(Base.metadata.tables[name]).compile(
                    dialect=postgresql.dialect()
                )
            )
            for name in TABLES
        )
        + ";\n"
    )
    assert rendered == TABLE_SQL


def test_each_foreign_key_has_exact_scoped_unique_target():
    for name in TABLES:
        for fk in Base.metadata.tables[name].foreign_key_constraints:
            columns = [element.column.name for element in fk.elements]
            target = fk.elements[0].column.table
            assert any(
                isinstance(constraint, (PrimaryKeyConstraint, UniqueConstraint))
                and list(constraint.columns.keys()) == columns
                for constraint in target.constraints
            ), (name, target.name, columns)


def test_new_execution_outcomes_do_not_impersonate_legacy_snapshots():
    table = Base.metadata.tables["factor_execution_evaluation_outcomes"]
    targets = {
        element.column.table.name
        for fk in table.foreign_key_constraints
        for element in fk.elements
    }
    assert "factor_execution_snapshot_outcomes" in targets
    assert "feature_snapshots" not in targets
    assert "factor_generation_boundaries" in targets

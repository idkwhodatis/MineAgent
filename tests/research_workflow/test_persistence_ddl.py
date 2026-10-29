"""Exact offline table inventory and referenced keys; actual PG remains required."""

import pytest
from sqlalchemy import PrimaryKeyConstraint, UniqueConstraint

from backend.app import research_workflow_persistence_ddl as ddl


def test_twelve_tables_all_deferred_and_no_legacy_recreation():
    tables = ddl.tables()
    assert set(tables) == set(ddl.TABLES) and len(tables) == 12
    sql = ddl.render_upgrade_sql("reviewed")
    assert sql.count("CREATE TABLE reviewed.") == 12
    assert sql.count("DEFERRABLE INITIALLY DEFERRED") == len(ddl.foreign_keys())
    assert "CREATE TABLE reviewed.research_runs" not in sql
    assert "%%" not in sql
    assert ddl.render_downgrade_sql("reviewed").count("DROP TABLE reviewed.") == 12


def test_every_foreign_key_targets_real_declared_unique_key():
    for table in ddl.tables().values():
        for fk in table.foreign_key_constraints:
            remote = tuple(e.column.name for e in fk.elements)
            target = fk.elements[0].column.table
            keys = {
                tuple(c.name for c in constraint.columns)
                for constraint in target.constraints
                if isinstance(constraint, (PrimaryKeyConstraint, UniqueConstraint))
            }
            assert remote in keys, (table.name, target.name, remote)


@pytest.mark.parametrize("schema", ["", "public;drop", '"public"', None])
def test_schema_is_not_sql_input(schema):
    with pytest.raises(ValueError):
        ddl.render_upgrade_sql(schema)

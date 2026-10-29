"""Actual SQLAlchemy metadata compilation; no PostgreSQL execution claim."""

import pytest
from sqlalchemy import PrimaryKeyConstraint, UniqueConstraint

from backend.app import oe_structured_persistence_ddl as ddl
from backend.app.oe_structured_trigger_sql import TABLES


def test_exact_domain_tables_and_all_foreign_keys_are_deferred():
    sql = ddl.render_upgrade_sql("review027")
    assert set(ddl.tables()) == set(TABLES) | {"factor_numeric_failure_feedbacks"}
    assert sql.count("CREATE TABLE review027.") == 21
    assert sql.count("FOREIGN KEY") == len(ddl.foreign_keys())
    assert sql.count("DEFERRABLE INITIALLY DEFERRED") == len(ddl.foreign_keys())
    assert "CREATE TABLE review027.research_runs" not in sql
    assert "CREATE TRIGGER" not in sql
    assert sql == ddl.render_upgrade_sql("review027")


def test_every_actual_referenced_tuple_has_an_existing_exact_unique_key():
    for table in ddl.tables().values():
        for fk in table.foreign_key_constraints:
            target = fk.elements[0].column.table
            columns = {e.column.name for e in fk.elements}
            keys = [
                {c.name for c in constraint.columns}
                for constraint in target.constraints
                if isinstance(constraint, (PrimaryKeyConstraint, UniqueConstraint))
            ]
            if target.name in ddl.EXTERNAL_UNIQUES:
                keys.append(set(ddl.EXTERNAL_UNIQUES[target.name]))
            assert columns in keys, (table.name, target.name, columns)


def test_drop_breaks_cycles_before_tables_without_cascade():
    down = ddl.render_downgrade_sql()
    assert down.count("DROP CONSTRAINT") == len(ddl.foreign_keys()) + len(
        ddl.EXTERNAL_UNIQUES
    )
    assert down.index("DROP CONSTRAINT oes027_fk_") < down.index("DROP TABLE")
    assert down.rindex("DROP TABLE") < down.index("DROP CONSTRAINT oes027_uq_")
    assert "CASCADE" not in down
    for value in ("public;DROP TABLE x", "Public", "@SCHEMA@", "", None):
        with pytest.raises(ValueError, match="Exact installation schema"):
            ddl.render_upgrade_sql(value)


def test_literal_compiler_never_applies_dbapi_percent_escaping():
    from sqlalchemy import CheckConstraint, Column, Integer, MetaData, Table
    from sqlalchemy.dialects.postgresql import dialect
    from sqlalchemy.schema import CreateTable

    table = Table(
        "literal_probe",
        MetaData(),
        Column("offset_bytes", Integer),
        CheckConstraint("offset_bytes%65536=0"),
    )
    sql = str(CreateTable(table).compile(dialect=dialect(paramstyle="named")))
    assert "offset_bytes%65536=0" in sql and "%%" not in sql
    assert "%%" not in ddl.render_upgrade_sql("review027")
    import inspect

    assert 'paramstyle="named"' in inspect.getsource(ddl.render_upgrade_sql)

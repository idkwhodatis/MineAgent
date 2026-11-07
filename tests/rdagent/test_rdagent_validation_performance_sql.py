"""Offline source invariants, not evidence of PostgreSQL execution or speedup."""

from backend.app import rdagent_validation_performance_sql as module
from backend.app.rdagent_provider_revision import schema_boolean_sql


def test_predecessors_and_downgrade_preserve_frozen_bodies():
    assert module.schema_original() == schema_boolean_sql()
    assert module.render_downgrade_sql() == module.canonical_original()


def test_upgrade_replaces_only_canonical_without_schema_changes():
    sql = module.render_upgrade_sql()
    assert sql == module.canonical_sql()
    assert sql.count("CREATE OR REPLACE FUNCTION") == 1
    assert "mineagent_rd_schema" not in sql
    assert "mineagent_rd_schema" not in module.render_downgrade_sql()


def test_scalar_fast_paths_retain_all_bounds_and_secure_search_path():
    sql = module.canonical_sql()
    assert "IMMUTABLE STRICT\nSET search_path=pg_catalog" in sql
    assert "IF depth<0 OR depth>24 THEN" in sql
    assert sql.count(module.DEPTH_CHECK) == 2
    assert sql.count(module.SCALAR_VALUE) == 2
    assert sql.count("IF octet_length(child)>1048576 THEN") == 2
    assert "IF octet_length(key_text)>1048576 THEN" in sql
    assert sql.count("IF octet_length(result)>1048576 THEN") == 3
    assert 'ORDER BY key COLLATE "C"' in sql
    assert "current_setting('server_encoding') <> 'UTF8'" in sql
    assert "position(chr(127) in t)=0" in sql
    # Slow strings still use the predecessor's exact Unicode escaping implementation.
    assert (
        sql[sql.index(" WHEN 'string' THEN\n   result") :]
        == module.canonical_original()[
            module.canonical_original().index(" WHEN 'string' THEN\n   result") :
        ]
    )


def test_upgrade_changes_functions_only():
    sql = module.render_upgrade_sql()
    assert sql.count("CREATE OR REPLACE FUNCTION") == 1
    for forbidden in (
        "ALTER TABLE",
        "DROP CONSTRAINT",
        "CREATE TABLE",
        "statement_timeout",
        "pg_temp",
    ):
        assert forbidden not in sql
    assert "@UNION@" not in sql


def test_differential_helper_preserves_sql_and_json_null_arguments(monkeypatch):
    from tests.integration import test_rdagent_validation_performance as regression

    captured = []

    def capture(connection, sql, params):
        captured.append(params)
        return ("ok", False)

    monkeypatch.setattr(regression, "outcome", capture)
    regression.schema_pair(None, None, None, None, depth=None)
    assert captured[-1] == {"v": "null", "r": "null", "defs": "null", "depth": None}
    regression.schema_pair(
        None,
        "x",
        {"type": "string"},
        {"s": {}},
        depth=None,
        sql_null=True,
        sql_null_rule=True,
        sql_null_defs=True,
    )
    assert captured[-1] == {"v": None, "r": None, "defs": None, "depth": None}
    regression.schema_pair(None, "x", {"type": "string"})
    assert captured[-1]["defs"] == "{}"
    regression.schema_pair(None, "x", {"type": "string"}, [])
    assert captured[-1]["defs"] == "[]"

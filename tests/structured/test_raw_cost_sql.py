"""Exact reconstructable source edits; real PostgreSQL parity is separate."""

from backend.app import structured_raw_cost_sql as module


def test_raw_predecessor_is_exactly_reconstructed():
    sql = module.raw_sql()
    assert sql.count(module.scalar_child()) == 2
    sql = sql.replace(module.scalar_child(), module.CHILD)
    assert sql.count(module.ascii_string("key", module.CANONICAL_KEY)) == 1
    sql = sql.replace(
        module.ascii_string("key", module.CANONICAL_KEY), module.CANONICAL_KEY
    )
    assert sql == module.raw_original()
    assert module.render_downgrade_sql() == module.raw_original()


def test_only_lossless_serializer_replaced_no_caller_or_graph_changes():
    sql = module.render_upgrade_sql()
    assert sql.count("CREATE OR REPLACE FUNCTION") == 1
    assert sql == module.raw_sql()
    for forbidden in (
        "CREATE TABLE",
        "ALTER TABLE",
        "CREATE TRIGGER",
        "pg_temp",
        "set_config",
        "statement_timeout",
        "graph_check",
    ):
        assert forbidden not in sql


def test_duplicate_depth_bound_number_and_unicode_fallback_retained():
    sql, old = module.raw_sql(), module.raw_original()
    assert (
        sql[: sql.index(" CASE json_typeof(v)")]
        == old[: old.index(" CASE json_typeof(v)")]
    )
    assert (
        "current_setting('server_encoding')<>'UTF8'"
        in sql[: sql.index(" CASE json_typeof(v)")]
    )
    assert 'count(DISTINCT key COLLATE "C")' in sql
    assert 'ORDER BY key COLLATE "C"' in sql
    assert "depth<24 AND octet_length(value::text)<=16777216" in sql
    assert "position(chr(127) in key)=0" in sql
    assert "position(chr(127) in value#>>'{}')=0" in sql
    assert "octet_length(pg_catalog.to_json(key)::text)<=1048576" in sql
    # Root scalar grammar, float8 representability and output bound are untouched.
    start = " WHEN 'string' THEN result:="
    assert sql[sql.index(start) :] == old[old.index(start) :]

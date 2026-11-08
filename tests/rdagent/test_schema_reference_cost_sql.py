"""Frozen helper/body reconstruction; PostgreSQL parity is an independent gate."""

from backend.app import schema_reference_cost_sql as module
from backend.app.rdagent_workflow_cost_sql import schema_sql as previous


def test_exact_frozen_predecessor_and_recursive_helper():
    assert module.schema_original() == previous()
    assert (
        module.recursive_helper().replace(module.HELPER, module.NAME)
        == module.schema_original()
    )
    assert "@SCHEMA@." + module.NAME + "(" not in module.recursive_helper()
    assert module.recursive_helper().count("@SCHEMA@." + module.HELPER + "(") > 1


def test_ordinary_loop_reconstructs_exact_predecessor():
    sql = module.schema_sql()
    assert sql.count(module.reference_switch()) == 1
    assert sql.index(module.GUARD) < sql.index(module.reference_switch())
    assert "depth:=depth+1;\n   IF depth>24 THEN RETURN false; END IF;" in sql
    for call, count in zip(module.RECURSIVE_CALLS, (1, 2)):
        assert sql.count(module.recursive_dispatch(call)) == count
        sql = sql.replace(module.recursive_dispatch(call), call)
    assert (
        sql.replace(module.reference_switch(), module.REFERENCE)
        == module.schema_original()
    )
    assert (
        "RETURN @SCHEMA@." + module.HELPER + "(v,rule,defs,depth)"
        not in module.schema_sql()
    )


def test_bundle_function_only_and_downgrade_restores_before_drop():
    sql = module.render_upgrade_sql()
    assert sql.count("CREATE OR REPLACE FUNCTION") == 2
    assert sql == module.recursive_helper() + "\n" + module.schema_sql()
    down = module.render_downgrade_sql()
    assert down.startswith(module.schema_original())
    assert down.count("DROP FUNCTION") == 1
    assert down.endswith(
        f"DROP FUNCTION @SCHEMA@.{module.HELPER}(jsonb,jsonb,jsonb,integer);"
    )
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

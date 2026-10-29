"""Literal composition/dependency checks only. Real PG gate still required."""

import re

from backend.app import oe_structured_revision_ddl as ddl


def test_complete_local_function_inventory_and_no_placeholder_drift():
    sql = ddl.render_upgrade_sql()
    defined = re.findall(
        r"CREATE(?: OR REPLACE)? FUNCTION (?:@SCHEMA@\.)?(mineagent_oes_\w+)\(", sql
    )
    referenced = set(re.findall(r"(mineagent_oes_\w+)\(", sql))
    assert len(defined) == len(set(defined))
    assert referenced == set(defined)
    assert set(re.findall(r"@[A-Z_]+@", sql)) == {"@SCHEMA@"}
    assert "IS DISTINCT FROM CASE" not in sql
    assert "CREATE TABLE" not in sql  # separate first literal, no duplicate tables


def test_domain_predicates_precede_global_deferred_installation():
    sql = ddl.render_upgrade_sql()
    assert sql.index("CREATE FUNCTION @SCHEMA@.mineagent_oes_graph_check") < sql.index(
        "CREATE CONSTRAINT TRIGGER oes027_consistent"
    )
    assert sql.index(
        "CREATE FUNCTION @SCHEMA@.mineagent_numeric_rejection_stopped"
    ) < sql.index("CREATE TRIGGER oes027_receipt_extension")
    tables = ddl.table_sql()
    assert tables.count("CREATE TABLE @SCHEMA@.") == 21
    assert ddl.SENTINEL not in tables


def test_refuse_all_retained027_before_restoring_old_predicates_or_dropping_tables():
    down = ddl.render_downgrade_sql()
    assert down.startswith("DO $$ BEGIN IF ")
    guard = ddl.downgrade_guard()
    for name in ddl.oe_structured_persistence_ddl.tables():
        assert f"FROM @SCHEMA@.{name})" in guard
    for kind in ddl.KINDS:
        assert "'" + kind + "'" in guard
    assert "controller_completion" in guard and "rejection" in guard
    assert down.index("Retained027 evidence prevents downgrade") < down.index(
        "DROP TRIGGER"
    )
    assert down.rindex("DROP FUNCTION") < down.index("DROP TABLE")
    assert "CASCADE" not in down


def test_successor_keeps_controller027_profile_and_physical_limits():
    up = ddl.render_upgrade_sql()
    down = ddl.render_downgrade_sql()
    for sql in (up, down):
        assert "mineagent.rdagent-orchestrator.v3" in sql
        assert "THEN 120000 ELSE 60000 END)" in sql
        assert "THEN interval '120 seconds' ELSE interval '60 seconds' END)" in sql
    assert (
        "mineagent_rd_controller_profile_matches(spec.config,p.profile) IS TRUE" in up
    )

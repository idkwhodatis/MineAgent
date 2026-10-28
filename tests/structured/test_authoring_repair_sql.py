"""Exact predecessor preservation and production canonical regression corpus."""

import pytest

from backend.app import structured_authoring_repair_sql as repair
from backend.app.openevolve_revision_ddl import _function, historical
from tests.integration.structured_authoring_fixture import (
    authoring_cases,
    authoring_statements,
)


def test_upgrade_only_adds_local_rule_projection():
    old = repair.previous_function()
    assert repair.render_upgrade_sql().replace(repair.PROJECTION, "") == old
    assert old == _function(
        historical("025", "UPGRADE_SQL"), "mineagent_structured_contract"
    ).replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    assert "v:=" not in repair.PROJECTION
    assert (
        "CREATE OR REPLACE FUNCTION @SCHEMA@.mineagent_rd_schema"
        not in repair.render_upgrade_sql()
    )


def test_downgrade_keeps_retained_input_guard_and_exact_original_function():
    assert repair.render_downgrade_sql().endswith(repair.previous_function())
    assert (
        "Cannot downgrade retained structured inputs" in repair.render_downgrade_sql()
    )


def test_predecessor_pin_fails_closed(monkeypatch):
    monkeypatch.setattr(repair, "PREDECESSOR_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="predecessor"):
        repair.render_upgrade_sql()


def test_real_assembly_and_explicit_authoring_match_canonical_matrix(monkeypatch):
    cases = authoring_cases()
    assert len(cases) == 14
    assert "authoring" not in cases[0][1]["engine"]["manifest"]
    statements = authoring_statements()
    assert len(statements) == 15

    # Exercise the hosted entry point: literal JSON must reach the DBAPI intact,
    # never be parsed as SQLAlchemy named binds (e.g. :10000 or :false).
    from unittest.mock import MagicMock

    from sqlalchemy import text

    from tests.integration import structured_authoring_fixture as fixture
    from tests.integration.test_seed_parent_contract_migration import (
        test_installed_authoring_contract_matches_real_canonical_producer as run,
    )

    assert text(statements[0]).compile().params
    monkeypatch.setattr(fixture, "authoring_statements", lambda: statements)
    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    connection.scalar.return_value = "test_schema"
    run(engine)
    connection.execute.assert_not_called()
    assert [call.args for call in connection.exec_driver_sql.call_args_list] == [
        (statement.replace("@SCHEMA@", '"test_schema"'),) for statement in statements
    ]


def test_valid_portable_authoring_remains_forbidden_in_closed_structured_engine():
    from packages.factors.contracts import canonical_bytes
    from packages.strategy_sdk.contracts import StrategyManifest
    from packages.structured_factor_strategy.wire import read_input

    doc = authoring_cases()[1][1]
    StrategyManifest.model_validate_json(canonical_bytes(doc["engine"]["manifest"]))
    with pytest.raises(ValueError, match="Portable policy declarations"):
        read_input(canonical_bytes(doc))

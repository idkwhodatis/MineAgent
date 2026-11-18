"""Installed metadata only; no heartbeat optimization or equivalence claim."""

import os

import pytest

from script import heartbeat_graph_inventory as inventory
from tests.integration.test_row_validation_invariants import catalog

pytestmark = pytest.mark.postgres


def test_installed_heartbeat_inventory_read_only_and_unchanged(migrated_engine):
    with migrated_engine.connect() as connection, connection.begin():
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        before = catalog(connection)
        target = os.environ.get("MINEAGENT_HEARTBEAT_INVENTORY_DIR")
        result = (
            inventory.export(connection, target)
            if target
            else inventory.capture(connection)
        )
        assert catalog(connection) == before
    by_name = {row["name"]: row for row in result["triggers"] if not row["internal"]}
    expected = {
        "rd_provider_graph": "mineagent_rd_provider_graph_trigger",
        "structured025_consistent": "mineagent_structured_consistent",
        "oes027_consistent": "mineagent_oes_consistent",
        "oe031_reverse": "mineagent_oe_workflow_reverse",
    }
    # Expected noninternal registrations from frozen migrations, checked against
    # installed catalog. Internal FK triggers remain separately captured.
    for name, function in expected.items():
        row = by_name[name]
        assert row["function_name"] == function
        assert row["deferrable"] and row["initially_deferred"]
        assert row["constraint_trigger"] and row["enabled"] == "O"
        assert row["event_bits"] == (21 if name == "rd_provider_graph" else 29)
        assert row["update_columns"] == ""
    immediate = {
        "guard_neutral_terminal": ("mineagent_neutral_terminal_guard", 19),
        "guard_native_terminal": ("mineagent_native_run_guard", 19),
        "guard_execution_snapshot_terminal": (
            "mineagent_execution_snapshot_terminal",
            19,
        ),
        "a_rd_source_run_lock": ("mineagent_rd_source_run_lock", 31),
    }
    assert set(by_name) == set(expected) | set(immediate)
    for name, (function, event_bits) in immediate.items():
        row = by_name[name]
        assert row["function_name"] == function
        assert row["event_bits"] == event_bits
        assert not row["deferrable"] and not row["initially_deferred"]
        assert not row["constraint_trigger"] and row["enabled"] == "O"
        assert row["update_columns"] == ""
    assert any(row["internal"] for row in result["triggers"])
    assert "static closure NOT established" in result["dependency_status"]

"""Separate actual native SQL trial; no local-admission scheduling candidate."""

import os

import pytest
from test_backup_restore import drill_databases as drill_databases  # noqa: PLC0414

from tests.sql_probes.reverse_heartbeat_native_trial import (
    isolated_native_sql_trial,
    verify_restored_completed_graph,
)

pytestmark = pytest.mark.postgres


def test_actual_current032g_workflow_with_reverse_heartbeat_sql_trial(
    drill_databases, tmp_path, monkeypatch
):
    if os.environ.get("MINEAGENT_REVERSE_HEARTBEAT_NATIVE_REQUIRED") != "1":
        pytest.fail("Explicit isolated native SQL trial required", pytrace=False)
    import test_backup_restore as legacy
    import test_workflow_current_head_baseline as baseline

    with isolated_native_sql_trial(
        baseline, legacy, drill_databases.source, monkeypatch
    ) as state:
        baseline.test_actual_current032g_workflow_completes_with_readonly_graph_checks(
            drill_databases, tmp_path, monkeypatch
        )
    if not state["restored"] or state["starts"] < 1:
        pytest.fail("Native SQL trial coverage incomplete", pytrace=False)

    try:
        verify_restored_completed_graph(state["engine"])
    finally:
        state["engine"].dispose()

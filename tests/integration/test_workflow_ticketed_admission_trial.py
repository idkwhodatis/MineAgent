"""Isolated actual current032g trial; original native baseline stays separate."""

import os

import pytest
from test_backup_restore import drill_databases as drill_databases  # noqa: PLC0414
from test_workflow_current_head_baseline import (
    test_actual_current032g_workflow_completes_with_readonly_graph_checks as _original_baseline,
)

from tests.sql_probes.rd_native_admission_trial import (
    native_admission_trial,
    report_trial_counts,
)

pytestmark = pytest.mark.postgres


def test_actual_current032g_workflow_with_ticketed_admission_trial(
    drill_databases, tmp_path, monkeypatch
):
    if os.environ.get("MINEAGENT_TICKETED_NATIVE_TRIAL_REQUIRED") != "1":
        pytest.fail("Explicit isolated native scheduling trial required", pytrace=False)
    counts = None
    try:
        with native_admission_trial() as counts:
            _original_baseline(drill_databases, tmp_path, monkeypatch)
    finally:
        report_trial_counts(counts)
    if not all(
        counts[name] > 0 for name in ("claims", "neutral_commits", "pulse_returns")
    ):
        pytest.fail("Native scheduling coverage incomplete", pytrace=False)

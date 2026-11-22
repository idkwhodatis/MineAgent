"""Isolated current032g finite bridge and cold restore with bounded owner locking."""

import os

import pytest
import test_workflow_current_head_baseline as baseline
from test_backup_restore import drill_databases as drill_databases  # noqa: PLC0414
from test_workflow_owner_first_heartbeat import (
    test_actual_owner_first_pulse_waits_before_row_locks_and_times_out_closed as _forward,
)
from workflow_structured_stage_fixture import assert_restore, run_bridge

from tests.sql_probes.rd_native_admission_trial import native_admission_trial

pytestmark = pytest.mark.postgres


def _require_fixture():
    if (
        os.environ.get("MINEAGENT_OWNER_ONLY_COMPOSED_REQUIRED") != "1"
        or os.environ.get("DATA_PROVIDER_MODE") != "fixture"
        or os.environ.get("MODEL_PROFILES_ENABLED") != "false"
        or os.environ.get("E2E_LIVE_PROVIDERS") != "0"
    ):
        pytest.fail("Explicit isolated owner-only fixture required", pytrace=False)


def test_owner_only_forward_lock_and_expiry(drill_databases, tmp_path, monkeypatch):
    _require_fixture()
    _forward(drill_databases, tmp_path, monkeypatch, owner_only=True)


def test_actual_current032g_owner_only_workflow_and_cold_restore(
    drill_databases, tmp_path, monkeypatch, record_property
):
    _require_fixture()

    def bridge_and_restore(env, source, root):
        with native_admission_trial(owner_only=True) as counts:
            completed = run_bridge(env, source, root)
        assert counts["claims"] == 1
        assert counts["neutral_commits"] == 0
        assert 0 < counts["owner_first_pulses"] <= counts["pulse_returns"]
        record_property("owner_only_bridge", "passed")
        call_counts = (len(env.rd_calls), len(env.oe_calls))
        assert_restore(
            drill_databases,
            root,
            source,
            env,
            completed,
            expected_revision="032g_schema_keyset_cost",
        )
        assert (len(env.rd_calls), len(env.oe_calls)) == call_counts
        record_property("owner_only_cold_restore", "passed")
        return completed

    # Preserve original pre-admission controls and all original post-bridge
    # read-only checks. The variant covers only the actual bridge.
    monkeypatch.setattr(baseline, "run_bridge", bridge_and_restore)
    baseline.test_actual_current032g_workflow_completes_with_readonly_graph_checks(
        drill_databases, tmp_path, monkeypatch
    )

"""Execute the reconciler's exact schema/family dispatch, not source-text probes."""

from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from backend.app.core.db import REQUIRED_SCHEMA_REVISION
from workers.research import dispatcher, queue_recovery


@pytest.mark.parametrize(
    "revisions,expected",
    [
        ([], [{}]),
        (["022_factor_analysis"], [{}]),
        (["023_rd_submissions"], [{}, {"rd": True}]),
        (["027c_seeded_controller_v4"], [{}, {"rd": True}]),
        (["033_unknown"], [{}]),
        ([REQUIRED_SCHEMA_REVISION, "unknown"], [{}]),
        *[
            ([revision], [{}, {"rd": True}, {"oe": True}])
            for revision in queue_recovery.OE_REVISIONS
        ],
    ],
)
def test_reconciler_recovers_only_installed_queue_families(
    monkeypatch, revisions, expected
):
    session = NS(scalars=lambda query: NS(all=lambda: revisions))
    recover = MagicMock(return_value=1)
    monkeypatch.setattr(dispatcher, "recover_stalled_jobs", recover)
    assert queue_recovery.recover_stalled_research_jobs(session) == len(expected)
    assert [call.kwargs for call in recover.call_args_list] == expected
    assert all(call.args == (session,) for call in recover.call_args_list)


def test_required_head_includes_both_current_queue_families():
    assert REQUIRED_SCHEMA_REVISION in queue_recovery.RD_REVISIONS
    assert REQUIRED_SCHEMA_REVISION in queue_recovery.OE_REVISIONS


@pytest.mark.parametrize("flags", [{"rd": 1}, {"oe": 1}, {"rd": True, "oe": True}])
def test_ambiguous_family_rejected_before_queue_access(flags):
    session = MagicMock()
    with pytest.raises(ValueError, match="queue family"):
        dispatcher.recover_stalled_jobs(session, **flags)
    session.execute.assert_not_called()


@pytest.mark.parametrize(
    "flags,task",
    [
        ({}, "tradegrad.execute_run"),
        ({"rd": True}, "mineagent.execute_rd_run"),
        ({"oe": True}, "mineagent.execute_oe_structured_run"),
    ],
)
def test_each_family_selects_only_its_stale_jobs(flags, task):
    session = MagicMock()
    session.execute.return_value.mappings.return_value.all.return_value = []
    assert dispatcher.recover_stalled_jobs(session, **flags) == 0
    assert session.execute.call_args.args[1] == {"task": task, "seconds": 120}
    sql = str(session.execute.call_args.args[0])
    assert "j.status = 'doing'" in sql
    assert "w.last_heartbeat <" in sql and "j.worker_id IS NULL" in sql
    assert "FOR UPDATE OF j SKIP LOCKED" in sql

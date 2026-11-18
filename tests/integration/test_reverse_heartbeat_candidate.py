"""Isolated current032g timestamp-event experiment, NOT native acceptance."""

import os
from uuid import uuid4

import pytest
from rd_completed_graph_probe import _require_unchanged, _snapshot
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_backup_restore import drill_databases as drill_databases  # noqa: PLC0414
from test_workflow_heartbeat_admission_candidate import (
    candidate_workflow as candidate_workflow,  # noqa: PLC0414
)
from test_workflow_heartbeat_lock_order import _run_attempt

from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.services import heartbeat
from tests.sql_probes import reverse_heartbeat_candidate as candidate

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.parametrize(
        "candidate_workflow", ["032g_schema_keyset_cost"], indirect=True, ids=["032g"]
    ),
]


@pytest.fixture(autouse=True)
def explicit_candidate():
    if os.environ.get("MINEAGENT_REVERSE_HEARTBEAT_CANDIDATE_REQUIRED") != "1":
        pytest.fail("Explicit isolated SQL candidate required", pytrace=False)


def _owner(session, state):
    _try_neutral_lock(session, f"submit:{state.fence.owner_id}")


def _owner_busy(error):
    if (
        getattr(error.orig, "sqlstate", None) != "P0001"
        or getattr(getattr(error.orig, "diag", None), "message_primary", None)
        != "workflow owner is busy"
    ):
        raise AssertionError("Expected exact original owner contention guard") from None


def _timestamps(state):
    with state.source.connect() as connection:
        return connection.execute(
            text(
                "SELECT heartbeat_at,lease_expires_at FROM execution_attempts WHERE id=:id"
            ),
            {"id": state.fence.attempt_id},
        ).one()


def _restore_timestamps(state, values):
    with state.env.factory.begin() as session:
        _owner(session, state)
        _run_attempt(session, state.fence)
        session.execute(
            text(
                "UPDATE execution_attempts SET heartbeat_at=:heartbeat,lease_expires_at=:expiry WHERE id=:id"
            ),
            {"heartbeat": values[0], "expiry": values[1], "id": state.fence.attempt_id},
        )


def _pulse_transaction(state):
    with state.env.factory.begin() as session:
        assert (
            heartbeat(
                session,
                state.fence.attempt_id,
                state.fence.generation,
                state.fence.lease_token,
                state.env.settings,
            )
            is False
        )


def _baseline_contention(state):
    before = _snapshot(state.source)
    with state.env.factory() as holder, holder.begin():
        _owner(holder, state)
        with pytest.raises(DBAPIError) as caught:
            _pulse_transaction(state)
        _owner_busy(caught.value)
    _require_unchanged(state.source, before)


def test_candidate_pure_timestamp_commit_and_exact_original_restore(candidate_workflow):
    state = candidate_workflow
    before, times = _snapshot(state.source), _timestamps(state)
    _baseline_contention(state)
    try:
        with candidate.installed_candidate(state.source):
            with state.env.factory() as holder, holder.begin():
                _owner(holder, state)
                _pulse_transaction(state)
            candidate.check(state.source, True)
        _baseline_contention(state)
    finally:
        _restore_timestamps(state, times)
    _require_unchanged(state.source, before)


@pytest.mark.parametrize(
    "mode", ["token", "status", "revert", "immediate", "savepoint"]
)
def test_candidate_preserves_other_events_and_transaction_boundaries(
    candidate_workflow, mode
):
    state = candidate_workflow
    before, times = _snapshot(state.source), _timestamps(state)
    try:
        with candidate.installed_candidate(state.source):  # noqa: SIM117 -- restore after holder exits
            with state.env.factory() as holder, holder.begin():
                _owner(holder, state)

                def mutate():
                    with state.env.factory.begin() as writer:
                        nested = writer.begin_nested() if mode == "savepoint" else None
                        if mode == "status":
                            writer.execute(
                                text(
                                    "UPDATE execution_attempts SET status='finalizing' WHERE id=:id"
                                ),
                                {"id": state.fence.attempt_id},
                            )
                        else:
                            writer.execute(
                                text(
                                    "UPDATE execution_attempts SET lease_token=:token WHERE id=:id"
                                ),
                                {"token": uuid4(), "id": state.fence.attempt_id},
                            )
                        if mode == "immediate":
                            writer.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
                        if mode == "revert":
                            writer.execute(
                                text(
                                    "UPDATE execution_attempts SET lease_token=:token WHERE id=:id"
                                ),
                                {
                                    "token": state.fence.lease_token,
                                    "id": state.fence.attempt_id,
                                },
                            )
                        if nested is not None:
                            nested.rollback()
                        writer.execute(
                            text(
                                "UPDATE execution_attempts SET heartbeat_at=heartbeat_at+interval '1 microsecond' WHERE id=:id"
                            ),
                            {"id": state.fence.attempt_id},
                        )
                        if mode == "savepoint":
                            writer.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

                if mode == "savepoint":
                    mutate()
                else:
                    with pytest.raises(DBAPIError) as caught:
                        mutate()
                    _owner_busy(caught.value)
    finally:
        _restore_timestamps(state, times)
    _require_unchanged(state.source, before)


def test_candidate_never_bypasses_original_attempt_run_fk(candidate_workflow):
    state = candidate_workflow
    before = _snapshot(state.source)

    def corrupt():
        with pytest.raises(DBAPIError) as caught:  # noqa: SIM117 -- separate exception boundary
            with state.env.factory.begin() as writer:
                _owner(writer, state)
                _run_attempt(writer, state.fence)
                writer.execute(
                    text(
                        "UPDATE execution_attempts SET run_id=:run,heartbeat_at=heartbeat_at+interval '1 microsecond' WHERE id=:id"
                    ),
                    {"run": uuid4(), "id": state.fence.attempt_id},
                )
        error = caught.value.orig
        # Exact original FK identity, never broad P0001 or raw error output.
        if (
            getattr(error, "sqlstate", None) != "23503"
            or error.diag.constraint_name != "execution_attempts_run_id_fkey"
        ):
            raise AssertionError(
                "Expected original attempt-run foreign key rejection"
            ) from None

    corrupt()
    with candidate.installed_candidate(state.source):
        corrupt()
    corrupt()
    _require_unchanged(state.source, before)

"""Draft peer-assignment checks: mocked service wiring, never auth acceptance."""

from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable

from backend.app import openevolve_worker_models as models
from backend.app.openevolve_worker_services import (
    bind_started_worker,
    close_worker_assignment,
)
from backend.app.services import DomainError


def test_worker_tables_compile_with_only_known_epoch_unique_pending():
    for model in (
        models.SearchWorkerTicket,
        models.SearchWorkerExpectedIdentity,
        models.SearchWorkerAssignment,
        models.SearchWorkerAssignmentClosure,
    ):
        table = model.__table__
        assert "FOREIGN KEY" in str(CreateTable(table).compile(dialect=dialect()))
        for constraint in table.foreign_key_constraints:
            target = next(iter(constraint.elements)).column.table
            columns = tuple(x.column.name for x in constraint.elements)
            keys = [tuple(c.name for c in target.primary_key.columns)] + [
                tuple(c.name for c in key.columns)
                for key in target.constraints
                if isinstance(key, UniqueConstraint)
            ]
            assert columns in keys or (
                target.name == "strategy_search_epochs"
                and columns
                == (
                    "owner_id",
                    "run_id",
                    "attempt_id",
                    "generation",
                    "id",
                    "admission_id",
                )
            )


def fixture():
    fence = SimpleNamespace(
        owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1
    )
    epoch = SimpleNamespace(id=uuid4(), admission_id=uuid4())
    intent = SimpleNamespace(id=uuid4(), native_iteration=1)
    ticket, expected = SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())
    observed = {
        "host_pid": 41,
        "start_ticks": 543,
        "pid_namespace": "pid:[123]",
        "cgroup": "0::/unit",
    }
    return fence, (epoch, intent, ticket, expected, observed)


def test_open_assignment_blocks_reusing_pool_worker_for_next_intent():
    fence, binding = fixture()
    session = Mock()
    session.scalar.side_effect = [None, None]
    session.scalars.return_value.all.return_value = [SimpleNamespace(id=uuid4())]
    with (
        patch(
            "backend.app.openevolve_worker_services._live_worker", return_value=binding
        ),
        pytest.raises(DomainError, match="remains open"),
    ):
        bind_started_worker(
            session, fence, object(), object(), expected_process_profile="fixed"
        )
    session.add.assert_not_called()


def test_exact_observed_assignment_is_created_only_after_prior_closure():
    fence, binding = fixture()
    session = Mock()
    session.scalar.side_effect = [None, SimpleNamespace(id=uuid4())]
    prior = SimpleNamespace(id=uuid4(), native_iteration=1)
    binding[1].native_iteration = 2
    session.scalars.side_effect = [
        SimpleNamespace(all=lambda: [SimpleNamespace(id=uuid4(), intent_id=prior.id)]),
        SimpleNamespace(all=lambda: [prior]),
    ]
    with patch(
        "backend.app.openevolve_worker_services._live_worker", return_value=binding
    ):
        row = bind_started_worker(
            session, fence, object(), object(), expected_process_profile="fixed"
        )
    assert row.expected_identity_id == binding[3].id
    assert row.start_ticks == binding[4]["start_ticks"]
    session.commit.assert_not_called()


@pytest.mark.parametrize("fault", ["peer", "closed", "expected"])
def test_started_replay_cannot_change_or_reopen_assignment(fault):
    fence, binding = fixture()
    row = SimpleNamespace(
        id=uuid4(),
        ticket_id=binding[2].id,
        expected_identity_id=binding[3].id,
        **binding[4],
    )
    if fault == "peer":
        row.start_ticks += 1
    if fault == "expected":
        row.expected_identity_id = uuid4()
    session = Mock()
    session.scalar.side_effect = [
        row,
        SimpleNamespace(id=uuid4()) if fault == "closed" else None,
    ]
    with (
        patch(
            "backend.app.openevolve_worker_services._live_worker", return_value=binding
        ),
        pytest.raises(DomainError),
    ):
        bind_started_worker(
            session, fence, object(), object(), expected_process_profile="fixed"
        )
    session.add.assert_not_called()


def test_exact_assignment_closure_is_append_only_and_replay_has_no_side_effect():
    fence, binding = fixture()
    epoch, intent, ticket, expected, observed = binding
    assignment = SimpleNamespace(
        id=uuid4(), ticket_id=ticket.id, expected_identity_id=expected.id, **observed
    )
    outcome = SimpleNamespace(id=uuid4(), epoch_id=epoch.id, intent_id=intent.id)
    session = Mock()
    session.scalar.side_effect = [assignment, None]
    with (
        patch(
            "backend.app.openevolve_worker_services._live_worker", return_value=binding
        ),
        patch(
            "backend.app.openevolve_worker_services._owned_row", return_value=outcome
        ),
    ):
        closure = close_worker_assignment(
            session,
            fence,
            object(),
            object(),
            worker_outcome_id=outcome.id,
            expected_process_profile="fixed",
        )
        session.reset_mock()
        session.scalar.side_effect = [assignment, closure]
        assert (
            close_worker_assignment(
                session,
                fence,
                object(),
                object(),
                worker_outcome_id=outcome.id,
                expected_process_profile="fixed",
            )
            is closure
        )
    session.add.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.parametrize(
    "fault", ["missing_expected", "wrong_worker", "ticket", "intent"]
)
def test_live_peer_and_ticket_cannot_substitute_expected_worker_placement(fault):
    import hashlib

    from backend.app.openevolve_worker_services import _live_worker
    from packages.openevolve_adapter.worker_binding_contracts import IterationGrant

    fence, (epoch, intent, ticket, expected, observed) = fixture()
    admission = SimpleNamespace(
        id=epoch.admission_id, logical_operation_id=uuid4(), profile="fixed"
    )
    intent.epoch_id = epoch.id
    intent.parent_candidate_id = uuid4()
    intent.parent_source_sha256, intent.selection_sha256 = "b" * 64, "c" * 64
    grant = IterationGrant(
        schema_version="mineagent.openevolve-iteration-grant.v1",
        admission_id=admission.id,
        epoch_id=epoch.id,
        intent_id=intent.id,
        intent_ticket="a" * 64,
        native_iteration=1,
        parent_candidate_id=intent.parent_candidate_id,
        parent_source_sha256=intent.parent_source_sha256,
        selection_sha256=intent.selection_sha256,
    )
    ticket = SimpleNamespace(
        id=ticket.id,
        **vars(fence),
        epoch_id=epoch.id,
        admission_id=admission.id,
        ticket_sha256=hashlib.sha256(("a" * 64).encode()).hexdigest(),
    )
    expected = SimpleNamespace(
        id=expected.id,
        **vars(fence),
        epoch_id=epoch.id,
        admission_id=admission.id,
        ticket_id=ticket.id,
        **observed,
    )
    if fault == "missing_expected":
        expected = None
    elif fault == "wrong_worker":
        expected.start_ticks += 1
    elif fault == "ticket":
        ticket.ticket_sha256 = "0" * 64
    else:
        intent.native_iteration = 2
    session = Mock()
    session.scalar.side_effect = [ticket, expected]
    peer = SimpleNamespace(peer=observed, leader={**observed, "host_pid": 9})
    with (
        patch("backend.app.openevolve_worker_services.require_neutral_transaction"),
        patch(
            "backend.app.openevolve_worker_services._locked_epoch", return_value=epoch
        ),
        patch(
            "backend.app.openevolve_worker_services._owned_row",
            side_effect=[admission, intent],
        ),
        patch(
            "backend.app.openevolve_worker_services.require_running_peer",
            return_value=admission,
        ),
        patch(
            "backend.app.openevolve_worker_services.require_authenticated_peer",
            return_value=peer,
        ),
        pytest.raises(DomainError),
    ):
        _live_worker(session, fence, object(), grant, "fixed")
    session.add.assert_not_called()


def test_worker_cannot_skip_unstarted_prior_charged_intent():
    fence, binding = fixture()
    binding[1].native_iteration = 2
    prior = SimpleNamespace(id=uuid4(), native_iteration=1)
    session = Mock()
    session.scalar.return_value = None
    session.scalars.side_effect = [
        SimpleNamespace(all=list),
        SimpleNamespace(all=lambda: [prior]),
    ]
    with (
        patch(
            "backend.app.openevolve_worker_services._live_worker", return_value=binding
        ),
        pytest.raises(DomainError, match="ordered assignment"),
    ):
        bind_started_worker(
            session, fence, object(), object(), expected_process_profile="fixed"
        )
    session.add.assert_not_called()

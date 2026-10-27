"""Unregistered SQL/service wiring; not PostgreSQL or socket acceptance."""

from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable
from test_checkpoint_native import native as native_fixture
from test_event_projection import envelope, wire


@pytest.fixture
def native(tmp_path):
    return native_fixture.__wrapped__(tmp_path)


from backend.app.openevolve_event_models import SearchControllerEventReceipt
from backend.app.openevolve_event_services import record_controller_event
from backend.app.services import DomainError
from packages.openevolve_adapter.event_projection import (
    evidence_projections,
    parse_controller_event,
    terminal_command,
)
from packages.openevolve_adapter.host_contracts import EvidenceRef
from packages.research_runtime.broker_contracts import digest


def test_receipt_schema_has_complete_nondeferrable_target_keys():
    table = SearchControllerEventReceipt.__table__
    assert "FOREIGN KEY" in str(CreateTable(table).compile(dialect=dialect()))
    missing = []
    for fk in table.foreign_key_constraints:
        target = next(iter(fk.elements)).column.table
        columns = tuple(x.column.name for x in fk.elements)
        keys = [tuple(c.name for c in target.primary_key.columns)] + [
            tuple(c.name for c in constraint.columns)
            for constraint in target.constraints
            if isinstance(constraint, UniqueConstraint) and not constraint.deferrable
        ]
        if columns not in keys:
            missing.append((target.name, columns))
    assert missing == []
    assert any(
        fk.elements[0].column.table.name == "strategy_search_epochs"
        and tuple(e.column.name for e in fk.elements)
        == ("owner_id", "run_id", "attempt_id", "generation", "id", "admission_id")
        for fk in table.foreign_key_constraints
    )


def fixture(native):
    event = envelope(native)
    parsed = parse_controller_event(wire(event))
    (state,) = evidence_projections(parsed)
    command = terminal_command(
        parsed,
        state_ref=EvidenceRef(id=uuid4(), sha256=state.sha256),
        seed_objective_ack_id=uuid4(),
    )
    event_ref = EvidenceRef(id=uuid4(), sha256=parsed.event_sha256)
    fence = SimpleNamespace(
        owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1
    )
    return event, parsed, command, event_ref, fence


@pytest.mark.parametrize(
    "conflict", [None, "command", "event", "object", "admission", "owner"]
)
def test_exact_replay_revalidates_peer_but_never_reapplies_domain(native, conflict):
    event, parsed, command, event_ref, fence = fixture(native)
    previous = SimpleNamespace(
        **vars(fence),
        admission_id=parsed.event.admission_id,
        event_sha256=parsed.event_sha256,
        command_sha256="0" * 64 if conflict == "command" else digest(command),
        event_object_id=event_ref.id,
    )
    if conflict == "event":
        previous.event_sha256 = "0" * 64
    elif conflict == "object":
        previous.event_object_id = uuid4()
    elif conflict == "admission":
        previous.admission_id = uuid4()
    elif conflict == "owner":
        previous.owner_id = uuid4()
    session, authorization = Mock(), object()
    session.scalar.return_value = previous
    with (
        patch(
            "backend.app.openevolve_event_services.inspect_authenticated_controller_event",
            return_value=parsed,
        ) as auth,
        patch(
            "backend.app.openevolve_event_services._read_evidence", return_value=event
        ),
        patch(
            "backend.app.openevolve_event_services.acknowledge_terminal_application"
        ) as mutate,
    ):
        if conflict:
            with pytest.raises(DomainError):
                record_controller_event(
                    session,
                    fence,
                    authorization,
                    wire(event),
                    expected_process_profile="fixed",
                    event_reference=event_ref,
                    command=command,
                    settings=object(),
                )
        else:
            assert (
                record_controller_event(
                    session,
                    fence,
                    authorization,
                    wire(event),
                    expected_process_profile="fixed",
                    event_reference=event_ref,
                    command=command,
                    settings=object(),
                )
                is previous
            )
        auth.assert_called_once()
        mutate.assert_not_called()
    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_new_terminal_receipt_is_in_same_flush_only_boundary(native):
    event, parsed, command, event_ref, fence = fixture(native)
    terminal = SimpleNamespace(id=uuid4())
    control = SimpleNamespace(
        id=uuid4(),
        **vars(fence),
        epoch_id=parsed.event.epoch_id,
        kind="seed",
        sampling_intent_id=None,
        terminal_id=terminal.id,
        sequence=1,
        frontier=0,
        state_sha256=command.state.sha256,
    )
    session = Mock()
    session.scalar.side_effect = [None, control]
    with (
        patch(
            "backend.app.openevolve_event_services.inspect_authenticated_controller_event",
            return_value=parsed,
        ),
        patch(
            "backend.app.openevolve_event_services._read_evidence", return_value=event
        ),
        patch(
            "backend.app.openevolve_event_services.acknowledge_terminal_application",
            return_value=terminal,
        ) as mutate,
    ):
        receipt = record_controller_event(
            session,
            fence,
            object(),
            wire(event),
            expected_process_profile="fixed",
            event_reference=event_ref,
            command=command,
            settings=object(),
        )
        mutate.assert_called_once()
    assert receipt.control_id == control.id
    assert receipt.terminal_id == terminal.id
    assert receipt.event_sha256 == parsed.event_sha256
    session.add.assert_called_once_with(receipt)
    session.flush.assert_called_once()
    session.commit.assert_not_called()


def test_sampling_receipt_binds_same_atomic_intent_and_control(native):
    from test_event_projection import sampling_envelope

    from packages.openevolve_adapter.event_projection import sampling_command

    event = sampling_envelope(native)
    parsed = parse_controller_event(wire(event))
    selection, state = evidence_projections(parsed)
    command = sampling_command(
        parsed,
        parent_candidate_id=uuid4(),
        parent_source_sha256="c" * 64,
        mutation_reservation_id=uuid4(),
        selection_ref=EvidenceRef(id=uuid4(), sha256=selection.sha256),
        state_ref=EvidenceRef(id=uuid4(), sha256=state.sha256),
    )
    fence = SimpleNamespace(
        owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1
    )
    intent = SimpleNamespace(id=uuid4())
    control = SimpleNamespace(
        id=uuid4(),
        **vars(fence),
        epoch_id=parsed.event.epoch_id,
        kind="sampling",
        sampling_intent_id=intent.id,
        terminal_id=None,
        sequence=2,
        frontier=0,
        state_sha256=state.sha256,
    )
    session = Mock()
    session.scalar.return_value = None
    with (
        patch(
            "backend.app.openevolve_event_services.inspect_authenticated_controller_event",
            return_value=parsed,
        ),
        patch(
            "backend.app.openevolve_event_services._read_evidence", return_value=event
        ),
        patch(
            "backend.app.openevolve_event_services.register_sampling_intent",
            return_value=(intent, control),
        ) as mutate,
    ):
        receipt = record_controller_event(
            session,
            fence,
            object(),
            wire(event),
            expected_process_profile="fixed",
            event_reference=EvidenceRef(id=uuid4(), sha256=parsed.event_sha256),
            command=command,
            settings=object(),
        )
    assert receipt.intent_id == intent.id
    assert receipt.control_id == control.id
    assert receipt.native_iteration == 1 and receipt.control_frontier == 0
    mutate.assert_called_once()
    session.commit.assert_not_called()

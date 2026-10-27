"""Read-side snapshot binding rejects substituted event/worker evidence."""

import copy
import hashlib
from types import SimpleNamespace as Row
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

import pytest
from test_checkpoint_native import native as native_fixture
from test_event_projection import envelope
from test_worker_issuance import fixture as worker_fixture

from backend.app.openevolve_checkpoint_events import validate_checkpoint_event_objects
from backend.app.services import DomainError
from packages.openevolve_adapter.host_contracts import (
    EvidenceRef,
    IterationIntentCommand,
    SamplingIntentCommand,
    TerminalCommand,
)
from packages.openevolve_adapter.observer_transport import canonical
from packages.research_runtime.broker_contracts import digest


@pytest.fixture
def native(tmp_path):
    return native_fixture.__wrapped__(tmp_path)


@pytest.mark.parametrize("tampered", [False, True])
def test_checkpoint_event_must_equal_acknowledged_population(native, tampered):
    event = envelope(native)
    state = copy.deepcopy(event["payload"]["native_state"])
    epoch = Row(id=UUID(event["epoch_id"]), admission_id=UUID(event["admission_id"]))
    control = Row(
        id=uuid4(),
        frontier=0,
        state_object_id=uuid4(),
        state_sha256=event["payload"]["state_sha256"],
    )
    terminal = Row(
        id=uuid4(),
        frontier=0,
        outcome="seed",
        state_object_id=control.state_object_id,
        state_sha256=control.state_sha256,
        worker_outcome_id=None,
        seed_objective_ack_id=uuid4(),
    )
    control.request_sha256 = digest(
        TerminalCommand(
            epoch_id=epoch.id,
            frontier=0,
            outcome="seed",
            state=EvidenceRef(
                id=terminal.state_object_id, sha256=terminal.state_sha256
            ),
            seed_objective_ack_id=terminal.seed_objective_ack_id,
        )
    )
    if tampered:
        program = next(iter(event["payload"]["native_state"]["programs"].values()))
        program["metadata"]["invented"] = True
    receipt = Row(
        id=uuid4(),
        event_object_id=uuid4(),
        event_sha256=hashlib.sha256(canonical(event)).hexdigest(),
        event_kind="terminal_applied",
        control_id=control.id,
        terminal_id=terminal.id,
        command_sha256=control.request_sha256,
        native_iteration=0,
    )
    rows = {
        "control_transition_ids": [control],
        "intent_ids": [],
        "terminal_ids": [terminal],
        "controller_event_receipt_ids": [receipt],
        "worker_expected_identity_ids": [],
    }
    with (
        patch("backend.app.openevolve_services._read_evidence", return_value=event),
        patch(
            "backend.app.openevolve_services._validated_population", return_value=state
        ),
    ):
        if tampered:
            with pytest.raises(DomainError):
                validate_checkpoint_event_objects(
                    Mock(), object(), epoch, rows, object()
                )
        else:
            validate_checkpoint_event_objects(Mock(), object(), epoch, rows, object())


@pytest.mark.parametrize(
    "fault", [None, "kernel", "event_hash", "scope", "object_hash"]
)
def test_checkpoint_worker_identity_binds_exact_stored_scope_and_placement(fault):
    fence, epoch, intent, ticket, _, _, evidence, ref = worker_fixture()
    expected = Row(
        id=uuid4(),
        intent_id=intent.id,
        ticket_id=ticket.id,
        evidence_object_id=ref.id,
        evidence_sha256=ref.sha256,
        **{
            k: evidence["worker"][k]
            for k in ["host_pid", "start_ticks", "pid_namespace", "cgroup"]
        },
    )
    if fault == "kernel":
        expected.host_pid += 1
    elif fault == "event_hash":
        evidence["placement_event_sha256"] = "0" * 64
    elif fault == "scope":
        evidence["generation"] = 2
    elif fault == "object_hash":
        expected.evidence_sha256 = "0" * 64
    rows = {
        "control_transition_ids": [],
        "intent_ids": [],
        "terminal_ids": [],
        "controller_event_receipt_ids": [],
        "worker_expected_identity_ids": [expected],
    }
    with patch("backend.app.openevolve_services._read_evidence", return_value=evidence):
        if fault:
            with pytest.raises(DomainError):
                validate_checkpoint_event_objects(Mock(), fence, epoch, rows, object())
        else:
            validate_checkpoint_event_objects(Mock(), fence, epoch, rows, object())


@pytest.mark.parametrize(
    "fault", [None, "before_cursor", "selection", "reservation", "command_digest"]
)
def test_sampling_checkpoint_object_binds_previous_cursor_and_exact_snapshot(
    native, fault
):
    from test_sampling_contracts import transition

    from packages.openevolve_adapter.checkpoint_native import state_sha256

    before, after, parent, _ = transition(native)
    epoch = Row(id=uuid4(), admission_id=uuid4())
    prior = Row(
        id=uuid4(),
        frontier=0,
        state_object_id=uuid4(),
        state_sha256=state_sha256(before),
    )
    current = Row(
        id=uuid4(),
        frontier=0,
        state_object_id=uuid4(),
        state_sha256=state_sha256(after),
        previous_id=prior.id,
    )
    snapshot = {
        "programs": before["programs"],
        "islands": [[parent]],
        "current_island": 0,
        "sampling_island": 0,
        "feature_dimensions": ["complexity", "diversity"],
        "artifacts": {},
    }
    selection = {
        "schema_version": "mineagent.native-selection.v1",
        "native_iteration": 1,
        "parent_id": parent,
        "inspiration_ids": [],
        "snapshot": snapshot,
    }
    intent = Row(
        id=uuid4(),
        selection_object_id=uuid4(),
        selection_sha256=hashlib.sha256(canonical(selection)).hexdigest(),
    )
    intent.native_iteration = 1
    intent.parent_candidate_id = uuid4()
    intent.parent_source_sha256 = "c" * 64
    intent.mutation_reservation_id = uuid4()
    current.request_sha256 = digest(
        SamplingIntentCommand(
            intent=IterationIntentCommand(
                epoch_id=epoch.id,
                native_iteration=1,
                parent_candidate_id=intent.parent_candidate_id,
                parent_source_sha256=intent.parent_source_sha256,
                mutation_reservation_id=intent.mutation_reservation_id,
                selection=EvidenceRef(
                    id=intent.selection_object_id, sha256=intent.selection_sha256
                ),
            ),
            before_state_sha256=prior.state_sha256,
            after_state=EvidenceRef(
                id=current.state_object_id, sha256=current.state_sha256
            ),
        )
    )
    payload = {
        "iteration": 1,
        "parent_id": parent,
        "inspiration_ids": [],
        "snapshot": copy.deepcopy(snapshot),
        "sampling": {
            "schema_version": "mineagent.native-sampling-transition.v1",
            "before_state_sha256": prior.state_sha256,
            "after_state": after,
        },
    }
    if fault == "before_cursor":
        payload["sampling"]["before_state_sha256"] = "0" * 64
    elif fault == "selection":
        payload["snapshot"]["artifacts"] = {"invented": "feedback"}
    event = {
        "schema_version": "mineagent.openevolve-event.v1",
        "admission_id": str(epoch.admission_id),
        "epoch_id": str(epoch.id),
        "kind": "iteration_intent",
        "payload": payload,
    }
    receipt = Row(
        id=uuid4(),
        event_object_id=uuid4(),
        event_sha256=hashlib.sha256(canonical(event)).hexdigest(),
        event_kind="iteration_intent",
        control_id=current.id,
        command_sha256=current.request_sha256,
        intent_id=intent.id,
        native_iteration=1,
    )
    if fault == "reservation":
        intent.mutation_reservation_id = uuid4()
    elif fault == "command_digest":
        receipt.command_sha256 = "0" * 64
    rows = {
        "control_transition_ids": [prior, current],
        "intent_ids": [intent],
        "terminal_ids": [],
        "controller_event_receipt_ids": [receipt],
        "worker_expected_identity_ids": [],
    }

    def read(session, fence, ref, kind, settings):
        return event if kind == "search_controller_event" else selection

    def population(session, fence, epoch_id, frontier, ref, settings):
        return before if ref.id == prior.state_object_id else after

    with (
        patch("backend.app.openevolve_services._read_evidence", side_effect=read),
        patch(
            "backend.app.openevolve_services._validated_population",
            side_effect=population,
        ),
    ):
        if fault:
            with pytest.raises(DomainError):
                validate_checkpoint_event_objects(
                    Mock(), object(), epoch, rows, object()
                )
        else:
            validate_checkpoint_event_objects(Mock(), object(), epoch, rows, object())


@pytest.mark.parametrize("fault", [None, "outcome", "command_digest", "control_digest"])
def test_same_population_cannot_hide_different_terminal_semantics(native, fault):
    from packages.openevolve_adapter.checkpoint_native import state_sha256

    event = envelope(native)
    state = event["payload"]["native_state"]
    state["metadata"]["last_iteration"] = 1
    event["payload"].update(
        iteration=1, outcome="error", state_sha256=state_sha256(state)
    )
    epoch = Row(id=UUID(event["epoch_id"]), admission_id=UUID(event["admission_id"]))
    terminal = Row(
        id=uuid4(),
        frontier=1,
        outcome="rejected" if fault == "outcome" else "error",
        state_object_id=uuid4(),
        state_sha256=state_sha256(state),
        worker_outcome_id=uuid4(),
        seed_objective_ack_id=None,
    )
    command = TerminalCommand(
        epoch_id=epoch.id,
        frontier=1,
        outcome=terminal.outcome,
        state=EvidenceRef(id=terminal.state_object_id, sha256=terminal.state_sha256),
        worker_outcome_id=terminal.worker_outcome_id,
    )
    control = Row(
        id=uuid4(),
        frontier=1,
        state_object_id=terminal.state_object_id,
        state_sha256=terminal.state_sha256,
        request_sha256="0" * 64 if fault == "control_digest" else digest(command),
    )
    receipt = Row(
        id=uuid4(),
        event_object_id=uuid4(),
        event_sha256=hashlib.sha256(canonical(event)).hexdigest(),
        event_kind="terminal_applied",
        control_id=control.id,
        terminal_id=terminal.id,
        native_iteration=1,
        command_sha256="0" * 64 if fault == "command_digest" else digest(command),
    )
    rows = {
        "control_transition_ids": [control],
        "intent_ids": [],
        "terminal_ids": [terminal],
        "controller_event_receipt_ids": [receipt],
        "worker_expected_identity_ids": [],
    }
    with (
        patch("backend.app.openevolve_services._read_evidence", return_value=event),
        patch(
            "backend.app.openevolve_services._validated_population", return_value=state
        ),
    ):
        if fault:
            with pytest.raises(DomainError):
                validate_checkpoint_event_objects(
                    Mock(), object(), epoch, rows, object()
                )
        else:
            validate_checkpoint_event_objects(Mock(), object(), epoch, rows, object())

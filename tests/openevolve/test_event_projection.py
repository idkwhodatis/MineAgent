"""Projection tests do not grant socket, process or SQL authority."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from test_checkpoint_native import native as native_fixture

from packages.openevolve_adapter.checkpoint_native import (
    state_sha256,
    validate_directory,
)
from packages.openevolve_adapter.event_projection import (
    evidence_projections,
    parse_controller_event,
    terminal_command,
)
from packages.openevolve_adapter.host_contracts import EvidenceRef
from packages.openevolve_adapter.observer_transport import canonical


@pytest.fixture
def native(tmp_path):
    return native_fixture.__wrapped__(tmp_path)


def envelope(native):
    state = validate_directory(native)
    return {
        "schema_version": "mineagent.openevolve-event.v1",
        "admission_id": str(uuid4()),
        "epoch_id": str(uuid4()),
        "kind": "terminal_applied",
        "payload": {
            "iteration": 0,
            "outcome": "seed",
            "native_state": state,
            "state_sha256": state_sha256(state),
        },
    }


def wire(event):
    return canonical(
        {
            "event": event,
            "event_sha256": hashlib.sha256(canonical(event)).hexdigest(),
            "process_ticket": "a" * 64,
        }
    )


def test_terminal_evidence_is_exact_and_requires_host_resolved_ack(native):
    event = parse_controller_event(wire(envelope(native)))
    (projection,) = evidence_projections(event)
    ref = EvidenceRef(id=uuid4(), sha256=projection.sha256)
    ack = uuid4()
    command = terminal_command(event, state_ref=ref, seed_objective_ack_id=ack)
    assert command.seed_objective_ack_id == ack
    assert command.state.sha256 == event.payload.state_sha256
    assert "a" * 64 not in repr(event)
    with pytest.raises(ValueError):
        terminal_command(
            event,
            state_ref=EvidenceRef(id=uuid4(), sha256="0" * 64),
            seed_objective_ack_id=ack,
        )


@pytest.mark.parametrize(
    "fault", ["bool", "unknown", "digest", "future", "resume", "uuid_alias"]
)
def test_event_rejects_ambiguous_or_unsupported_payload(native, fault):
    event = envelope(native)
    if fault == "bool":
        event["payload"]["iteration"] = False
    elif fault == "unknown":
        event["payload"]["unknown"] = 1
    elif fault == "digest":
        event["payload"]["state_sha256"] = "0" * 64
    elif fault == "future":
        event["payload"]["iteration"] = 1
    elif fault == "resume":
        event["kind"] = "checkpoint_load_prepare"
    else:
        event["epoch_id"] = event["epoch_id"].upper()
    with pytest.raises(ValueError):
        parse_controller_event(wire(event))


def test_duplicate_json_and_ticket_tampering_rejected(native):
    raw = wire(envelope(native))
    with pytest.raises(ValueError):
        parse_controller_event(raw[:-1] + b',"event_sha256":"' + b"0" * 64 + b'"}')
    value = json.loads(raw)
    value["event_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        parse_controller_event(canonical(value))
    value["process_ticket"] = True
    with pytest.raises(ValueError):
        parse_controller_event(canonical(value))


@pytest.mark.parametrize("descendant", [False, True])
def test_resolver_rechecks_real_peer_api_and_does_not_mutate_or_commit(
    native, descendant
):
    from backend.app.openevolve_event_projection import (
        inspect_authenticated_controller_event,
    )
    from backend.app.services import DomainError

    event = envelope(native)
    from uuid import UUID

    epoch = SimpleNamespace(
        admission_id=UUID(event["admission_id"]), bootstrap_sha256="b" * 64
    )
    admission = SimpleNamespace(
        id=epoch.admission_id,
        logical_operation_id=uuid4(),
        image_id="sha256:" + "c" * 64,
        profile="fixed-host-profile",
        ticket_sha256=hashlib.sha256(("a" * 64).encode()).hexdigest(),
    )
    session, fence, authorization = Mock(), object(), object()
    session.get.return_value = admission
    with (
        patch(
            "backend.app.openevolve_event_projection.require_neutral_transaction"
        ) as locked,
        patch(
            "backend.app.openevolve_event_projection._locked_epoch", return_value=epoch
        ),
        patch(
            "backend.app.openevolve_event_projection.require_controller_peer",
            return_value=admission,
            side_effect=DomainError("peer", "descendant", 409) if descendant else None,
        ) as running,
    ):
        if descendant:
            with pytest.raises(DomainError):
                inspect_authenticated_controller_event(
                    session,
                    fence,
                    authorization,
                    wire(event),
                    expected_process_profile=admission.profile,
                )
        else:
            inspect_authenticated_controller_event(
                session,
                fence,
                authorization,
                wire(event),
                expected_process_profile=admission.profile,
            )
        locked.assert_called_once_with(session, fence)
        running.assert_called_once()
        assert running.call_args.args == (session, fence, authorization)
        binding = running.call_args.kwargs["binding"]
        assert binding.admission_id == epoch.admission_id
        assert binding.bootstrap_sha256 == epoch.bootstrap_sha256
        assert binding.image_id == admission.image_id
        assert binding.role == "openevolve_orchestrator"
    session.add.assert_not_called()
    session.commit.assert_not_called()


def sampling_envelope(native):
    event = envelope(native)
    state = event["payload"]["native_state"]
    parent = next(iter(state["programs"]))
    event["kind"] = "iteration_intent"
    event["payload"] = {
        "iteration": 1,
        "parent_id": parent,
        "inspiration_ids": [],
        "snapshot": {
            "programs": state["programs"],
            "islands": state["metadata"]["islands"],
            "current_island": 0,
            "sampling_island": 0,
            "feature_dimensions": ["complexity", "diversity"],
            "artifacts": {},
        },
        "sampling": {
            "schema_version": "mineagent.native-sampling-transition.v1",
            "before_state_sha256": "b" * 64,
            "after_state": state,
        },
    }
    return event


def test_sampling_projection_preserves_observed_order_and_binds_object_hashes(native):
    from packages.openevolve_adapter.event_projection import sampling_command

    parsed = parse_controller_event(wire(sampling_envelope(native)))
    selection, state = evidence_projections(parsed)
    parent, reservation = uuid4(), uuid4()
    command = sampling_command(
        parsed,
        parent_candidate_id=parent,
        parent_source_sha256="c" * 64,
        mutation_reservation_id=reservation,
        selection_ref=EvidenceRef(id=uuid4(), sha256=selection.sha256),
        state_ref=EvidenceRef(id=uuid4(), sha256=state.sha256),
    )
    assert command.intent.parent_candidate_id == parent
    assert command.intent.mutation_reservation_id == reservation
    assert json.loads(selection.content)["snapshot"] == parsed.payload.snapshot
    assert command.before_state_sha256 == "b" * 64


@pytest.mark.parametrize(
    "fault",
    ["unknown_snapshot", "bool_island", "foreign_program", "duplicate_inspiration"],
)
def test_sampling_projection_rejects_nonactual_snapshot(native, fault):
    event = sampling_envelope(native)
    payload = event["payload"]
    if fault == "unknown_snapshot":
        payload["snapshot"]["secret"] = 1
    elif fault == "bool_island":
        payload["snapshot"]["sampling_island"] = False
    elif fault == "foreign_program":
        payload["snapshot"]["programs"] = {}
    else:
        payload["inspiration_ids"] = [payload["parent_id"], payload["parent_id"]]
    with pytest.raises(ValueError):
        parse_controller_event(wire(event))


@pytest.mark.parametrize(
    "fault", ["wire_admission", "running_admission", "profile", "ticket"]
)
def test_resolver_rejects_cross_scope_or_wrong_launch_evidence(native, fault):
    from uuid import UUID

    from backend.app.openevolve_event_projection import (
        inspect_authenticated_controller_event,
    )
    from backend.app.services import DomainError

    event = envelope(native)
    epoch = SimpleNamespace(
        admission_id=UUID(event["admission_id"]), bootstrap_sha256="b" * 64
    )
    admission = SimpleNamespace(
        id=epoch.admission_id,
        logical_operation_id=uuid4(),
        image_id="sha256:" + "c" * 64,
        profile="fixed",
        ticket_sha256=hashlib.sha256(("a" * 64).encode()).hexdigest(),
    )
    if fault == "wire_admission":
        event["admission_id"] = str(uuid4())
    elif fault == "running_admission":
        admission.id = uuid4()
    elif fault == "profile":
        admission.profile = "other"
    else:
        admission.ticket_sha256 = "0" * 64
    session = Mock()
    session.get.return_value = admission
    with (
        patch("backend.app.openevolve_event_projection.require_neutral_transaction"),
        patch(
            "backend.app.openevolve_event_projection._locked_epoch", return_value=epoch
        ),
        patch(
            "backend.app.openevolve_event_projection.require_controller_peer",
            return_value=admission,
        ),
        pytest.raises(DomainError),
    ):
        inspect_authenticated_controller_event(
            session, object(), object(), wire(event), expected_process_profile="fixed"
        )
    session.add.assert_not_called()
    session.commit.assert_not_called()

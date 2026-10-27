"""Exact row closure and pending-tail policy, independent of future SQL migration."""

from types import SimpleNamespace as Row
from uuid import uuid4

import pytest

from packages.openevolve_adapter.worker_checkpoint_closure import (
    validate_worker_closure,
)


def closure_fixture():
    epoch = Row(id=uuid4(), admission_id=uuid4())
    i1, i2 = (Row(id=uuid4(), native_iteration=n) for n in (1, 2))
    terminal0, terminal1 = uuid4(), uuid4()
    controls = []
    events = []
    for sequence, (kind, frontier, intent, terminal) in enumerate(
        [
            ("seed", 0, None, terminal0),
            ("sampling", 0, i1, None),
            ("sampling", 0, i2, None),
            ("terminal", 1, None, terminal1),
        ],
        1,
    ):
        control = Row(
            id=uuid4(),
            kind=kind,
            sequence=sequence,
            frontier=frontier,
            state_sha256="a" * 64,
            request_sha256="c" * 64,
            sampling_intent_id=intent.id if intent else None,
            terminal_id=terminal,
        )
        controls.append(control)
        events.append(
            Row(
                id=uuid4(),
                admission_id=epoch.admission_id,
                control_id=control.id,
                control_sequence=sequence,
                control_frontier=frontier,
                control_state_sha256=control.state_sha256,
                command_sha256=control.request_sha256,
                intent_id=control.sampling_intent_id,
                terminal_id=terminal,
                event_kind="iteration_intent" if intent else "terminal_applied",
                native_iteration=intent.native_iteration if intent else frontier,
            )
        )
    tickets = [
        Row(
            id=uuid4(),
            intent_id=i.id,
            admission_id=epoch.admission_id,
            controller_receipt_id=events[n].id,
        )
        for n, i in [(1, i1), (2, i2)]
    ]
    kernel = {
        "host_pid": 11,
        "start_ticks": 100,
        "pid_namespace": "pid:[1]",
        "cgroup": "0::/unit",
    }
    expected = [
        Row(
            id=uuid4(),
            intent_id=i.id,
            ticket_id=t.id,
            admission_id=epoch.admission_id,
            **kernel,
        )
        for i, t in zip((i1, i2), tickets, strict=True)
    ]
    assigned = [
        Row(
            id=uuid4(),
            intent_id=e.intent_id,
            ticket_id=e.ticket_id,
            expected_identity_id=e.id,
            admission_id=epoch.admission_id,
            **kernel,
        )
        for e in expected
    ]
    outcome = Row(id=uuid4(), intent_id=i1.id)
    closed = Row(
        id=uuid4(),
        assignment_id=assigned[0].id,
        intent_id=i1.id,
        worker_outcome_id=outcome.id,
        native_iteration=1,
    )
    rows = {
        "control_transition_ids": controls,
        "intent_ids": [i1, i2],
        "controller_event_receipt_ids": events,
        "worker_ticket_ids": tickets,
        "worker_expected_identity_ids": expected,
        "worker_assignment_ids": assigned,
        "worker_assignment_closure_ids": [closed],
        "worker_outcome_ids": [outcome],
    }
    return epoch, rows, {i2.id}, {assigned[1].id}


def test_pending_tail_allows_one_open_worker_and_unstarted_prefetched_intent():
    epoch, rows, pending, opened = closure_fixture()
    assert validate_worker_closure(epoch, rows, pending, opened)
    rows["worker_assignment_ids"].pop()
    assert validate_worker_closure(epoch, rows, pending, set())


def test_completed_prefetched_tail_still_requires_closed_assignment():
    epoch, rows, pending, _ = closure_fixture()
    assignment = rows["worker_assignment_ids"][1]
    outcome = Row(id=uuid4(), intent_id=assignment.intent_id)
    rows["worker_outcome_ids"].append(outcome)
    rows["worker_assignment_closure_ids"].append(
        Row(
            id=uuid4(),
            assignment_id=assignment.id,
            intent_id=assignment.intent_id,
            worker_outcome_id=outcome.id,
            native_iteration=2,
        )
    )
    assert validate_worker_closure(epoch, rows, pending, set())


@pytest.mark.parametrize(
    "fault",
    [
        "missing_event",
        "event_control",
        "missing_ticket",
        "missing_expected",
        "foreign_unit",
        "kernel_change",
        "wrong_ticket",
        "orphan_outcome",
        "open_applied",
        "skip_first",
        "wrong_closure",
        "two_open",
        "open_not_listed",
    ],
)
def test_checkpoint_rejects_incomplete_or_illegal_worker_tail(fault):
    epoch, rows, pending, opened = closure_fixture()
    if fault == "missing_event":
        rows["controller_event_receipt_ids"].pop()
    elif fault == "event_control":
        rows["controller_event_receipt_ids"][0].control_state_sha256 = "b" * 64
    elif fault == "missing_ticket":
        rows["worker_ticket_ids"].pop()
    elif fault == "missing_expected":
        rows["worker_expected_identity_ids"].pop()
    elif fault == "foreign_unit":
        rows["worker_expected_identity_ids"][1].admission_id = uuid4()
    elif fault == "kernel_change":
        rows["worker_assignment_ids"][1].start_ticks += 1
    elif fault == "wrong_ticket":
        rows["worker_assignment_ids"][1].ticket_id = uuid4()
    elif fault == "orphan_outcome":
        rows["worker_assignment_closure_ids"].clear()
    elif fault == "open_applied":
        pending.clear()
    elif fault == "skip_first":
        rows["worker_assignment_ids"].pop(0)
        rows["worker_assignment_closure_ids"].clear()
        rows["worker_outcome_ids"].clear()
    elif fault == "wrong_closure":
        rows["worker_assignment_closure_ids"][0].intent_id = uuid4()
    elif fault == "two_open":
        rows["worker_assignment_closure_ids"].clear()
        rows["worker_outcome_ids"].clear()
        opened = {x.id for x in rows["worker_assignment_ids"]}
        pending = {x.id for x in rows["intent_ids"]}
    else:
        opened.clear()
    with pytest.raises(ValueError):
        validate_worker_closure(epoch, rows, pending, opened)


def closure_dto():
    from packages.openevolve_adapter.host_contracts import InitialCheckpointClosure
    from packages.openevolve_adapter.observer_transport import canonical

    epoch, rows, pending, opened = closure_fixture()
    data = {
        "epoch_id": str(epoch.id),
        "frontier": 1,
        "spec_sha256": "b" * 64,
        "state_sha256": "a" * 64,
        "control_sequence": 4,
        **{key: sorted(str(x.id) for x in values) for key, values in rows.items()},
        "sampling_transition_ids": sorted(
            str(x.id) for x in rows["control_transition_ids"] if x.kind == "sampling"
        ),
        "terminal_ids": sorted(
            str(x.terminal_id) for x in rows["control_transition_ids"] if x.terminal_id
        ),
        "candidate_ids": [str(uuid4())],
        "objective_receipt_ids": [str(uuid4())],
        "objective_ack_ids": [str(uuid4())],
        "pending_intent_ids": sorted(map(str, pending)),
        "open_worker_assignment_ids": sorted(map(str, opened)),
        "rendered_context_ids": [],
        "proposal_response_ids": [],
        "proposal_source_ids": [],
        "evaluation_ids": [str(uuid4())],
        "evaluation_outcome_ids": [str(uuid4())],
    }
    return InitialCheckpointClosure.model_validate_json(canonical(data))


def test_complete_checkpoint_dto_roundtrip_remains_nonresumable():
    from packages.openevolve_adapter.host_contracts import InitialCheckpointClosure

    dto = closure_dto()
    assert InitialCheckpointClosure.model_validate_json(dto.model_dump_json()) == dto
    assert dto.continuation == "not_admitted_initial_epoch_only"


@pytest.mark.parametrize(
    "fault",
    ["missing_events", "missing_tickets", "unlisted_open", "wrong_control_sequence"],
)
def test_checkpoint_dto_rejects_incomplete_new_evidence_lists(fault):
    from packages.openevolve_adapter.host_contracts import InitialCheckpointClosure

    data = closure_dto().model_dump(mode="python")
    if fault == "missing_events":
        data.pop("controller_event_receipt_ids")
    elif fault == "missing_tickets":
        data["worker_ticket_ids"] = ()
    elif fault == "unlisted_open":
        data["open_worker_assignment_ids"] = (uuid4(),)
    else:
        data["control_sequence"] = 3
    with pytest.raises(ValueError):
        InitialCheckpointClosure.model_validate(data)

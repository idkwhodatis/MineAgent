"""Disabled service wiring with doubles, not kernel/SQL acceptance."""

from contextlib import ExitStack
from types import SimpleNamespace as N
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from backend.app import openevolve_worker_actions as actions
from backend.app.services import DomainError
from packages.openevolve_adapter.contracts import SearchOperationContext
from packages.openevolve_adapter.host_contracts import (
    EvidenceRef,
    RenderedContextCommand,
    WorkerOutcomeCommand,
)
from packages.research_runtime.broker_contracts import digest


def fixture():
    fence = N(owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1)
    epoch = N(id=uuid4(), admission_id=uuid4())
    intent = N(
        id=uuid4(),
        epoch_id=epoch.id,
        native_iteration=1,
        parent_candidate_id=uuid4(),
        parent_source_sha256="a" * 64,
        selection_sha256="b" * 64,
    )
    ticket, expected = N(id=uuid4()), N(id=uuid4())
    peer = {
        "host_pid": 123,
        "start_ticks": 456,
        "pid_namespace": "pid:[7]",
        "cgroup": "0::/unit",
    }
    assignment = N(
        id=uuid4(),
        epoch_id=epoch.id,
        admission_id=epoch.admission_id,
        ticket_id=ticket.id,
        expected_identity_id=expected.id,
        **vars(fence),
        **peer,
    )
    return fence, epoch, intent, ticket, expected, peer, assignment


def binding(f):
    return f[1:6]


@pytest.mark.parametrize(
    "fault",
    [
        "closed",
        "old_worker",
        "reused_pid",
        "ticket",
        "expected",
        "admission",
        "outcome",
        "missing",
    ],
)
def test_every_action_rejects_closed_or_wrong_assignment(fault):
    f = fixture()
    fence, _, _, _, _, _peer, assignment = f
    if fault == "old_worker":
        assignment.host_pid += 1
    if fault == "reused_pid":
        assignment.start_ticks += 1
    if fault in ("ticket", "expected", "admission"):
        setattr(
            assignment,
            {
                "ticket": "ticket_id",
                "expected": "expected_identity_id",
                "admission": "admission_id",
            }[fault],
            uuid4(),
        )
    session = Mock()
    session.scalar.side_effect = [
        None if fault == "missing" else assignment,
        N() if fault == "closed" else None,
        N(epoch_id=f[1].id, **vars(fence)) if fault == "outcome" else None,
    ]
    with (
        patch.object(actions, "_live_worker", return_value=binding(f)),
        pytest.raises(DomainError),
    ):
        actions._open_worker(session, fence, object(), object(), "fixed")
    session.add.assert_not_called()
    session.commit.assert_not_called()


def model_fixture():
    f = fixture()
    fence, epoch, intent = f[:3]
    ctx = SearchOperationContext(
        owner_id=fence.owner_id,
        run_id=fence.run_id,
        epoch_id=epoch.id,
        iteration_intent_id=intent.id,
        native_iteration=1,
        operation_ordinal=0,
        parent_candidate_id=intent.parent_candidate_id,
        parent_source_sha256=intent.parent_source_sha256,
        selection_snapshot_sha256=intent.selection_sha256,
        rendered_context_sha256="c" * 64,
    )
    context = N(
        id=uuid4(), context=ctx.model_dump(mode="json"), context_sha256=digest(ctx)
    )
    rendered = N(
        id=uuid4(),
        context_id=context.id,
        request_sha256="d" * 64,
        rendered_sha256="c" * 64,
    )
    operation = N(
        id=uuid4(),
        context_id=context.id,
        purpose="chat",
        ordinal=0,
        state="open",
        request_sha256="d" * 64,
    )
    return f, context, rendered, operation


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "context",
        "purpose",
        "ordinal",
        "state",
        "request",
        "wrong_intent",
        "context_hash",
        "aborted",
        "phase_advanced",
    ],
)
def test_model_operation_binds_exact_context_phase_and_logical_identity(fault):
    f, context, rendered, operation = model_fixture()
    fence, epoch, intent = f[:3]
    if fault == "context":
        operation.context_id = uuid4()
    if fault == "purpose":
        operation.purpose = "embedding"
    if fault == "ordinal":
        operation.ordinal = False
    if fault == "state":
        operation.state = "unknown"
    if fault == "request":
        operation.request_sha256 = "e" * 64
    if fault == "wrong_intent":
        context.context["iteration_intent_id"] = str(uuid4())
    if fault == "context_hash":
        context.context_sha256 = "e" * 64
    session = Mock()
    session.scalar.return_value = N() if fault == "aborted" else None
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(actions, "_open_worker", return_value=(epoch, intent))
        )
        stack.enter_context(
            patch.object(
                actions,
                "_phase",
                return_value=(
                    rendered,
                    N() if fault == "phase_advanced" else None,
                    None,
                    None,
                    None,
                ),
            )
        )
        stack.enter_context(
            patch.object(actions.domain, "_owned_row", side_effect=[context, operation])
        )
        if fault:
            stack.enter_context(pytest.raises(DomainError))
        actions.require_worker_model_operation(
            session,
            fence,
            object(),
            object(),
            expected_process_profile="fixed",
            context_id=context.id,
            operation_id=operation.id,
            request_sha256="d" * 64,
        )
    session.add.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.parametrize(
    "fault", [None, "seed", "candidate", "ticket", "epoch", "grant", "ack", "completed"]
)
def test_evaluation_cannot_cross_candidate_epoch_or_logical_ticket(fault):
    f = fixture()
    fence, epoch, intent = f[:3]
    candidate = N(id=uuid4(), candidate_id=uuid4(), source_sha256="a" * 64)
    logical = uuid4()
    evaluation = N(
        id=uuid4(),
        epoch_id=epoch.id,
        candidate_id=candidate.candidate_id,
        source_sha256=candidate.source_sha256,
        phase="mutation",
        logical_ticket_id=logical,
    )
    if fault == "seed":
        evaluation.phase = "seed"
    if fault == "candidate":
        evaluation.candidate_id = uuid4()
    if fault == "ticket":
        evaluation.logical_ticket_id = uuid4()
    if fault == "epoch":
        evaluation.epoch_id = uuid4()
    session = Mock()
    session.scalar.side_effect = [
        N() if fault == "ack" else None,
        N() if fault == "completed" else None,
    ]
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(actions, "_open_worker", return_value=(epoch, intent))
        )
        stack.enter_context(
            patch.object(
                actions, "_phase", return_value=(None, None, None, None, candidate)
            )
        )
        stack.enter_context(
            patch.object(actions.domain, "_owned_row", return_value=evaluation)
        )
        if fault:
            stack.enter_context(pytest.raises(DomainError))
        actions.require_worker_evaluation(
            session,
            fence,
            object(),
            object(),
            expected_process_profile="fixed",
            candidate_grant_id=uuid4() if fault == "grant" else candidate.id,
            evaluation_id=evaluation.id,
            logical_ticket_id=logical,
        )
    session.add.assert_not_called()


@pytest.mark.parametrize(
    "fault", [None, "epoch", "intent", "advanced", "unknown_event"]
)
def test_observer_render_is_exact_intent_and_only_initial_phase(fault):
    f = fixture()
    fence, epoch, intent = f[:3]
    command = RenderedContextCommand(
        epoch_id=uuid4() if fault == "epoch" else epoch.id,
        intent_id=uuid4() if fault == "intent" else intent.id,
        context_id=uuid4(),
        projection=EvidenceRef(id=uuid4(), sha256="a" * 64),
    )
    session = Mock()
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(actions, "_open_worker", return_value=(epoch, intent))
        )
        stack.enter_context(
            patch.object(
                actions,
                "_phase",
                return_value=(
                    N() if fault == "advanced" else None,
                    None,
                    None,
                    None,
                    None,
                ),
            )
        )
        mutation = stack.enter_context(
            patch.object(actions.domain, "register_rendered_context")
        )
        if fault:
            stack.enter_context(pytest.raises(DomainError))
        actions.apply_worker_observer_command(
            session,
            fence,
            object(),
            object(),
            command,
            event="unknown" if fault == "unknown_event" else "rendered_prompt",
            expected_process_profile="fixed",
            settings=object(),
        )
    assert mutation.call_count == (0 if fault else 1)
    session.commit.assert_not_called()


def test_worker_outcome_and_assignment_closure_share_flush_only_transaction():
    f = fixture()
    fence, epoch, intent = f[:3]
    command = WorkerOutcomeCommand(
        epoch_id=epoch.id,
        intent_id=intent.id,
        native_iteration=1,
        status="worker_error",
        evidence=EvidenceRef(id=uuid4(), sha256="a" * 64),
    )
    session = Mock()
    with (
        patch.object(actions, "_open_worker", return_value=(epoch, intent)),
        patch.object(actions, "_phase", return_value=(None,) * 5),
        patch.object(
            actions.domain, "record_worker_outcome", return_value=N(id=uuid4())
        ) as record,
        patch(
            "backend.app.openevolve_worker_services.close_worker_assignment"
        ) as close,
    ):
        result = actions.apply_worker_observer_command(
            session,
            fence,
            object(),
            object(),
            command,
            event="worker_outcome",
            expected_process_profile="fixed",
            settings=object(),
        )
    record.assert_called_once()
    assert close.call_args.kwargs["worker_outcome_id"] == result.id
    session.commit.assert_not_called()


def test_phase_rejects_orphaned_and_cross_proposal_evidence():
    f = fixture()
    fence, epoch, intent = f[:3]
    rows = [None, N(id=uuid4(), rendered_context_id=uuid4()), None, None, None]
    with patch.object(actions, "_row", side_effect=rows), pytest.raises(DomainError):
        actions._phase(Mock(), fence, intent, epoch.admission_id)


@pytest.mark.parametrize(
    "fault", [None, "proposal", "applied", "enforced", "grant", "seed", "admission"]
)
def test_phase_chain_requires_exact_predecessors(fault):
    f = fixture()
    fence, epoch, intent = f[:3]
    rendered = N(id=uuid4())
    proposal = N(id=uuid4(), rendered_context_id=rendered.id)
    applied = N(id=uuid4(), proposal_id=proposal.id)
    enforced = N(id=uuid4(), proposal_id=proposal.id)
    candidate = N(
        id=uuid4(),
        proposal_source_id=enforced.id,
        phase="mutation",
        admission_id=epoch.admission_id,
    )
    if fault == "proposal":
        proposal.rendered_context_id = uuid4()
    if fault == "applied":
        applied.proposal_id = uuid4()
    if fault == "enforced":
        enforced.proposal_id = uuid4()
    if fault == "grant":
        candidate.proposal_source_id = uuid4()
    if fault == "seed":
        candidate.phase = "seed"
    if fault == "admission":
        candidate.admission_id = uuid4()
    rows = [rendered, proposal, applied, enforced, candidate]
    with ExitStack() as stack:
        stack.enter_context(patch.object(actions, "_row", side_effect=rows))
        if fault:
            stack.enter_context(pytest.raises(DomainError))
        assert actions._phase(Mock(), fence, intent, epoch.admission_id) == tuple(rows)


@pytest.mark.parametrize(
    "fault",
    [None, "seed", "other_admission", "already_registered", "oversize", "no_enforced"],
)
def test_candidate_registration_only_current_enforced_mutation(fault):
    from packages.openevolve_adapter.host_contracts import CandidateIntentCommand

    f = fixture()
    fence, epoch, intent = f[:3]
    command = CandidateIntentCommand(
        epoch_id=epoch.id,
        admission_id=uuid4() if fault == "other_admission" else epoch.admission_id,
        upstream_program_id=uuid4(),
        phase="seed" if fault == "seed" else "mutation",
        intent_id=None if fault == "seed" else intent.id,
        source=EvidenceRef(id=uuid4(), sha256="a" * 64),
        ticket_sha256="b" * 64,
        request_sha256="c" * 64,
    )
    enforced = (
        None
        if fault == "no_enforced"
        else N(
            evidence="oversize_source_digest"
            if fault == "oversize"
            else "complete_source"
        )
    )
    session = Mock()
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(actions, "_open_worker", return_value=(epoch, intent))
        )
        stack.enter_context(
            patch.object(
                actions,
                "_phase",
                return_value=(
                    None,
                    None,
                    None,
                    enforced,
                    N() if fault == "already_registered" else None,
                ),
            )
        )
        mutation = stack.enter_context(
            patch.object(actions.domain, "register_candidate_intent")
        )
        if fault:
            stack.enter_context(pytest.raises(DomainError))
        actions.apply_worker_observer_command(
            session,
            fence,
            object(),
            object(),
            command,
            event="candidate_intent",
            expected_process_profile="fixed",
            settings=object(),
        )
    assert mutation.call_count == (0 if fault else 1)


@pytest.mark.parametrize(
    "fault", [None, "wrong_grant", "already_acknowledged", "no_candidate"]
)
def test_evaluation_ack_requires_exact_current_grant(fault):
    from packages.openevolve_adapter.host_contracts import ObjectiveAckCommand

    f = fixture()
    fence, epoch, intent = f[:3]
    candidate = N(id=uuid4())
    command = ObjectiveAckCommand(
        epoch_id=epoch.id,
        grant_id=uuid4() if fault == "wrong_grant" else candidate.id,
        objective_receipt_id=uuid4(),
        eligible=True,
        combined_score=0.0,
        request_sha256="a" * 64,
    )
    session = Mock()
    session.scalar.return_value = N() if fault == "already_acknowledged" else None
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(actions, "_open_worker", return_value=(epoch, intent))
        )
        stack.enter_context(
            patch.object(
                actions,
                "_phase",
                return_value=(
                    None,
                    None,
                    None,
                    None,
                    None if fault == "no_candidate" else candidate,
                ),
            )
        )
        mutation = stack.enter_context(
            patch.object(actions.domain, "acknowledge_objective")
        )
        if fault:
            stack.enter_context(pytest.raises(DomainError))
        actions.apply_worker_observer_command(
            session,
            fence,
            object(),
            object(),
            command,
            event="evaluation_complete",
            expected_process_profile="fixed",
            settings=object(),
        )
    assert mutation.call_count == (0 if fault else 1)


def test_error_outcome_cannot_drop_already_acknowledged_objective():
    f = fixture()
    fence, epoch, intent = f[:3]
    command = WorkerOutcomeCommand(
        epoch_id=epoch.id,
        intent_id=intent.id,
        native_iteration=1,
        status="worker_error",
        evidence=EvidenceRef(id=uuid4(), sha256="a" * 64),
    )
    session = Mock()
    session.scalar.return_value = N(id=uuid4())
    with (
        patch.object(actions, "_open_worker", return_value=(epoch, intent)),
        patch.object(
            actions, "_phase", return_value=(None, None, None, None, N(id=uuid4()))
        ),
        patch.object(actions.domain, "record_worker_outcome") as mutation,
        pytest.raises(DomainError),
    ):
        actions.apply_worker_observer_command(
            session,
            fence,
            object(),
            object(),
            command,
            event="worker_outcome",
            expected_process_profile="fixed",
            settings=object(),
        )
    mutation.assert_not_called()


@pytest.mark.parametrize("event", ["proposal_applied", "proposal_enforced"])
@pytest.mark.parametrize(
    "fault",
    [
        None,
        "wrong_proposal",
        "wrong_stage",
        "already_enforced",
        "wrong_order",
        "candidate_exists",
    ],
)
def test_proposal_source_stage_exact_predecessor_and_evidence(event, fault):
    import hashlib

    from packages.openevolve_adapter.host_contracts import ProposalSourceCommand

    f = fixture()
    fence, epoch, intent = f[:3]
    proposal = N(id=uuid4())
    command = ProposalSourceCommand(
        epoch_id=epoch.id,
        intent_id=intent.id,
        proposal_id=uuid4() if fault == "wrong_proposal" else proposal.id,
        source=EvidenceRef(id=uuid4(), sha256="a" * 64),
    )
    stage = "applied" if event == "proposal_applied" else "enforced"
    source = {
        "stage": ("enforced" if stage == "applied" else "applied")
        if fault == "wrong_stage"
        else stage,
        "source": "x=1\n",
        "source_sha256": hashlib.sha256(b"x=1\n").hexdigest(),
        "source_bytes": 4,
        "evidence": "complete_source",
    }
    applied = N() if stage == "enforced" else None
    if fault == "wrong_order":
        applied = None if stage == "enforced" else N()
    session = Mock()
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(actions, "_open_worker", return_value=(epoch, intent))
        )
        stack.enter_context(
            patch.object(
                actions,
                "_phase",
                return_value=(
                    N(),
                    proposal,
                    applied,
                    N() if fault == "already_enforced" else None,
                    N() if fault == "candidate_exists" else None,
                ),
            )
        )
        stack.enter_context(
            patch.object(actions.domain, "_read_evidence", return_value=source)
        )
        mutation = stack.enter_context(
            patch.object(actions.domain, "record_proposal_source")
        )
        if fault:
            stack.enter_context(pytest.raises(DomainError))
        actions.apply_worker_observer_command(
            session,
            fence,
            object(),
            object(),
            command,
            event=event,
            expected_process_profile="fixed",
            settings=object(),
        )
    assert mutation.call_count == (0 if fault else 1)


@pytest.mark.parametrize(
    "fault", [None, "no_rendered", "already_proposed", "wrong_intent"]
)
def test_proposal_response_only_after_exact_rendered_intent(fault):
    from packages.openevolve_adapter.host_contracts import ProposalResponseCommand

    f = fixture()
    fence, epoch, intent = f[:3]
    command = ProposalResponseCommand(
        epoch_id=epoch.id,
        intent_id=uuid4() if fault == "wrong_intent" else intent.id,
        model_outcome_id=uuid4(),
        response_sha256="a" * 64,
        response_text="diff",
    )
    session = Mock()
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(actions, "_open_worker", return_value=(epoch, intent))
        )
        stack.enter_context(
            patch.object(
                actions,
                "_phase",
                return_value=(
                    None if fault == "no_rendered" else N(),
                    N() if fault == "already_proposed" else None,
                    None,
                    None,
                    None,
                ),
            )
        )
        mutation = stack.enter_context(
            patch.object(actions.domain, "record_proposal_response")
        )
        if fault:
            stack.enter_context(pytest.raises(DomainError))
        actions.apply_worker_observer_command(
            session,
            fence,
            object(),
            object(),
            command,
            event="proposal_response",
            expected_process_profile="fixed",
            settings=object(),
        )
    assert mutation.call_count == (0 if fault else 1)

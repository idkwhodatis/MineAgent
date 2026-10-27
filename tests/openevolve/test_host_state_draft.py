"""Draft SQL shape and service command tests, never PG/RPC admission evidence."""

from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable

from backend.app import openevolve_models as m
from backend.app.openevolve_services import acknowledge_objective, prepare_checkpoint
from backend.app.services import DomainError
from packages.openevolve_adapter.host_contracts import (
    CheckpointIntentCommand,
    ObjectiveAckCommand,
    TerminalCommand,
)

TABLES = [
    value.__table__
    for value in vars(m).values()
    if isinstance(value, type)
    and hasattr(value, "__table__")
    and value.__module__ == m.__name__
]


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
def test_all_draft_tables_compile_with_owner_scope(table):
    sql = str(CreateTable(table).compile(dialect=dialect()))
    assert "owner_id UUID NOT NULL" in sql
    assert "FOREIGN KEY" in sql


def test_every_internal_fk_targets_a_declared_unique_tuple():
    names = {t.name for t in TABLES}
    for table in TABLES:
        for fk in table.foreign_key_constraints:
            remote = next(iter(fk.elements)).column.table
            if remote.name not in names:
                continue
            columns = tuple(element.column.name for element in fk.elements)
            uniques = [tuple(col.name for col in remote.primary_key.columns)]
            uniques.extend(
                tuple(col.name for col in c.columns)
                for c in remote.constraints
                if isinstance(c, UniqueConstraint)
            )
            assert columns in uniques, (table.name, remote.name, columns)


def test_rendered_context_not_required_before_actual_prompt():
    assert "context_id" not in m.SearchIterationIntent.__table__.columns
    assert "context_id" in m.SearchRenderedContext.__table__.columns
    assert "source_object_sha256" in m.SearchCandidate.__table__.columns
    assert "source_sha256" in m.SearchCandidate.__table__.columns


def setup_receipt(score=-1.25, status="eligible"):
    fence = SimpleNamespace(
        owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1
    )
    scope = vars(fence)
    epoch = uuid4()
    candidate = uuid4()
    grant_id = uuid4()
    receipt_id = uuid4()
    grant = m.SearchCandidateGrant(
        id=grant_id,
        **scope,
        epoch_id=epoch,
        candidate_id=candidate,
        source_sha256="a" * 64,
    )
    receipt = m.SearchObjectiveReceipt(
        id=receipt_id,
        **scope,
        epoch_id=epoch,
        candidate_id=candidate,
        source_sha256="a" * 64,
        status=status,
        combined_score=score,
    )
    session = Mock()
    session.get.side_effect = lambda model, identity: {
        grant_id: grant,
        receipt_id: receipt,
    }.get(identity)
    session.scalar.return_value = None
    command = ObjectiveAckCommand(
        epoch_id=epoch,
        grant_id=grant_id,
        objective_receipt_id=receipt_id,
        eligible=status == "eligible",
        combined_score=score,
        request_sha256="b" * 64,
    )
    return fence, session, command, grant, receipt


@pytest.mark.parametrize("score", [-3.5, 0.0, 2.25])
def test_exact_signed_objective_ack_flush_only(score):
    fence, session, command, grant, receipt = setup_receipt(score)
    with patch("backend.app.openevolve_services._locked_epoch"):
        row = acknowledge_objective(session, fence, command)
    assert row.objective_receipt_id == receipt.id and row.grant_id == grant.id
    session.flush.assert_called_once()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize("mutation", ["candidate", "epoch", "source", "score"])
def test_cross_identity_or_score_ack_rejected(mutation):
    fence, session, command, _grant, receipt = setup_receipt()
    if mutation == "candidate":
        receipt.candidate_id = uuid4()
    elif mutation == "epoch":
        receipt.epoch_id = uuid4()
    elif mutation == "source":
        receipt.source_sha256 = "c" * 64
    else:
        receipt.combined_score = 0.0
    with (
        patch("backend.app.openevolve_services._locked_epoch"),
        pytest.raises(DomainError),
    ):
        acknowledge_objective(session, fence, command)
    session.add.assert_not_called()


def test_ineligible_null_ack_valid_but_zero_forbidden():
    fence, session, command, _, _ = setup_receipt(None, "undefined_objective")
    with patch("backend.app.openevolve_services._locked_epoch"):
        acknowledge_objective(session, fence, command)
    changed = command.model_copy(update={"combined_score": 0.0})
    with (
        patch("backend.app.openevolve_services._locked_epoch"),
        pytest.raises(DomainError),
    ):
        acknowledge_objective(session, fence, changed)


def test_strict_scalar_bool_cannot_spoof_objective():
    _, _, command, _, _ = setup_receipt()
    with pytest.raises(ValidationError):
        ObjectiveAckCommand.model_validate(
            command.model_copy(update={"combined_score": True}).model_dump(
                mode="python"
            )
        )


def test_terminal_dto_cannot_skip_seed_or_use_seed_as_mutation():
    common = {"epoch_id": uuid4(), "state": {"id": uuid4(), "sha256": "a" * 64}}
    with pytest.raises(ValidationError):
        TerminalCommand(
            **common, frontier=0, outcome="accepted", worker_outcome_id=uuid4()
        )
    with pytest.raises(ValidationError):
        TerminalCommand(
            **common, frontier=1, outcome="seed", seed_objective_ack_id=uuid4()
        )


def test_checkpoint_requires_latest_acknowledged_terminal():
    fence, session, _, _, _ = setup_receipt()
    scope = vars(fence)
    epoch = uuid4()
    old = m.SearchTerminalApplication(
        id=uuid4(), **scope, epoch_id=epoch, frontier=0, state_sha256="a" * 64
    )
    latest = m.SearchTerminalApplication(
        id=uuid4(), **scope, epoch_id=epoch, frontier=1, state_sha256="b" * 64
    )
    session.get.return_value = old
    session.get.side_effect = None
    session.scalar.side_effect = [None, latest, latest]
    command = CheckpointIntentCommand(
        epoch_id=epoch,
        terminal_id=old.id,
        frontier=0,
        state_sha256="a" * 64,
        request_sha256="c" * 64,
    )
    with (
        patch("backend.app.openevolve_services._locked_epoch"),
        pytest.raises(DomainError),
    ):
        prepare_checkpoint(session, fence, command)
    session.add.assert_not_called()


@pytest.mark.parametrize("change", ["drop", "metadata", "archive", "best", "artifact"])
def test_error_terminal_cannot_mutate_state(change):
    import copy

    from backend.app.openevolve_services import acknowledge_terminal_application

    fence, session, _, _, _ = setup_receipt()
    epoch = uuid4()
    worker = m.SearchWorkerOutcome(
        id=uuid4(),
        **vars(fence),
        epoch_id=epoch,
        native_iteration=1,
        status="worker_error",
    )
    previous = m.SearchTerminalApplication(
        id=uuid4(),
        **vars(fence),
        epoch_id=epoch,
        frontier=0,
        state_object_id=uuid4(),
        state_sha256="a" * 64,
    )
    before = {
        "metadata": {
            "last_iteration": 0,
            "archive": ["seed"],
            "best_program_id": "seed",
        },
        "programs": {"seed": {"metadata": {}, "artifacts_json": None}},
    }
    after = copy.deepcopy(before)
    after["metadata"]["last_iteration"] = 1
    if change == "drop":
        after["programs"] = {}
    elif change == "metadata":
        after["programs"]["seed"]["metadata"] = {"forged": True}
    elif change == "archive":
        after["metadata"]["archive"] = []
    elif change == "best":
        after["metadata"]["best_program_id"] = None
    else:
        after["programs"]["seed"]["artifacts_json"] = "{}"
    session.scalar.side_effect = [None, previous, previous]
    session.get.side_effect = None
    session.get.return_value = worker
    command = TerminalCommand(
        epoch_id=epoch,
        frontier=1,
        outcome="error",
        worker_outcome_id=worker.id,
        state={"id": uuid4(), "sha256": "b" * 64},
    )
    with (
        patch("backend.app.openevolve_services._locked_epoch"),
        patch(
            "backend.app.openevolve_services._validated_population",
            side_effect=[after, before],
        ),
        pytest.raises(DomainError, match="changed native"),
    ):
        acknowledge_terminal_application(session, fence, command, object())
    session.add.assert_not_called()


def test_accepted_terminal_cannot_insert_prefetched_other_candidate():
    from backend.app.openevolve_services import acknowledge_terminal_application

    fence, session, _, _, _ = setup_receipt()
    epoch = uuid4()
    candidate = m.SearchCandidate(
        id=uuid4(), **vars(fence), upstream_program_id=uuid4()
    )
    worker = m.SearchWorkerOutcome(
        id=uuid4(),
        **vars(fence),
        epoch_id=epoch,
        native_iteration=1,
        status="evaluated",
        candidate_id=candidate.id,
    )
    previous = m.SearchTerminalApplication(
        id=uuid4(),
        **vars(fence),
        epoch_id=epoch,
        frontier=0,
        state_object_id=uuid4(),
        state_sha256="a" * 64,
    )
    before = {"metadata": {"last_iteration": 0}, "programs": {"seed": {}}}
    after = {
        "metadata": {"last_iteration": 1},
        "programs": {"seed": {}, str(uuid4()): {}},
    }
    session.scalar.side_effect = [None, previous, previous]
    session.get.side_effect = lambda _, identity: {
        worker.id: worker,
        candidate.id: candidate,
    }[identity]
    command = TerminalCommand(
        epoch_id=epoch,
        frontier=1,
        outcome="accepted",
        worker_outcome_id=worker.id,
        state={"id": uuid4(), "sha256": "b" * 64},
    )
    with (
        patch("backend.app.openevolve_services._locked_epoch"),
        patch(
            "backend.app.openevolve_services._validated_population",
            side_effect=[after, before],
        ),
        pytest.raises(DomainError, match="another intent"),
    ):
        acknowledge_terminal_application(session, fence, command, object())
    session.add.assert_not_called()


def test_existing_iteration_replay_does_not_recheck_mutated_reservation():
    from backend.app.openevolve_services import _persist_iteration_intent
    from packages.openevolve_adapter.host_contracts import IterationIntentCommand

    fence, session, _, _, _ = setup_receipt()
    command = IterationIntentCommand(
        epoch_id=uuid4(),
        native_iteration=2,
        parent_candidate_id=uuid4(),
        parent_source_sha256="a" * 64,
        mutation_reservation_id=uuid4(),
        selection={"id": uuid4(), "sha256": "b" * 64},
    )
    existing = m.SearchIterationIntent(
        id=uuid4(),
        **vars(fence),
        epoch_id=command.epoch_id,
        native_iteration=2,
        parent_candidate_id=command.parent_candidate_id,
        parent_source_sha256=command.parent_source_sha256,
        mutation_reservation_id=command.mutation_reservation_id,
        selection_sha256=command.selection.sha256,
    )
    session.scalar.return_value = existing
    with patch("backend.app.openevolve_services._locked_epoch"):
        assert _persist_iteration_intent(session, fence, command, object()) is existing
    session.get.assert_not_called()
    session.add.assert_not_called()


def test_existing_checkpoint_prepare_replays_after_frontier_advances():
    fence, session, _, _, _ = setup_receipt()
    command = CheckpointIntentCommand(
        epoch_id=uuid4(),
        terminal_id=uuid4(),
        frontier=0,
        state_sha256="a" * 64,
        request_sha256="b" * 64,
    )
    existing = m.SearchCheckpointIntent(
        id=uuid4(),
        **vars(fence),
        epoch_id=command.epoch_id,
        terminal_id=command.terminal_id,
        frontier=0,
        state_sha256="a" * 64,
        request_sha256="b" * 64,
    )
    session.scalar.return_value = existing
    with patch("backend.app.openevolve_services._locked_epoch"):
        assert prepare_checkpoint(session, fence, command) is existing
    session.get.assert_not_called()
    session.add.assert_not_called()


def test_finished_but_unapplied_worker_remains_in_checkpoint_tail():
    from backend.app.openevolve_services import _pending_intent_ids

    first, second = uuid4(), uuid4()
    w1, w2 = uuid4(), uuid4()
    intents = [SimpleNamespace(id=first), SimpleNamespace(id=second)]
    workers = [
        SimpleNamespace(id=w1, intent_id=first),
        SimpleNamespace(id=w2, intent_id=second),
    ]
    terminals = [
        SimpleNamespace(worker_outcome_id=None),
        SimpleNamespace(worker_outcome_id=w1),
    ]
    assert _pending_intent_ids(intents, workers, terminals) == {second}
    assert (
        _pending_intent_ids(
            intents, workers, terminals + [SimpleNamespace(worker_outcome_id=w2)]
        )
        == set()
    )


def test_published_snapshot_exact_replay_ignores_later_frontier():
    from backend.app.openevolve_services import publish_checkpoint_snapshot
    from packages.openevolve_adapter.host_contracts import CheckpointPublicationCommand

    fence, session, _, _, _ = setup_receipt()
    epoch = SimpleNamespace(id=uuid4())
    intent = m.SearchCheckpointIntent(id=uuid4(), **vars(fence), epoch_id=epoch.id)
    existing = m.SearchCheckpointPublication(
        id=uuid4(),
        **vars(fence),
        intent_id=intent.id,
        inventory_sha256="a" * 64,
        closure_sha256="b" * 64,
    )
    session.get.side_effect = None
    session.get.return_value = intent
    session.scalar.return_value = existing
    command = CheckpointPublicationCommand(
        epoch_id=epoch.id,
        intent_id=intent.id,
        inventory={"id": uuid4(), "sha256": "a" * 64},
        closure={"id": uuid4(), "sha256": "b" * 64},
    )
    existing.inventory_object_id = command.inventory.id
    existing.closure_object_id = command.closure.id
    session.get.side_effect = lambda model, identity: (
        existing if identity == existing.id else intent
    )
    with patch("backend.app.openevolve_services._locked_epoch", return_value=epoch):
        assert (
            publish_checkpoint_snapshot(session, fence, command, object()) is existing
        )
    session.add.assert_not_called()
    session.scalars.assert_not_called()


def test_proposal_source_never_accepts_truncated_oversize():
    import hashlib

    from packages.openevolve_adapter.host_contracts import ProposalSourceEnvelope

    raw = "x = 1\n"
    valid = ProposalSourceEnvelope(
        stage="enforced",
        source=raw,
        source_sha256=hashlib.sha256(raw.encode()).hexdigest(),
        source_bytes=len(raw),
        evidence="complete_source",
    )
    assert valid.source == raw
    with pytest.raises(ValidationError):
        ProposalSourceEnvelope(
            stage="enforced",
            source=raw,
            source_sha256="a" * 64,
            source_bytes=32769,
            evidence="oversize_source_digest",
        )
    with pytest.raises(ValidationError):
        ProposalSourceEnvelope(
            stage="applied",
            source=None,
            source_sha256="a" * 64,
            source_bytes=12,
            evidence="complete_source",
        )


def test_proposal_and_candidate_full_intent_fk():
    candidate_sql = str(
        CreateTable(m.SearchCandidateGrant.__table__).compile(dialect=dialect())
    )
    proposal_sql = str(
        CreateTable(m.SearchProposalResponse.__table__).compile(dialect=dialect())
    )
    assert (
        "search_proposal_sources (owner_id, run_id, attempt_id, generation, epoch_id, intent_id, source_sha256, id, stage, evidence)"
        in candidate_sql
    )
    assert (
        "search_rendered_contexts (owner_id, run_id, attempt_id, generation, epoch_id, intent_id, id)"
        in proposal_sql
    )


@pytest.mark.parametrize("raw", ["", "x=1\r\n", "x=\x00\n", "# coding: latin-1\nx=1\n"])
def test_rejected_proposal_bytes_are_retained_without_candidate_admission(raw):
    import hashlib

    from packages.openevolve_adapter.host_contracts import (
        ProposalSourceEnvelope,
        SourceEnvelope,
    )

    sha = hashlib.sha256(raw.encode()).hexdigest()
    captured = ProposalSourceEnvelope(
        stage="applied",
        source=raw,
        source_sha256=sha,
        source_bytes=len(raw.encode()),
        evidence="complete_source",
    )
    assert captured.source == raw
    with pytest.raises(ValidationError):
        SourceEnvelope(source=raw, source_sha256=sha)


def test_rendered_projection_cannot_omit_actual_empty_artifact_hash():
    from packages.openevolve_adapter.host_contracts import RenderedProjection

    with pytest.raises(ValidationError):
        RenderedProjection(
            system="system",
            user="user",
            references=(),
            current_source_sha256="a" * 64,
            artifact_section_sha256=None,
        )


def test_accepted_terminal_cannot_mutate_existing_program_feedback():
    import copy

    from backend.app.openevolve_services import acknowledge_terminal_application

    fence, session, _, _, _ = setup_receipt()
    epoch = uuid4()
    candidate = m.SearchCandidate(
        id=uuid4(), **vars(fence), upstream_program_id=uuid4()
    )
    worker = m.SearchWorkerOutcome(
        id=uuid4(),
        **vars(fence),
        epoch_id=epoch,
        native_iteration=1,
        status="evaluated",
        candidate_id=candidate.id,
    )
    previous = m.SearchTerminalApplication(
        id=uuid4(),
        **vars(fence),
        epoch_id=epoch,
        frontier=0,
        state_object_id=uuid4(),
        state_sha256="a" * 64,
    )
    before = {
        "metadata": {"last_iteration": 0},
        "programs": {"seed": {"artifacts_json": "original"}},
    }
    after = copy.deepcopy(before)
    after["metadata"]["last_iteration"] = 1
    after["programs"]["seed"]["artifacts_json"] = "invented"
    session.scalar.side_effect = [None, previous, previous]
    session.get.side_effect = lambda _, identity: {
        worker.id: worker,
        candidate.id: candidate,
    }[identity]
    command = TerminalCommand(
        epoch_id=epoch,
        frontier=1,
        outcome="accepted",
        worker_outcome_id=worker.id,
        state={"id": uuid4(), "sha256": "b" * 64},
    )
    with (
        patch("backend.app.openevolve_services._locked_epoch"),
        patch(
            "backend.app.openevolve_services._validated_population",
            side_effect=[after, before],
        ),
        pytest.raises(DomainError, match="existing native program"),
    ):
        acknowledge_terminal_application(session, fence, command, object())
    session.add.assert_not_called()


def test_native_artifact_must_equal_host_objective_feedback(tmp_path):
    import hashlib
    import json

    from backend.app.openevolve_services import _validated_population
    from packages.openevolve_adapter.checkpoint_native import (
        state_sha256,
        validate_directory,
    )
    from packages.openevolve_adapter.host_contracts import EvidenceRef
    from tests.openevolve.test_checkpoint_native import native

    state = validate_directory(native.__wrapped__(tmp_path))
    program_id = next(iter(state["programs"]))
    program = state["programs"][program_id]
    program["artifacts_json"] = json.dumps({"native_feedback": "invented"})
    fence, session, _, _, _ = setup_receipt()
    epoch = uuid4()
    from uuid import UUID

    candidate = m.SearchCandidate(
        id=uuid4(),
        **vars(fence),
        epoch_id=epoch,
        upstream_program_id=UUID(program_id),
        source_sha256=hashlib.sha256(program["code"].encode()).hexdigest(),
        kind="seed",
    )
    receipt = m.SearchObjectiveReceipt(
        id=uuid4(),
        **vars(fence),
        status="eligible",
        combined_score=-5.0,
        feedback="actual",
        feedback_sha256=hashlib.sha256(b"actual").hexdigest(),
    )
    ack = m.SearchObjectiveAcknowledgement(
        id=uuid4(), **vars(fence), objective_receipt_id=receipt.id
    )
    session.scalar.side_effect = [candidate, ack]
    session.get.side_effect = None
    session.get.return_value = receipt
    with (
        patch("backend.app.openevolve_services._read_evidence", return_value=state),
        pytest.raises(DomainError, match="feedback differs"),
    ):
        _validated_population(
            session,
            fence,
            epoch,
            0,
            EvidenceRef(id=uuid4(), sha256=state_sha256(state)),
            object(),
        )


def test_fixed_profile_prompt_exposure_order_and_dedup_without_rng():
    import random

    from packages.openevolve_adapter.host_contracts import initial_prompt_references

    a, b, c = map(str, [uuid4(), uuid4(), uuid4()])
    programs = {
        a: {"code": "a=1\n", "metrics": {"combined_score": 1.0}},
        b: {"code": "b=2\n", "metrics": {"combined_score": 1.0}},
        c: {"code": "c=3\n", "metrics": {"combined_score": 0.0}},
    }
    selection = {
        "snapshot": {"programs": programs, "islands": [[b, a, c]]},
        "inspiration_ids": [a, c, b],
    }
    state = random.getstate()
    refs = initial_prompt_references(selection)
    assert random.getstate() == state
    assert [(r.role, str(r.upstream_program_id)) for r in refs] == [
        ("previous_summary", b),
        ("top", b),
        ("diverse", a),
        ("inspiration", c),
    ]
    assert refs != () and refs != tuple(reversed(refs)) and refs != refs + (refs[-1],)


def test_epoch_is_bound_to_one_exact_orchestrator_unit():
    sql = str(CreateTable(m.StrategySearchEpoch.__table__).compile(dialect=dialect()))
    assert "UNIQUE (admission_id)" in sql
    assert (
        "research_process_admissions (owner_id, run_id, attempt_id, generation, id)"
        in sql
    )
    assert "bootstrap_sha256 VARCHAR(64) NOT NULL" in sql


def test_candidate_event_from_other_same_scope_unit_rejected_before_replay():
    from backend.app.openevolve_services import register_candidate_intent
    from packages.openevolve_adapter.host_contracts import CandidateIntentCommand

    fence, session, _, _, _ = setup_receipt()
    command = CandidateIntentCommand(
        epoch_id=uuid4(),
        admission_id=uuid4(),
        upstream_program_id=uuid4(),
        phase="seed",
        source={"id": uuid4(), "sha256": "a" * 64},
        ticket_sha256="b" * 64,
        request_sha256="c" * 64,
    )
    with (
        patch(
            "backend.app.openevolve_services._locked_epoch",
            return_value=SimpleNamespace(admission_id=uuid4()),
        ),
        pytest.raises(DomainError, match="another orchestrator"),
    ):
        register_candidate_intent(session, fence, command, object())
    session.scalar.assert_not_called()
    session.add.assert_not_called()


def control_chain():
    seed, first, second = uuid4(), uuid4(), uuid4()
    intent1, intent2 = uuid4(), uuid4()
    kinds = [
        ("seed", 0, seed, None),
        ("sampling", 0, None, intent1),
        ("sampling", 0, None, intent2),
        ("terminal", 1, first, None),
        ("terminal", 2, second, None),
    ]
    rows = []
    for index, (kind, frontier, terminal, intent) in enumerate(kinds, 1):
        rows.append(
            SimpleNamespace(
                id=uuid4(),
                sequence=index,
                previous_id=rows[-1].id if rows else None,
                previous_sequence=index - 1 if rows else None,
                kind=kind,
                frontier=frontier,
                terminal_id=terminal,
                sampling_intent_id=intent,
            )
        )
    return (
        rows,
        [
            SimpleNamespace(id=x, native_iteration=i)
            for i, x in enumerate((intent1, intent2), 1)
        ],
        [SimpleNamespace(id=x) for x in (seed, first, second)],
    )


def test_control_chain_preserves_prefetched_sampling_before_applied_frontier():
    from backend.app.openevolve_services import _validate_control_chain

    rows, intents, terminals = control_chain()
    _validate_control_chain(rows, intents, terminals, rows[-1])
    # Checkpoint at K0 must retain both charged prefetched intent receipts.
    _validate_control_chain(rows[:3], intents, terminals[:1], rows[2])


@pytest.mark.parametrize(
    "fault",
    [
        "missing_sampling",
        "duplicate_sampling",
        "missing_terminal",
        "wrong_previous",
        "skip_sequence",
        "sampling_advances_k",
        "terminal_no_advance",
    ],
)
def test_control_chain_rejects_missing_or_reordered_receipts(fault):
    from backend.app.openevolve_services import _validate_control_chain

    rows, intents, terminals = control_chain()
    if fault == "missing_sampling":
        intents.append(SimpleNamespace(id=uuid4()))
    elif fault == "duplicate_sampling":
        rows[2].sampling_intent_id = rows[1].sampling_intent_id
    elif fault == "missing_terminal":
        terminals.append(SimpleNamespace(id=uuid4()))
    elif fault == "wrong_previous":
        rows[2].previous_id = rows[0].id
    elif fault == "skip_sequence":
        rows[2].sequence += 1
    elif fault == "sampling_advances_k":
        rows[2].frontier += 1
    else:
        rows[3].frontier = 0
    with pytest.raises(DomainError):
        _validate_control_chain(rows, intents, terminals, rows[-1])


def sampling_command():
    from packages.openevolve_adapter.host_contracts import (
        IterationIntentCommand,
        SamplingIntentCommand,
    )

    return SamplingIntentCommand(
        intent=IterationIntentCommand(
            epoch_id=uuid4(),
            native_iteration=1,
            parent_candidate_id=uuid4(),
            parent_source_sha256="a" * 64,
            mutation_reservation_id=uuid4(),
            selection={"id": uuid4(), "sha256": "b" * 64},
        ),
        before_state_sha256="c" * 64,
        after_state={"id": uuid4(), "sha256": "d" * 64},
    )


def test_sampling_replay_requires_exact_atomic_receipt_before_mutable_checks():
    from backend.app.openevolve_services import digest, register_sampling_intent

    fence, session, _, _, _ = setup_receipt()
    command = sampling_command()
    intent = SimpleNamespace(id=uuid4())
    receipt = SimpleNamespace(
        request_sha256=digest(command), state_sha256=command.after_state.sha256
    )
    session.scalar.side_effect = [intent, receipt]
    with (
        patch("backend.app.openevolve_services._locked_epoch"),
        patch("backend.app.openevolve_services._latest_control") as cursor,
    ):
        assert register_sampling_intent(session, fence, command, object()) == (
            intent,
            receipt,
        )
    cursor.assert_not_called()
    session.add.assert_not_called()


@pytest.mark.parametrize(
    "receipt", [None, SimpleNamespace(request_sha256="0" * 64, state_sha256="d" * 64)]
)
def test_sampling_replay_cannot_ack_incomplete_or_different_transition(receipt):
    from backend.app.openevolve_services import register_sampling_intent

    fence, session, _, _, _ = setup_receipt()
    session.scalar.side_effect = [SimpleNamespace(id=uuid4()), receipt]
    with (
        patch("backend.app.openevolve_services._locked_epoch"),
        pytest.raises(DomainError, match="incomplete"),
    ):
        register_sampling_intent(session, fence, sampling_command(), object())
    session.add.assert_not_called()


def test_sampling_cannot_start_from_an_unacknowledged_state():
    from backend.app.openevolve_services import register_sampling_intent

    fence, session, _, _, _ = setup_receipt()
    session.scalar.return_value = None
    with (
        patch("backend.app.openevolve_services._locked_epoch"),
        patch(
            "backend.app.openevolve_services._latest_control",
            return_value=SimpleNamespace(state_sha256="0" * 64),
        ),
        pytest.raises(DomainError, match="unacknowledged"),
    ):
        register_sampling_intent(session, fence, sampling_command(), object())
    session.add.assert_not_called()


def test_sampling_persists_intent_and_control_receipt_in_same_transaction():
    from backend.app.openevolve_services import register_sampling_intent

    fence, session, _, _, _ = setup_receipt()
    command = sampling_command()
    intent = SimpleNamespace(id=uuid4(), epoch_id=command.intent.epoch_id)
    cursor = SimpleNamespace(
        id=uuid4(),
        sequence=1,
        frontier=0,
        state_object_id=uuid4(),
        state_sha256=command.before_state_sha256,
    )
    session.scalar.return_value = None
    selection = {"parent_id": str(uuid4()), "snapshot": {"programs": {}}}
    with (
        patch("backend.app.openevolve_services._locked_epoch"),
        patch("backend.app.openevolve_services._latest_control", return_value=cursor),
        patch(
            "backend.app.openevolve_services._validated_population",
            side_effect=[{"before": 1}, {"after": 1}],
        ),
        patch("backend.app.openevolve_services._read_evidence", return_value=selection),
        patch(
            "packages.openevolve_adapter.sampling_contracts.validate_sampling_transition"
        ) as validate,
        patch(
            "backend.app.openevolve_services._persist_iteration_intent",
            return_value=intent,
        ) as persist,
    ):
        result, receipt = register_sampling_intent(session, fence, command, object())
    assert result is intent
    assert receipt.sampling_intent_id == intent.id
    assert (receipt.sequence, receipt.frontier, receipt.previous_id) == (
        2,
        0,
        cursor.id,
    )
    assert receipt.state_sha256 == command.after_state.sha256
    validate.assert_called_once_with(
        {"before": 1}, {"after": 1}, selection["parent_id"], selection["snapshot"]
    )
    persist.assert_called_once()
    session.add.assert_called_once_with(receipt)
    session.flush.assert_called_once()
    session.commit.assert_not_called()

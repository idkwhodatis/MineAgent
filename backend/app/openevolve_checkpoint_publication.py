"""Finalize uploaded checkpoint against the actual complete retained epoch graph.

This does not make a resumable pointer, stop a process, or refund the interrupted
upload hold. The installed controller receives its ACK only after publication
and the existing full native checkpoint validator commit successfully.
"""

from uuid import UUID

from sqlalchemy import select

from backend.app import openevolve_models as m
from backend.app import openevolve_worker_models as w
from backend.app.openevolve_controller_services import require_search_controller
from backend.app.openevolve_event_identity import exact_event, require_event_ticket
from backend.app.openevolve_event_models import SearchControllerEventReceipt
from backend.app.openevolve_object_publication import publish_search_object
from backend.app.openevolve_operational_models import (
    SearchCheckpointUpload,
    SearchCheckpointUploadCompletion,
)
from backend.app.openevolve_services import (
    _latest_control,
    _owned_row,
    _pending_intent_ids,
    _read_evidence,
    _scope,
    publish_checkpoint_snapshot,
)
from backend.app.research_lifecycle import neutral_transaction
from packages.openevolve_adapter.host_contracts import (
    CheckpointInventory,
    CheckpointPublicationCommand,
    EvidenceRef,
    InitialCheckpointClosure,
)
from packages.openevolve_adapter.observer_transport import canonical
from runtimes.openevolve_operational.wire import Event

TABLES = {
    "controller_event_receipt_ids": SearchControllerEventReceipt,
    "worker_ticket_ids": w.SearchWorkerTicket,
    "worker_expected_identity_ids": w.SearchWorkerExpectedIdentity,
    "worker_assignment_ids": w.SearchWorkerAssignment,
    "worker_assignment_closure_ids": w.SearchWorkerAssignmentClosure,
    "terminal_ids": m.SearchTerminalApplication,
    "control_transition_ids": m.SearchControlTransition,
    "candidate_ids": m.SearchCandidate,
    "objective_receipt_ids": m.SearchObjectiveReceipt,
    "objective_ack_ids": m.SearchObjectiveAcknowledgement,
    "intent_ids": m.SearchIterationIntent,
    "worker_outcome_ids": m.SearchWorkerOutcome,
    "rendered_context_ids": m.SearchRenderedContext,
    "proposal_response_ids": m.SearchProposalResponse,
    "proposal_source_ids": m.SearchProposalSource,
    "evaluation_ids": m.SearchEvaluation,
}


def _sorted(values):
    return tuple(sorted(values, key=str))


def collect_closure(session, fence, epoch, intent):
    control = _latest_control(session, epoch.id)
    if control is None or control.id != intent.control_transition_id:
        raise ValueError("Checkpoint control cursor changed")
    rows = {}
    for name, model in TABLES.items():
        # 64 is the largest finite closure collection. Query one extra so even
        # corrupted historical metadata cannot cause unbounded host allocation.
        values = list(
            session.scalars(
                select(model).where(model.epoch_id == epoch.id).limit(65)
            ).all()
        )
        if len(values) > 64 or any(
            any(getattr(row, key) != value for key, value in _scope(fence).items())
            for row in values
        ):
            raise ValueError("Checkpoint graph exceeds bound or scope")
        rows[name] = values
    evaluation_ids = _sorted(row.id for row in rows["evaluation_ids"])
    outcomes = list(
        session.scalars(
            select(m.SearchEvaluationOutcome)
            .where(m.SearchEvaluationOutcome.evaluation_id.in_(evaluation_ids))
            .limit(65)
        ).all()
    )
    if len(outcomes) > 64 or any(
        any(getattr(row, key) != value for key, value in _scope(fence).items())
        for row in outcomes
    ):
        raise ValueError("Checkpoint outcome bound or scope")
    closed = {row.assignment_id for row in rows["worker_assignment_closure_ids"]}
    return InitialCheckpointClosure(
        epoch_id=epoch.id,
        frontier=intent.frontier,
        spec_sha256=epoch.spec_sha256,
        state_sha256=intent.state_sha256,
        control_sequence=control.sequence,
        **{key: _sorted(row.id for row in value) for key, value in rows.items()},
        sampling_transition_ids=_sorted(
            row.id for row in rows["control_transition_ids"] if row.kind == "sampling"
        ),
        open_worker_assignment_ids=_sorted(
            row.id for row in rows["worker_assignment_ids"] if row.id not in closed
        ),
        pending_intent_ids=_sorted(
            _pending_intent_ids(
                rows["intent_ids"], rows["worker_outcome_ids"], rows["terminal_ids"]
            )
        ),
        evaluation_outcome_ids=_sorted(row.id for row in outcomes),
    )


def _resolve(session, fence, peer, event, token):
    require_event_ticket(session, fence, event)
    _launch, epoch = require_search_controller(
        session, fence, peer, epoch_id=event.epoch_id
    )
    intent = _owned_row(session, m.SearchCheckpointIntent, token, fence)
    if (
        epoch.admission_id != event.admission_id
        or intent.epoch_id != epoch.id
        or (intent.frontier, intent.state_sha256)
        != (event.payload["frontier"], event.payload["state_sha256"])
    ):
        raise ValueError("Checkpoint publication scope or frontier differs")
    upload = session.scalar(
        select(SearchCheckpointUpload).where(
            SearchCheckpointUpload.checkpoint_intent_id == intent.id
        )
    )
    if upload is None:
        raise ValueError("Checkpoint has no admitted upload")
    _owned_row(session, SearchCheckpointUpload, upload.id, fence)
    completion = session.scalar(
        select(SearchCheckpointUploadCompletion).where(
            SearchCheckpointUploadCompletion.upload_id == upload.id
        )
    )
    if completion is None:
        raise ValueError("Checkpoint upload has no durable completion")
    _owned_row(session, SearchCheckpointUploadCompletion, completion.id, fence)
    return (
        epoch,
        intent,
        EvidenceRef(
            id=completion.inventory_object_id, sha256=completion.inventory_sha256
        ),
    )


def finalize_checkpoint(factory, fence, peer, *, event, settings):
    if type(event) is not Event or event.kind != "checkpoint_publish":
        raise ValueError("Exact checkpoint publication event required")
    exact_event(event)
    payload = event.payload
    if (
        set(payload) != {"token", "frontier", "state_sha256", "path"}
        or type(payload["frontier"]) is not int
    ):
        raise ValueError("Closed checkpoint publication fields required")
    token = UUID(payload["token"])
    if (
        str(token) != payload["token"]
        or payload["path"] != f"/work/checkpoints/{token}"
    ):
        raise ValueError("Checkpoint token/path differs")
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        epoch, intent, inventory = _resolve(session, fence, peer, event, token)
        previous = session.scalar(
            select(m.SearchCheckpointPublication).where(
                m.SearchCheckpointPublication.intent_id == token
            )
        )
        if previous is not None:
            _owned_row(session, m.SearchCheckpointPublication, previous.id, fence)
            if (previous.inventory_object_id, previous.inventory_sha256) != (
                inventory.id,
                inventory.sha256,
            ):
                raise ValueError("Checkpoint completed inventory drift")
            for ref, kind in (
                (inventory, "search_checkpoint_inventory"),
                (
                    EvidenceRef(
                        id=previous.closure_object_id, sha256=previous.closure_sha256
                    ),
                    "search_checkpoint_closure",
                ),
            ):
                _read_evidence(session, fence, ref, kind, settings)
            from backend.app.research_object_services import read_research_object
            from packages.openevolve_adapter.checkpoint_evidence import (
                validate_checkpoint_evidence,
            )

            retained = CheckpointInventory.model_validate_json(
                canonical(
                    _read_evidence(
                        session,
                        fence,
                        inventory,
                        "search_checkpoint_inventory",
                        settings,
                    )
                )
            )
            if len(retained.files) > 11:
                raise ValueError("Initial checkpoint file count exceeded")
            files = {
                item.name: read_research_object(
                    session,
                    fence,
                    item.object.id,
                    "search_checkpoint_file",
                    item.object.sha256,
                    settings,
                )
                for item in retained.files
            }
            validate_checkpoint_evidence(retained, files, intent.state_sha256)
            return previous.id, {
                "published": True,
                "frontier": intent.frontier,
                "state_sha256": intent.state_sha256,
            }
        closure = collect_closure(session, fence, epoch, intent)
    obj = publish_search_object(
        factory,
        fence,
        kind="search_checkpoint_closure",
        content=canonical(closure.model_dump(mode="json")),
        logical_key=f"oe-checkpoint-closure:{token}",
        settings=settings,
    )
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        _epoch, intent, current_inventory = _resolve(session, fence, peer, event, token)
        if inventory != current_inventory:
            raise ValueError("Checkpoint inventory changed during publication")
        receipt = publish_checkpoint_snapshot(
            session,
            fence,
            CheckpointPublicationCommand(
                epoch_id=event.epoch_id,
                intent_id=token,
                inventory=inventory,
                closure=EvidenceRef(id=obj.id, sha256=obj.sha256),
            ),
            settings,
        )
        receipt_id = receipt.id
        answer = {
            "published": True,
            "frontier": intent.frontier,
            "state_sha256": intent.state_sha256,
        }
    return receipt_id, answer

"""Fresh retained evidence/whole-unit stop closure before initial search success.

No live-controller claim is needed after execution: only current fence and actual
immutable journals/bytes. This function never launches, restores or fabricates a
missing receipt. Unknown physical obligations remain charged and nonterminal.
"""

import hashlib
import json

from sqlalchemy import select

from backend.app import openevolve_models as m
from backend.app.models import ExecutionAttempt, ResearchRun, utcnow
from backend.app.openevolve_native_evidence import read_native_evidence
from backend.app.openevolve_operational_models import (
    SearchCheckpointUpload,
    SearchCheckpointUploadCompletion,
    SearchEvaluationPreparation,
)
from backend.app.openevolve_services import (
    _locked_epoch,
    _scope,
    revalidate_current_checkpoint,
)
from backend.app.research_broker_models import (
    ResearchBudgetReservation,
    ResearchModelOperation,
)
from backend.app.research_broker_services import settle_budget
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.research_object_models import (
    ResearchObjectOutcome,
    ResearchObjectPublication,
)
from backend.app.research_object_services import (
    _read_exact,
    publication_journal,
    read_research_object,
)
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
    ResearchProcessState,
)
from packages.openevolve_adapter.host_contracts import (
    CheckpointPublicationCommand,
    EvidenceRef,
)
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_persistence_codec import ROW_FIELDS, decode_row
from runtimes.openevolve_operational.process_journal import read_controller_journal


def _rows(session, model, fence):
    result = session.scalars(select(model).filter_by(**_scope(fence)).limit(4097)).all()
    if len(result) > 4096:
        raise ValueError("Bounded initial retained closure exceeded")
    return result


def complete_initial_epoch(
    session,
    fence,
    *,
    epoch_id,
    frontier,
    controller_admission_id,
    actual_exit_code,
    original_deadline_completion,
    settings,
):
    require_neutral_transaction(session, fence, resource_scope="processes")
    # Caller values are internal host observations, never accepted from RPC. The
    # positive containment itself is independently reread below from real files.
    if (
        original_deadline_completion is not True
        or type(actual_exit_code) is not int
        or actual_exit_code != 124
        or type(frontier) is not int
        or not 1 <= frontier <= 2
    ):
        raise ValueError("Actual original protected deadline completion required")
    epoch = _locked_epoch(session, fence, epoch_id)
    if epoch.admission_id != controller_admission_id:
        raise ValueError("Controller epoch differs")
    latest_intent = session.scalar(
        select(m.SearchCheckpointIntent)
        .filter_by(**_scope(fence), epoch_id=epoch_id)
        .order_by(m.SearchCheckpointIntent.save_ordinal.desc())
        .limit(1)
    )
    if (
        latest_intent is None
        or type(latest_intent.save_ordinal) is not int
        or not 1 <= latest_intent.save_ordinal <= 4
        or latest_intent.frontier != frontier
    ):
        raise ValueError("Latest actual save must cover final frontier")
    publications = session.scalars(
        select(m.SearchCheckpointPublication)
        .filter_by(
            **_scope(fence),
            epoch_id=epoch_id,
            intent_id=latest_intent.id,
            frontier=frontier,
        )
        .limit(2)
    ).all()
    if len(publications) != 1:
        raise ValueError("Exactly one latest final retained checkpoint required")
    checkpoint = publications[0]
    revalidate_current_checkpoint(
        session,
        fence,
        CheckpointPublicationCommand(
            epoch_id=epoch_id,
            intent_id=checkpoint.intent_id,
            inventory=EvidenceRef(
                id=checkpoint.inventory_object_id, sha256=checkpoint.inventory_sha256
            ),
            closure=EvidenceRef(
                id=checkpoint.closure_object_id, sha256=checkpoint.closure_sha256
            ),
        ),
        settings,
    )
    admissions = _rows(session, ResearchProcessAdmission, fence)
    if not admissions:
        raise ValueError("No whole-unit physical closure")
    for row in admissions:
        state = session.get(ResearchProcessState, row.id)
        stop = session.scalar(
            select(ResearchProcessReceipt).filter_by(admission_id=row.id, kind="stop")
        )
        if (
            state is None
            or state.status != "stopped"
            or stop is None
            or any(getattr(stop, k) != v for k, v in _scope(fence).items())
        ):
            raise ValueError("Unresolved actual process obligation")
        if row.id == controller_admission_id:
            admission = decode_row({key: getattr(row, key) for key in ROW_FIELDS})
            _launch, _identity, child, actual_stop = read_controller_journal(
                settings.storage_root, admission, with_child=True, with_stop=True
            )
            if child is None or actual_stop.model_dump(mode="json") != stop.payload:
                raise ValueError("Controller positive stop/child journal differs")
        else:
            stop_id, _evidence = read_native_evidence(session, fence, row.id, settings)
            if stop_id != stop.id:
                raise ValueError("Actual native stop identity differs")
    # Fresh bytes and journals for every charged publication in this attempt,
    # including model responses and checkpoint chunks, not only final inventory.
    total = 0
    for publication in _rows(session, ResearchObjectPublication, fence):
        outcome = session.get(ResearchObjectOutcome, publication.id)
        if outcome is None or (
            outcome.owner_id,
            outcome.object_kind,
            outcome.sha256,
        ) != (fence.owner_id, publication.object_kind, publication.sha256):
            raise ValueError("Pending object publication prevents success")
        journal = publication_journal(publication)
        journal_sha = hashlib.sha256(journal).hexdigest()
        if journal_sha != outcome.journal_sha256:
            raise ValueError("Retained publication journal digest differs")
        _read_exact(
            settings.storage_root,
            f"research-objects/{fence.owner_id}/journals/{publication.id}.json",
            journal_sha,
            len(journal),
        )
        raw = read_research_object(
            session,
            fence,
            outcome.object_id,
            outcome.object_kind,
            outcome.sha256,
            settings,
        )
        if len(raw) != publication.size_bytes:
            raise ValueError("Actual retained object size differs")
        total += len(raw) + len(journal)
        if total > settings.neutral_object_installation_max_bytes:
            raise ValueError("Bounded retained read bytes exceeded")
    evaluations = {row.id for row in _rows(session, m.SearchEvaluation, fence)}
    outcomes = {
        row.evaluation_id for row in _rows(session, m.SearchEvaluationOutcome, fence)
    }
    if any(
        row.id not in evaluations or row.id not in outcomes
        for row in _rows(session, SearchEvaluationPreparation, fence)
    ):
        raise ValueError("Pending physical preparation prevents success")
    if any(
        row.state != "completed"
        for row in _rows(session, ResearchModelOperation, fence)
    ):
        raise ValueError("Unresolved model operation prevents success")
    # Full conservative charge after actual whole-unit stop; never refund an
    # upload or mutation slot merely because its publisher/controller exited.
    consumers = []
    for intent in _rows(session, m.SearchIterationIntent, fence):
        terminal = session.scalar(
            select(m.SearchTerminalApplication).filter_by(
                epoch_id=epoch_id, frontier=intent.native_iteration
            )
        )
        if terminal is None:
            raise ValueError("Unapplied mutation intent prevents success")
        consumers.append(
            (
                intent.mutation_reservation_id,
                "engine_mutation",
                f"oe-mutation:{epoch_id}:{intent.native_iteration}",
                BudgetVector(mutation_slots=1),
            )
        )
    for upload in _rows(session, SearchCheckpointUpload, fence):
        done = session.scalar(
            select(SearchCheckpointUploadCompletion).filter_by(upload_id=upload.id)
        )
        published = session.scalar(
            select(m.SearchCheckpointPublication).filter_by(
                intent_id=upload.checkpoint_intent_id
            )
        )
        if done is None or published is None:
            raise ValueError("Unpublished checkpoint transfer prevents success")

        from packages.openevolve_adapter.upload_contracts import Begin

        declaration = Begin.model_validate_json(json.dumps(upload.inventory))
        consumers.append(
            (
                upload.reservation_id,
                "object_storage",
                f"oe-upload:{upload.checkpoint_intent_id}",
                BudgetVector(storage_bytes=declaration.transfer_peak_bytes),
            )
        )
    for identity, purpose, logical_key, amounts in consumers:
        held = session.get(ResearchBudgetReservation, identity)
        if (
            held is None
            or held.purpose != purpose
            or held.logical_key != logical_key
            or BudgetVector.model_validate(held.amounts) != amounts
            or any(getattr(held, k) != v for k, v in _scope(fence).items())
        ):
            raise ValueError("Exact conservative consumer reservation required")
        settle_budget(
            session,
            fence,
            held.id,
            charge=BudgetVector.model_validate(held.amounts),
            outcome="conservative_unknown",
        )
    if any(
        row.state != "settled"
        for row in _rows(session, ResearchBudgetReservation, fence)
    ):
        raise ValueError("Unresolved accounting prevents success")
    run, attempt = (
        session.get(ResearchRun, fence.run_id),
        session.get(ExecutionAttempt, fence.attempt_id),
    )
    run.status = attempt.status = "succeeded"
    attempt.sandbox_stopped, attempt.exit_code = True, actual_exit_code
    attempt.finished_at = utcnow()
    from backend.app.research_lifecycle import _NEUTRAL_KEY

    session.info[_NEUTRAL_KEY]["terminal"] = (
        "oe_initial_succeeded",
        run.id,
        attempt.id,
        attempt.finished_at,
        actual_exit_code,
    )
    session.flush()
    return checkpoint.id

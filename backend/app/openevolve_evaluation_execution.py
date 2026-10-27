"""Actual fixed native evaluator orchestration, not a public launch route.

A current protected controller/assigned worker requests a retained candidate.
Every new unit is budgeted, committed, single-use claimed and reauthorized at
create/start. True completion evidence is persisted fence-only after submission;
a dying controller never turns a real completed exchange into a fake refund.
"""

import hashlib

from sqlalchemy import select

from backend.app.core.maintenance import shared_lifecycle
from backend.app.openevolve_candidate_events import resolve_evaluation_peer
from backend.app.openevolve_controller_services import typed_bootstrap
from backend.app.openevolve_evaluation_admission import (
    admit_prepared_input,
    prepare_evaluation,
    render_prepared_input,
)
from backend.app.openevolve_models import (
    SearchEvaluation,
    SearchEvaluationOutcome,
)
from backend.app.openevolve_native_evidence import read_native_evidence
from backend.app.openevolve_native_process import (
    check_native_claim,
    claim_native_launch,
    record_native_identity,
    record_native_stopped,
    settle_native_conservatively,
)
from backend.app.openevolve_object_publication import publish_search_object
from backend.app.openevolve_operational_models import SearchControllerLaunch
from backend.app.openevolve_services import (
    _owned_row,
    record_objective_receipt,
    record_physical_outcome,
)
from backend.app.research_lifecycle import neutral_transaction
from backend.app.research_object_services import read_research_object
from packages.openevolve_adapter.evaluator_protocol import EvaluationResponse
from packages.openevolve_adapter.host_contracts import (
    EvidenceRef,
    ObjectiveReceiptCommand,
    PhysicalOutcomeCommand,
)
from packages.openevolve_adapter.objective import native_observation, score
from packages.openevolve_adapter.observer_transport import canonical
from packages.research_runtime.broker_contracts import digest
from runtimes.research_supervisor.openevolve_native_supervisor import (
    NativeSupervisor,
    _mint_native_authority,
)


def verify_native_runtime_binding(runtime):
    from pathlib import Path

    from runtimes.openevolve_evaluator.recipe import wrapper_inventory_sha256
    from runtimes.openevolve_operational.fixed_input import read_fixed

    lock = Path(__file__).parents[2] / "runtimes/evaluator/requirements.lock"
    if (
        runtime.wrapper_inventory_sha256 != wrapper_inventory_sha256()
        or runtime.dependency_lock_sha256
        != hashlib.sha256(read_fixed(lock, 1048576)).hexdigest()
    ):
        raise ValueError("Native runtime differs from fixed accepted build inputs")


def _scope(factory, fence, peer, event, key):
    with neutral_transaction(factory, fence, resource_scope="processes") as session:
        candidate, _grant, _worker = resolve_evaluation_peer(
            session, fence, peer, event=event, ticket_key=key
        )
        launch = session.scalar(
            select(SearchControllerLaunch).where(
                SearchControllerLaunch.epoch_id == candidate.epoch_id
            )
        )
        if launch is None:
            raise ValueError("Native execution bootstrap absent")
        _owned_row(session, SearchControllerLaunch, launch.id, fence)
        bootstrap, _raw = typed_bootstrap(launch)
        verify_native_runtime_binding(bootstrap.native_runtime)
        return candidate.id, bootstrap


def _persist_result(factory, fence, admission_id, settings):
    with neutral_transaction(factory, fence, resource_scope="processes") as session:
        stop_id, evidence = read_native_evidence(session, fence, admission_id, settings)
        evaluation = session.scalar(
            select(SearchEvaluation).where(
                SearchEvaluation.process_admission_id == admission_id
            )
        )
        if evaluation is None:
            raise ValueError("Physical unit lacks exact evaluation")
        _owned_row(session, SearchEvaluation, evaluation.id, fence)
        eid, epoch_id = evaluation.id, evaluation.epoch_id
    observed = evidence["schema_version"] == "mineagent.native-observation-object.v1"
    raw = canonical(evidence)
    obj = publish_search_object(
        factory,
        fence,
        kind="search_native_observation" if observed else "search_native_failure",
        content=raw,
        logical_key=f"oe-native-outcome:{eid}",
        settings=settings,
    )
    with neutral_transaction(factory, fence, resource_scope="processes") as session:
        command = PhysicalOutcomeCommand(
            epoch_id=epoch_id,
            evaluation_id=eid,
            stop_receipt_id=stop_id,
            status="observed" if observed else "failed",
            evidence=EvidenceRef(id=obj.id, sha256=obj.sha256),
            request_sha256=hashlib.sha256(
                canonical(
                    {
                        "evaluation_id": str(eid),
                        "stop_receipt_id": str(stop_id),
                        "evidence_sha256": obj.sha256,
                    }
                )
            ).hexdigest(),
        )
        row = record_physical_outcome(session, fence, command, settings)
        return row.id


def execute_window(factory, fence, peer, *, event, ticket_key, window_id, settings):
    candidate_id, bootstrap = _scope(factory, fence, peer, event, ticket_key)
    with neutral_transaction(factory, fence, resource_scope="processes") as session:
        previous = session.scalar(
            select(SearchEvaluation).where(
                SearchEvaluation.candidate_id == candidate_id,
                SearchEvaluation.window_id == window_id,
            )
        )
        if previous is not None:
            _owned_row(session, SearchEvaluation, previous.id, fence)
            outcome = session.scalar(
                select(SearchEvaluationOutcome).where(
                    SearchEvaluationOutcome.evaluation_id == previous.id
                )
            )
            if outcome is None:
                raise ValueError(
                    "Existing physical evaluation requires containment/recovery, never a repeated create"
                )
            _owned_row(session, SearchEvaluationOutcome, outcome.id, fence)
            actual_stop, actual = read_native_evidence(
                session, fence, previous.process_admission_id, settings
            )
            raw = read_research_object(
                session,
                fence,
                outcome.bundle_object_id,
                outcome.bundle_kind,
                outcome.bundle_sha256,
                settings,
            )
            if actual_stop != outcome.stop_receipt_id or raw != canonical(actual):
                raise ValueError(
                    "Repeated native read differs from actual retained process output"
                )
            return previous.id
    preparation_id = prepare_evaluation(
        factory,
        fence,
        peer,
        event=event,
        ticket_key=ticket_key,
        window_id=window_id,
        settings=settings,
    )
    manifest, inputs = render_prepared_input(
        factory,
        fence,
        peer,
        event=event,
        ticket_key=ticket_key,
        preparation_id=preparation_id,
        settings=settings,
    )
    intent = admit_prepared_input(
        factory,
        fence,
        peer,
        event=event,
        ticket_key=ticket_key,
        preparation_id=preparation_id,
        manifest=manifest,
        settings=settings,
    )
    runtime = bootstrap.native_runtime
    supervisor = NativeSupervisor(
        runtime.protected_image_id, runtime.native_parent_image_id
    )
    inventory = supervisor.verified_inventory("native")
    with neutral_transaction(factory, fence, resource_scope="processes") as session:
        resolve_evaluation_peer(
            session, fence, peer, event=event, ticket_key=ticket_key
        )
        claimed = claim_native_launch(session, fence, intent.id, settings)
        if claimed != intent:
            raise ValueError("Native claim changed exact intent")
    authority = _mint_native_authority(intent, manifest, inputs, inventory)

    def before_submission():
        with neutral_transaction(factory, fence, resource_scope="processes") as session:
            resolve_evaluation_peer(
                session, fence, peer, event=event, ticket_key=ticket_key
            )
            check_native_claim(
                session, fence, intent.id, expected_intent=intent, settings=settings
            )

    def on_identity(_observed):
        # Read the protected journal again; callback data is not authority.
        with neutral_transaction(factory, fence, resource_scope="processes") as session:
            record_native_identity(
                session, fence, intent.id, storage_root=settings.storage_root
            )

    # Any failure leaves its single-use claim and full hold discoverable. The
    # supervisor performs bounded containment; this path never reruns Docker to
    # guess whether an uncertain create/start happened or manufactures a stop.
    supervisor.execute(
        settings.storage_root,
        authority,
        on_identity=on_identity,
        before_submission=before_submission,
    )
    with neutral_transaction(factory, fence, resource_scope="processes") as session:
        record_native_stopped(
            session,
            fence,
            intent.id,
            storage_root=settings.storage_root,
            with_output=True,
        )
        settle_native_conservatively(
            session, fence, intent.id, storage_root=settings.storage_root
        )
    _persist_result(factory, fence, intent.id, settings)
    return preparation_id


def objective_document(spec, candidate, evaluations, outcomes, evidence):
    observations = []
    for evaluation, outcome, document, window in zip(
        evaluations, outcomes, evidence, spec.objective.windows, strict=True
    ):
        if (
            evaluation.window_id != window.id
            or evaluation.candidate_id != candidate.id
            or evaluation.source_sha256 != candidate.source_sha256
        ):
            raise ValueError("Exact ordered native objective scope required")
        if outcome.status == "observed":
            observations.append(
                native_observation(
                    document["bundle"],
                    window_id=window.id,
                    evaluation_id=evaluation.id,
                    expected_bundle_sha256=outcome.native_bundle_sha256,
                )
            )
    if any(outcome.status == "failed" for outcome in outcomes):
        status, value, reason = "execution_failed", None, "native_error"
    else:
        computed = score(spec.objective, tuple(observations))
        status = (
            "eligible"
            if computed.eligible
            else (
                "constraint_failed"
                if computed.reason == "explicit_trade_constraint"
                else "undefined_objective"
            )
        )
        value, reason = computed.combined_score, computed.reason
    feedback = canonical(
        {"status": status, "combined_score": value, "reason": reason}
    ).decode()
    return {
        "schema_version": "mineagent.search-objective-object.v1",
        "candidate_id": str(candidate.id),
        "source_sha256": candidate.source_sha256,
        "objective_sha256": digest(spec.objective),
        "evaluation_ids": [str(e.id) for e in evaluations],
        "status": status,
        "combined_score": value,
        "reason": reason,
        "feedback": feedback,
        "feedback_sha256": hashlib.sha256(feedback.encode()).hexdigest(),
    }


def execute_candidate(factory, fence, peer, *, event, ticket_key, settings):
    # Maintenance FD is retained across all short transactions and external I/O.
    with shared_lifecycle(factory.kw["bind"], settings.storage_root):
        candidate_id, bootstrap = _scope(factory, fence, peer, event, ticket_key)
        identities = tuple(
            execute_window(
                factory,
                fence,
                peer,
                event=event,
                ticket_key=ticket_key,
                window_id=window.id,
                settings=settings,
            )
            for window in bootstrap.spec.objective.windows
        )
        with neutral_transaction(factory, fence, resource_scope="processes") as session:
            candidate, _grant, _worker = resolve_evaluation_peer(
                session, fence, peer, event=event, ticket_key=ticket_key
            )
            evaluations, outcomes, documents = [], [], []
            for eid in identities:
                evaluation = _owned_row(session, SearchEvaluation, eid, fence)
                outcome = session.scalar(
                    select(SearchEvaluationOutcome).where(
                        SearchEvaluationOutcome.evaluation_id == eid
                    )
                )
                if outcome is None:
                    raise ValueError("Objective requires all actual physical outcomes")
                _owned_row(session, SearchEvaluationOutcome, outcome.id, fence)
                stop_id, document = read_native_evidence(
                    session, fence, evaluation.process_admission_id, settings
                )
                if stop_id != outcome.stop_receipt_id or canonical(
                    document
                ) != read_research_object(
                    session,
                    fence,
                    outcome.bundle_object_id,
                    outcome.bundle_kind,
                    outcome.bundle_sha256,
                    settings,
                ):
                    raise ValueError("Objective physical reread differs")
                evaluations.append(evaluation)
                outcomes.append(outcome)
                documents.append(document)
            value = objective_document(
                bootstrap.spec, candidate, evaluations, outcomes, documents
            )
        obj = publish_search_object(
            factory,
            fence,
            kind="search_objective",
            content=canonical(value),
            logical_key=f"oe-objective:{candidate_id}",
            settings=settings,
        )
        with neutral_transaction(factory, fence, resource_scope="objects") as session:
            resolve_evaluation_peer(
                session, fence, peer, event=event, ticket_key=ticket_key
            )
            row = record_objective_receipt(
                session,
                fence,
                ObjectiveReceiptCommand(
                    epoch_id=event.epoch_id,
                    candidate_id=candidate_id,
                    evaluation_ids=identities,
                    evidence=EvidenceRef(id=obj.id, sha256=obj.sha256),
                ),
                settings,
            )
            answer = EvaluationResponse(
                candidate_id=candidate_id,
                source_sha256=row.source_sha256,
                objective_sha256=row.objective_sha256,
                objective_receipt_id=row.id,
                eligible=row.status == "eligible",
                combined_score=row.combined_score,
                reason="eligible"
                if row.status == "eligible"
                else (
                    "native_error"
                    if row.status == "execution_failed"
                    else (
                        "minimum_trades"
                        if row.status == "constraint_failed"
                        else "native_undefined"
                    )
                ),
                feedback=row.feedback,
            ).model_dump(mode="json")
            receipt = row.id
        return receipt, answer

"""Durable one-window preparation→actual retained inventory→native admission.

The preparation hold is committed before reading/rendering any dataset input.
Rendering is bounded memory only; the separately accepted supervisor owns the
sole physical staging/create sequence. Unknown preparation is never refunded.
"""

import hashlib
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from backend.app.models import DatasetVersion, utcnow
from backend.app.openevolve_candidate_events import resolve_evaluation_peer
from backend.app.openevolve_controller_services import typed_bootstrap
from backend.app.openevolve_models import SearchEvaluation
from backend.app.openevolve_native_process import FLOOR, persist_native_intent
from backend.app.openevolve_object_publication import publish_search_object
from backend.app.openevolve_operational_models import (
    SearchControllerLaunch,
    SearchEvaluationInput,
    SearchEvaluationPreparation,
)
from backend.app.openevolve_services import _owned_row, _scope
from backend.app.research_broker_services import require_reservation, reserve_budget
from backend.app.research_lifecycle import neutral_transaction
from backend.app.research_object_services import read_research_object
from packages.openevolve_adapter.host_contracts import SourceEnvelope
from packages.research_runtime.broker_contracts import digest as document_digest
from packages.research_runtime.process_contracts import canonical, digest
from packages.research_runtime.process_declarations_v2 import (
    NoIPC,
    ProcessDeclarationV2,
)
from runtimes.openevolve_evaluator.contracts import (
    PROFILE,
    NativeInputFile,
    NativeInputManifest,
    check_input_bytes,
)
from runtimes.openevolve_evaluator.journal import NativeInputBinding, NativeIntent
from workers.openevolve.staging import render_search_input


def _resolve(session, fence, peer, event, ticket_key, window_id):
    if event.kind != "execute_evaluation" or set(event.payload) != {"grant"}:
        raise ValueError("Closed evaluation event required")
    candidate, grant, worker = resolve_evaluation_peer(
        session, fence, peer, event=event, ticket_key=ticket_key
    )
    launch = session.scalar(
        select(SearchControllerLaunch).where(
            SearchControllerLaunch.epoch_id == candidate.epoch_id
        )
    )
    if launch is None:
        raise ValueError("Evaluation lacks persisted bootstrap")
    _owned_row(session, SearchControllerLaunch, launch.id, fence)
    bootstrap, _raw = typed_bootstrap(launch)
    window = next(
        (w for w in bootstrap.spec.execution.windows if w.id == window_id), None
    )
    if window is None:
        raise ValueError("Window outside immutable development plan")
    return candidate, grant, worker, bootstrap, window


def _held(session, fence, preparation):
    row = require_reservation(
        session,
        fence,
        preparation.reservation_id,
        purpose="process_unit",
        minimum=FLOOR,
    )
    if (
        row.logical_key != f"oe-native:{preparation.id}:1"
        or row.amounts != FLOOR.model_dump()
    ):
        raise ValueError("Preparation reservation identity differs")
    return row


def prepare_evaluation(factory, fence, peer, *, event, ticket_key, window_id, settings):
    with neutral_transaction(factory, fence, resource_scope="processes") as session:
        candidate, _grant, _worker, bootstrap, window = _resolve(
            session, fence, peer, event, ticket_key, window_id
        )
        previous = session.scalar(
            select(SearchEvaluationPreparation).where(
                SearchEvaluationPreparation.epoch_id == candidate.epoch_id,
                SearchEvaluationPreparation.candidate_id == candidate.id,
                SearchEvaluationPreparation.window_id == window.id,
            )
        )
        if previous is not None:
            _owned_row(session, SearchEvaluationPreparation, previous.id, fence)
            if (
                previous.source_sha256 != candidate.source_sha256
                or previous.execution_sha256
                != document_digest(bootstrap.spec.execution)
            ):
                raise ValueError("Preparation immutable replay differs")
            # Existing records are a reconciliation identity only, never a new
            # physical launch grant or a reset of a depleted reservation.
            return previous.id
        identity = uuid4()
        reservation = reserve_budget(
            session,
            fence,
            purpose="process_unit",
            settings=settings,
            logical_key=f"oe-native:{identity}:1",
            amounts=FLOOR,
        )
        row = SearchEvaluationPreparation(
            id=identity,
            **_scope(fence),
            epoch_id=candidate.epoch_id,
            candidate_id=candidate.id,
            source_sha256=candidate.source_sha256,
            window_id=window.id,
            execution_sha256=document_digest(bootstrap.spec.execution),
            admission_id=uuid4(),
            reservation_id=reservation.id,
            logical_ticket_id=uuid4(),
            physical_ordinal=1,
        )
        session.add(row)
        session.flush()
    return identity


def render_prepared_input(
    factory, fence, peer, *, event, ticket_key, preparation_id, settings
):
    with neutral_transaction(factory, fence, resource_scope="processes") as session:
        preparation = _owned_row(
            session, SearchEvaluationPreparation, preparation_id, fence
        )
        candidate, _grant, _worker, bootstrap, window = _resolve(
            session, fence, peer, event, ticket_key, preparation.window_id
        )
        if (
            preparation.epoch_id,
            preparation.candidate_id,
            preparation.source_sha256,
            preparation.execution_sha256,
        ) != (
            candidate.epoch_id,
            candidate.id,
            candidate.source_sha256,
            document_digest(bootstrap.spec.execution),
        ):
            raise ValueError("Prepared input differs from exact candidate/window")
        _held(session, fence, preparation)
        if session.get(SearchEvaluation, preparation.id) is not None:
            raise ValueError(
                "Admitted evaluation requires reconciliation, not restaging"
            )
        plan = bootstrap.spec.execution
        dataset = session.get(DatasetVersion, plan.dataset_version_id)
        if (
            dataset is None
            or dataset.owner_id != fence.owner_id
            or dataset.content_sha256 != plan.dataset_sha256
            or dataset.storage_key != f"datasets/{fence.owner_id}/{dataset.id}"
        ):
            raise ValueError("Exact owner dataset storage required")
        dataset_manifest, storage_key = dataset.manifest, dataset.storage_key
        source_raw = read_research_object(
            session,
            fence,
            candidate.source_object_id,
            "search_source",
            candidate.source_object_sha256,
            settings,
        )
        source = SourceEnvelope.model_validate_json(source_raw)
        if source.source_sha256 != candidate.source_sha256:
            raise ValueError("Retained candidate source differs")
        source_bytes = source.source.encode()
        evaluation_id = preparation.id
    _report, files = render_search_input(
        plan=plan,
        inputs=bootstrap.native_inputs,
        window=window,
        manifest=dataset_manifest,
        storage_root=settings.storage_root,
        dataset_storage_key=storage_key,
        source=source_bytes,
        source_sha256=source.source_sha256,
        max_bytes=64 * 1024**2,
    )
    manifest = NativeInputManifest(
        evaluation_id=evaluation_id,
        execution_plan_sha256=document_digest(plan),
        source_sha256=source.source_sha256,
        window_start=window.start,
        window_end=window.end,
        files=tuple(
            NativeInputFile(
                path=name, sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw)
            )
            for name, raw in sorted(files.items())
        ),
    )
    inputs = {f"/input/{name}": raw for name, raw in files.items()}
    check_input_bytes(manifest, files)
    return manifest, inputs


def admit_prepared_input(
    factory, fence, peer, *, event, ticket_key, preparation_id, manifest, settings
):
    manifest = NativeInputManifest.model_validate(manifest)
    if manifest.evaluation_id != preparation_id:
        raise ValueError("Prepared manifest evaluation differs")
    raw = canonical(manifest.model_dump(mode="json"))
    # Validate current phase before the independently charged evidence write.
    with neutral_transaction(factory, fence, resource_scope="processes") as session:
        preparation = _owned_row(
            session, SearchEvaluationPreparation, preparation_id, fence
        )
        _resolve(session, fence, peer, event, ticket_key, preparation.window_id)
        _held(session, fence, preparation)
    obj = publish_search_object(
        factory,
        fence,
        kind="search_evaluation_input",
        content=raw,
        logical_key=f"oe-native-input:{preparation_id}",
        settings=settings,
    )
    with neutral_transaction(factory, fence, resource_scope="processes") as session:
        preparation = _owned_row(
            session, SearchEvaluationPreparation, preparation_id, fence
        )
        candidate, _grant, _worker, bootstrap, window = _resolve(
            session, fence, peer, event, ticket_key, preparation.window_id
        )
        _held(session, fence, preparation)
        if session.get(SearchEvaluation, preparation.id) is not None:
            raise ValueError("Evaluation already admitted; never repeat create")
        if (
            preparation.candidate_id,
            preparation.source_sha256,
            preparation.execution_sha256,
            manifest.source_sha256,
            manifest.execution_plan_sha256,
            manifest.window_start,
            manifest.window_end,
        ) != (
            candidate.id,
            candidate.source_sha256,
            document_digest(bootstrap.spec.execution),
            candidate.source_sha256,
            document_digest(bootstrap.spec.execution),
            window.start,
            window.end,
        ):
            raise ValueError("Prepared native immutable input differs")
        issued = utcnow()
        if bootstrap.not_after < issued + timedelta(seconds=61):
            raise ValueError(
                "Controller original deadline cannot cover a new evaluator"
            )
        binding = NativeInputBinding(
            evaluation_id=preparation.id,
            manifest_object_id=obj.id,
            manifest_sha256=obj.sha256,
            manifest_size_bytes=len(raw),
            native_artifacts_sha256=manifest.native_artifacts_sha256,
            execution_plan_sha256=manifest.execution_plan_sha256,
            source_sha256=manifest.source_sha256,
        )
        admission = ProcessDeclarationV2(
            schema_version="mineagent.process-admission.v2",
            id=preparation.admission_id,
            **_scope(fence),
            role="native_evaluator",
            logical_operation_id=preparation.id,
            launch_ordinal=1,
            profile=PROFILE,
            input_sha256=digest(binding.model_dump(mode="json")),
            reservation_id=preparation.reservation_id,
            image_id=bootstrap.native_runtime.protected_image_id,
            ipc=NoIPC(ipc_policy="none", ticket_sha256=None),
        )
        intent = NativeIntent(
            id=admission.id,
            admission=admission,
            inputs=binding,
            issued_at=issued,
            not_after=issued + timedelta(seconds=60),
        )
        evaluation = SearchEvaluation(
            id=preparation.id,
            **_scope(fence),
            epoch_id=candidate.epoch_id,
            candidate_id=candidate.id,
            original_strategy_id=None,
            prepared_strategy_id=None,
            phase=candidate.kind,
            source_sha256=candidate.source_sha256,
            window_id=window.id,
            execution_sha256=preparation.execution_sha256,
            input_manifest_sha256=manifest.native_artifacts_sha256,
            logical_ticket_id=preparation.logical_ticket_id,
            physical_ordinal=1,
            reservation_id=preparation.reservation_id,
            process_admission_id=admission.id,
        )
        retained = SearchEvaluationInput(
            id=uuid4(),
            **_scope(fence),
            epoch_id=candidate.epoch_id,
            evaluation_id=evaluation.id,
            manifest_object_id=obj.id,
            manifest_kind="search_evaluation_input",
            manifest_sha256=obj.sha256,
            manifest_size_bytes=len(raw),
        )
        session.add_all([evaluation, retained])
        session.flush()
        persist_native_intent(session, fence, intent, settings)
    return intent

"""Caller-transaction deterministic external stages; no implicit commit or grant.

Prepare/start are separately committed by the dispatcher. Completion re-reads raw
bytes and computes its outputs, never accepting caller outputs or success flags.
All publication occurs within the existing quota/lifecycle admission. Activation
remains closed until workflow/input schemas and physical inventory are assembled.
"""

from uuid import uuid4

from sqlalchemy import select

from backend.app.models import utcnow
from backend.app.research_input_models import (
    ResearchInputDerivation,
    ResearchInputDerivationEvent,
    ResearchInputObject,
    ResearchInputRecord,
    ResearchInputVersion,
)
from backend.app.research_input_storage import (
    publish_input_object,
    read_input_object,
    require_input_capability,
)
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_workflow_control import require_schema
from backend.app.research_workflow_models import ResearchWorkflow, ResearchWorkflowStage
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_inputs.contracts import InputVersionRef
from packages.research_inputs.derivation import (
    DerivationEvent,
    ExternalNormalizationDefinition,
    validate_event_prefix,
)
from packages.research_inputs.normalization import (
    NormalizationError,
    normalize_external_json,
)
from packages.strategy_sdk import JsonLimits, parse_json


def fail():
    raise DomainError(
        "input_derivation_changed", "Input stage or retained evidence changed", 409
    )


def current_stage(session, owner_id, workflow_id, stage_id):
    require_input_capability(session)
    require_schema(session)
    _try_neutral_lock(session, f"submit:{owner_id}")
    root = session.scalar(
        select(ResearchWorkflow)
        .where(
            ResearchWorkflow.id == workflow_id, ResearchWorkflow.owner_id == owner_id
        )
        .with_for_update(nowait=True)
    )
    stage = session.scalar(
        select(ResearchWorkflowStage).where(
            ResearchWorkflowStage.id == stage_id,
            ResearchWorkflowStage.owner_id == owner_id,
            ResearchWorkflowStage.workflow_id == workflow_id,
        )
    )
    if (
        root is None
        or stage is None
        or root.current_stage_id != stage_id
        or root.status
        not in {"ready", "running", "pause_requested", "cancel_requested"}
    ):
        fail()
    if stage.kind != "external_normalization" or stage.child_run_id is not None:
        fail()
    definition = ExternalNormalizationDefinition.model_validate_json(
        stage.definition_canonical
    )
    if (
        canonical_bytes(definition).decode() != stage.definition_canonical
        or digest(definition) != stage.definition_sha256
    ):
        fail()
    return root, stage, definition


def events_for(session, derivation):
    rows = session.scalars(
        select(ResearchInputDerivationEvent)
        .where(ResearchInputDerivationEvent.derivation_id == derivation.id)
        .order_by(ResearchInputDerivationEvent.ordinal)
        .limit(4)
    ).all()
    result = []
    for row in rows:
        if (row.owner_id, row.workflow_id, row.stage_id) != (
            derivation.owner_id,
            derivation.workflow_id,
            derivation.stage_id,
        ):
            fail()
        outputs = parse_json(
            row.outputs_canonical.encode(),
            limits=JsonLimits(
                max_bytes=16384,
                max_depth=6,
                max_nodes=1000,
                max_items=256,
                max_string=256,
            ),
        )
        if (
            type(outputs) is not list
            or canonical_bytes(outputs).decode() != row.outputs_canonical
            or digest(outputs) != row.outputs_sha256
        ):
            fail()
        result.append(
            DerivationEvent(
                id=row.id,
                derivation_id=row.derivation_id,
                ordinal=row.ordinal,
                state=row.state,
                error_code=row.error_code,
                outputs=tuple(
                    InputVersionRef.model_validate_json(canonical_bytes(value))
                    for value in outputs
                ),
                recorded_at=row.created_at,
            )
        )
    if result:
        validate_event_prefix(result)
    return tuple(result)


def append_event(session, derivation, state, *, previous, outputs=(), error_code=None):
    event = DerivationEvent(
        id=uuid4(),
        derivation_id=derivation.id,
        ordinal=len(previous) + 1,
        state=state,
        error_code=error_code,
        outputs=tuple(outputs),
        recorded_at=utcnow(),
    )
    validate_event_prefix((*previous, event))
    projection = [value.model_dump(mode="json") for value in event.outputs]
    session.add(
        ResearchInputDerivationEvent(
            id=event.id,
            owner_id=derivation.owner_id,
            workflow_id=derivation.workflow_id,
            stage_id=derivation.stage_id,
            derivation_id=derivation.id,
            ordinal=event.ordinal,
            state=event.state,
            error_code=event.error_code,
            outputs_canonical=canonical_bytes(projection).decode(),
            outputs_sha256=digest(projection),
            created_at=event.recorded_at,
        )
    )
    session.flush()
    return event


def prepare_external(session, owner_id, workflow_id, stage_id, settings):
    """Raw intake already exists and is frozen in the actual stage definition."""
    _root, stage, definition = current_stage(session, owner_id, workflow_id, stage_id)
    row = session.scalar(
        select(ResearchInputDerivation).where(
            ResearchInputDerivation.stage_id == stage_id
        )
    )
    if row is not None:
        verify_derivation(row, owner_id, stage, definition)
        events = events_for(session, row)
        if not events:
            fail()
        return row, events[0]
    raw = session.get(ResearchInputObject, definition.raw_object_id)
    if raw is None or raw.kind != "raw_external" or raw.sha256 != definition.raw_sha256:
        fail()
    read_input_object(settings.storage_root, raw, owner_id)
    row = ResearchInputDerivation(
        id=uuid4(),
        owner_id=owner_id,
        workflow_id=workflow_id,
        stage_id=stage_id,
        operation="external_normalization",
        source_canonical=stage.definition_canonical,
        source_sha256=stage.definition_sha256,
        mapping_canonical=canonical_bytes(definition.mapping).decode(),
        mapping_sha256=definition.mapping.sha256,
        raw_object_id=raw.id,
        raw_object_sha256=raw.sha256,
        source_workflow_id=None,
        source_stage_id=None,
        source_child_run_id=None,
    )
    session.add(row)
    session.flush()
    return row, append_event(session, row, "prepared", previous=())


def verify_derivation(row, owner_id, stage, definition):
    if (
        row.owner_id,
        row.workflow_id,
        row.stage_id,
        row.operation,
        row.source_canonical,
        row.source_sha256,
        row.raw_object_id,
        row.raw_object_sha256,
        row.mapping_canonical,
        row.mapping_sha256,
        row.source_workflow_id,
        row.source_stage_id,
        row.source_child_run_id,
    ) != (
        owner_id,
        stage.workflow_id,
        stage.id,
        stage.kind,
        stage.definition_canonical,
        stage.definition_sha256,
        definition.raw_object_id,
        definition.raw_sha256,
        canonical_bytes(definition.mapping).decode(),
        definition.mapping.sha256,
        None,
        None,
        None,
    ):
        fail()


def start_external(session, owner_id, workflow_id, stage_id, derivation_id):
    root, stage, definition = current_stage(session, owner_id, workflow_id, stage_id)
    row = session.get(ResearchInputDerivation, derivation_id)
    if row is None:
        fail()
    verify_derivation(row, owner_id, stage, definition)
    previous = events_for(session, row)
    # A retry may read history, but must never obtain a second running claimant.
    if len(previous) != 1 or previous[0].state != "prepared":
        fail()
    if root.status == "cancel_requested":
        # No execution began. Record an honest terminal under this same root lock.
        return append_event(
            session, row, "cancelled", previous=previous, error_code="owner_cancelled"
        )
    # pause_after_stage applies after this already allocated immutable stage,
    # not before its truthful terminal/checkpoint can be recorded.
    return append_event(session, row, "started", previous=previous)


def finish_external(
    session,
    owner_id,
    workflow_id,
    stage_id,
    derivation_id,
    expected_started_event_id,
    settings,
    *,
    admission,
):
    """Actual bounded computation; caller commits receipt and output rows together."""
    _root, stage, definition = current_stage(session, owner_id, workflow_id, stage_id)
    row = session.get(ResearchInputDerivation, derivation_id)
    if row is None:
        fail()
    verify_derivation(row, owner_id, stage, definition)
    previous = events_for(session, row)
    if (
        len(previous) != 2
        or previous[-1].state != "started"
        or previous[-1].id != expected_started_event_id
    ):
        fail()
    raw = session.get(ResearchInputObject, definition.raw_object_id)
    if raw is None or raw.kind != "raw_external" or raw.sha256 != definition.raw_sha256:
        fail()
    content = read_input_object(
        settings.storage_root, raw, owner_id, deadline=float(admission)
    )
    try:
        result = normalize_external_json(content, definition.mapping)
    except NormalizationError as exc:
        return append_event(
            session, row, "rejected", previous=previous, error_code=exc.code
        )
    refs = []
    for output in result.outputs:
        obj = publish_input_object(
            session,
            owner_id,
            "normalized_external",
            canonical_bytes(output),
            settings,
            admission=admission,
        )
        record_id, version_id = uuid4(), uuid4()
        session.add(
            ResearchInputRecord(
                id=record_id,
                owner_id=owner_id,
                origin=output.origin,
                current_version_id=version_id,
                revision=1,
                disabled=False,
            )
        )
        session.add(
            ResearchInputVersion(
                id=version_id,
                owner_id=owner_id,
                record_id=record_id,
                ordinal=1,
                derivation_id=row.id,
                item_ordinal=output.item_ordinal,
                object_id=obj.id,
                content_canonical=canonical_bytes(output).decode(),
                content_sha256=output.sha256,
            )
        )
        refs.append(
            InputVersionRef(
                origin=output.origin,
                record_id=record_id,
                version_id=version_id,
                content_sha256=output.sha256,
            )
        )
    return append_event(session, row, "succeeded", previous=previous, outputs=refs)

"""Two-phase existing-release feedback input resolution, no exposure creation."""

from hashlib import sha256

from backend.app.research_input_derivations import events_for
from backend.app.research_input_feedback import verify_feedback_derivation
from backend.app.research_input_models import (
    ResearchInputDerivation,
    ResearchInputObject,
    ResearchInputRecord,
    ResearchInputVersion,
)
from backend.app.research_input_storage import (
    read_input_object,
)
from backend.app.research_workflow_feedback import (
    read_released_feedback_in_session,
    revalidate_released_feedback,
)
from backend.app.research_workflow_models import ResearchWorkflowStage
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_inputs.feedback import (
    FeedbackProjectionDefinition,
    project_direct_feedback,
)
from packages.research_inputs.resolved import ResolvedInputVersion


def fail():
    raise DomainError(
        "feedback_source_reader_unavailable",
        "Released feedback input is unavailable or changed",
        409,
    )


def _rows(session, owner_id, ref):
    record = session.get(ResearchInputRecord, ref.record_id)
    version = session.get(ResearchInputVersion, ref.version_id)
    if (
        record is None
        or version is None
        or (
            record.owner_id,
            record.origin,
            version.owner_id,
            version.record_id,
            version.content_sha256,
        )
        != (
            owner_id,
            "experiment_feedback_proposal",
            owner_id,
            ref.record_id,
            ref.content_sha256,
        )
    ):
        fail()
    row = session.get(ResearchInputDerivation, version.derivation_id)
    if (
        row is None
        or row.owner_id != owner_id
        or row.operation != "experiment_feedback_projection"
    ):
        fail()
    return record, version, row


def resolve_feedback_version(session, owner_id, ref, settings, *, historical):
    record, version, row = _rows(session, owner_id, ref)
    if not historical and (
        record.disabled or record.current_version_id != ref.version_id
    ):
        fail()
    stage = session.get(ResearchWorkflowStage, row.stage_id)
    if stage is None or (
        stage.owner_id,
        stage.workflow_id,
        stage.kind,
        stage.child_run_id,
    ) != (owner_id, row.workflow_id, "experiment_feedback_projection", None):
        fail()
    definition = FeedbackProjectionDefinition.model_validate_json(
        stage.definition_canonical
    )
    if (
        canonical_bytes(definition).decode() != stage.definition_canonical
        or digest(definition) != stage.definition_sha256
    ):
        fail()
    verify_feedback_derivation(row, owner_id, stage, definition)
    events = events_for(session, row)
    if (
        len(events) != 3
        or events[-1].state != "succeeded"
        or events[-1].outputs != (ref,)
        or version.item_ordinal != 0
    ):
        fail()
    # Requires actual native guard; ledger checked before reading source values.
    read = read_released_feedback_in_session(
        session, owner_id, row.workflow_id, row.stage_id, settings
    )
    verified, exposures = revalidate_released_feedback(
        session, owner_id, row.workflow_id, row.stage_id, settings, read
    )
    value = project_direct_feedback(definition, verified.measurement, exposures)
    obj = session.get(ResearchInputObject, version.object_id)
    if (
        obj is None
        or obj.kind != "normalized_feedback"
        or obj.sha256 != ref.content_sha256
    ):
        fail()
    raw = read_input_object(settings.storage_root, obj, owner_id)
    if (
        raw != canonical_bytes(value)
        or raw.decode() != version.content_canonical
        or value.sha256 != version.content_sha256
    ):
        fail()
    return ResolvedInputVersion(
        ref=ref,
        kind=value.kind,
        title=value.title,
        text=value.text,
        text_sha256=sha256(value.text.encode()).hexdigest(),
    )

"""Actual deterministic feedback lifecycle over a freshly released owned result.

Metadata preparation/cancellation never discloses values. Disclosure occurs via
the fixed workflow reader outside held owner/native locks; publication rechecks
its exact stage, release ledger and private result buffer before deriving text.
"""

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select

from backend.app.core.maintenance import shared_lifecycle
from backend.app.factor_services import publication
from backend.app.research_input_derivations import append_event, events_for, fail
from backend.app.research_input_models import (
    ResearchInputDerivation,
    ResearchInputRecord,
    ResearchInputVersion,
)
from backend.app.research_input_storage import (
    publish_input_object,
    require_input_capability,
)
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_workflow_feedback import (
    _binding,
    prepare_feedback_read,
    revalidate_feedback_read,
)
from backend.app.research_workflow_models import ResearchWorkflow
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes
from packages.research_inputs.contracts import InputVersionRef
from packages.research_inputs.feedback import project_direct_feedback

PUBLICATION_PEAK_BYTES = 65536
PUBLICATION_PEAK_FILES = 2


@dataclass(frozen=True)
class FeedbackTerminalEvidence:
    derivation_id: UUID
    terminal_event_id: UUID
    terminal_event_sha256: str
    state: str
    error_code: str | None
    outputs: tuple[InputVersionRef, ...]
    objects: tuple
    exposure_ids: tuple[UUID, ...]


def current_feedback_stage(session, owner_id, workflow_id, stage_id):
    require_input_capability(session)
    _try_neutral_lock(session, f"submit:{owner_id}")
    root = session.scalar(
        select(ResearchWorkflow)
        .filter_by(owner_id=owner_id, id=workflow_id)
        .with_for_update(nowait=True)
    )
    if (
        root is None
        or root.current_stage_id != stage_id
        or root.status
        not in {"ready", "running", "pause_requested", "cancel_requested"}
    ):
        fail()
    # Exact immutable adjacent engine-stage/checkpoint metadata only; no values
    # or exposure creation in this preparation/cancellation path.
    _, stage, _, definition, _, _ = _binding(session, owner_id, workflow_id, stage_id)
    return root, stage, definition


def verify_feedback_derivation(row, owner_id, stage, definition):
    if row is None or (
        row.owner_id,
        row.workflow_id,
        row.stage_id,
        row.operation,
        row.source_canonical,
        row.source_sha256,
        row.mapping_canonical,
        row.mapping_sha256,
        row.raw_object_id,
        row.raw_object_sha256,
        row.source_workflow_id,
        row.source_stage_id,
        row.source_child_run_id,
    ) != (
        owner_id,
        stage.workflow_id,
        stage.id,
        "experiment_feedback_projection",
        stage.definition_canonical,
        stage.definition_sha256,
        None,
        None,
        None,
        None,
        definition.source.workflow_id,
        definition.source.stage_id,
        definition.source.child_run_id,
    ):
        fail()


def prepare_feedback(session, owner_id, workflow_id, stage_id):
    _, stage, definition = current_feedback_stage(
        session, owner_id, workflow_id, stage_id
    )
    row = session.scalar(select(ResearchInputDerivation).filter_by(stage_id=stage_id))
    if row is not None:
        verify_feedback_derivation(row, owner_id, stage, definition)
        if not events_for(session, row):
            fail()
        return row
    row = ResearchInputDerivation(
        id=uuid4(),
        owner_id=owner_id,
        workflow_id=workflow_id,
        stage_id=stage_id,
        operation=stage.kind,
        source_canonical=stage.definition_canonical,
        source_sha256=stage.definition_sha256,
        mapping_canonical=None,
        mapping_sha256=None,
        raw_object_id=None,
        raw_object_sha256=None,
        source_workflow_id=definition.source.workflow_id,
        source_stage_id=definition.source.stage_id,
        source_child_run_id=definition.source.child_run_id,
    )
    session.add(row)
    session.flush()
    append_event(session, row, "prepared", previous=())
    return row


def start_feedback(session, owner_id, workflow_id, stage_id, derivation_id):
    root, stage, definition = current_feedback_stage(
        session, owner_id, workflow_id, stage_id
    )
    row = session.get(ResearchInputDerivation, derivation_id)
    verify_feedback_derivation(row, owner_id, stage, definition)
    previous = events_for(session, row)
    if len(previous) != 1 or previous[0].state != "prepared":
        fail()
    if root.status == "cancel_requested":
        return append_event(
            session, row, "cancelled", previous=previous, error_code="owner_cancelled"
        )
    return append_event(session, row, "started", previous=previous)


def finish_feedback(
    session,
    owner_id,
    workflow_id,
    stage_id,
    derivation_id,
    started_event_id,
    settings,
    read,
    *,
    admission,
):
    _, stage, definition = current_feedback_stage(
        session, owner_id, workflow_id, stage_id
    )
    row = session.get(ResearchInputDerivation, derivation_id)
    verify_feedback_derivation(row, owner_id, stage, definition)
    previous = events_for(session, row)
    if (
        len(previous) != 2
        or previous[-1].state != "started"
        or previous[-1].id != started_event_id
    ):
        fail()
    verified, exposures = revalidate_feedback_read(
        session, owner_id, workflow_id, stage_id, settings, read, admission=admission
    )
    output = project_direct_feedback(definition, verified.measurement, exposures)
    raw = canonical_bytes(output)
    obj = publish_input_object(
        session, owner_id, "normalized_feedback", raw, settings, admission=admission
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
            item_ordinal=0,
            object_id=obj.id,
            content_canonical=raw.decode(),
            content_sha256=output.sha256,
        )
    )
    ref = InputVersionRef(
        origin=output.origin,
        record_id=record_id,
        version_id=version_id,
        content_sha256=output.sha256,
    )
    return append_event(session, row, "succeeded", previous=previous, outputs=(ref,))


def run_feedback_stage(factory, settings, *, owner_id, workflow_id, stage_id):
    # Phase1 records only truthful metadata. A missing release leaves prepared,
    # never started/succeeded; root dispatcher can display waiting_release.
    with shared_lifecycle(factory.kw["bind"], settings.storage_root):
        with factory() as session:
            row = prepare_feedback(session, owner_id, workflow_id, stage_id)
            identity = row.id
            prior = events_for(session, row)
            if prior[-1].state not in {"prepared", "started"}:
                result = prior[-1]
                session.rollback()
                return result
            if len(prior) != 1:
                raise DomainError(
                    "input_stage_already_started",
                    "Input stage requires history or recovery",
                    409,
                )
            root, _, _ = current_feedback_stage(
                session, owner_id, workflow_id, stage_id
            )
            if root.status == "cancel_requested":
                terminal = start_feedback(
                    session, owner_id, workflow_id, stage_id, identity
                )
                session.commit()
                return terminal
            session.commit()
        # No SQL transaction, owner lock, or native factor lock spans disclose.
        read = prepare_feedback_read(
            factory,
            settings,
            owner_id=owner_id,
            workflow_id=workflow_id,
            stage_id=stage_id,
        )
        with factory() as session:
            started = start_feedback(session, owner_id, workflow_id, stage_id, identity)
            session.commit()
            if started.state == "cancelled":
                return started
            with publication(
                session,
                owner_id,
                settings,
                peak_bytes=PUBLICATION_PEAK_BYTES,
                peak_files=PUBLICATION_PEAK_FILES,
            ) as admission:
                terminal = finish_feedback(
                    session,
                    owner_id,
                    workflow_id,
                    stage_id,
                    identity,
                    started.id,
                    settings,
                    read,
                    admission=admission,
                )
            return terminal


def validate_feedback_terminal(
    session, owner_id, workflow_id, stage_id, settings, *, prepared_read, admission
):
    """Publication-guarded exact terminal facts; no new exposure creation."""
    from backend.app.research_input_storage import require_publication_admission

    root = require_publication_admission(session, settings, admission)

    def resolve():
        return revalidate_feedback_read(
            session,
            owner_id,
            workflow_id,
            stage_id,
            settings,
            prepared_read,
            admission=admission,
        )

    result = _feedback_terminal_facts(
        session,
        owner_id,
        workflow_id,
        stage_id,
        settings,
        root,
        float(admission),
        resolve,
    )
    require_publication_admission(session, settings, admission)
    return result


def validate_feedback_terminal_under_native_guard(
    session, owner_id, workflow_id, stage_id, settings
):
    """Historical terminal under an actual native guard, never fake publication.

    Only the fixed historical reader may supply released source values. A true
    prepared-only cancellation has no values or exposure creation to perform.
    """
    import time
    from pathlib import Path

    from backend.app.factor_native_io import require_native_storage_guard
    from backend.app.research_workflow_feedback import (
        read_released_feedback_in_session,
        revalidate_released_feedback,
    )

    require_native_storage_guard(settings.storage_root)

    def resolve():
        read = read_released_feedback_in_session(
            session, owner_id, workflow_id, stage_id, settings
        )
        return revalidate_released_feedback(
            session, owner_id, workflow_id, stage_id, settings, read
        )

    result = _feedback_terminal_facts(
        session,
        owner_id,
        workflow_id,
        stage_id,
        settings,
        Path(settings.storage_root).absolute(),
        time.monotonic() + 10,
        resolve,
    )
    require_native_storage_guard(settings.storage_root)
    return result


def _feedback_terminal_facts(
    session, owner_id, workflow_id, stage_id, settings, root, deadline, resolve_released
):
    from backend.app.research_input_models import ResearchInputObject
    from backend.app.research_input_terminal import (
        VerifiedInputObject,
        _inventory,
    )
    from packages.factors.contracts import digest

    _, stage, _, definition, _, _ = _binding(session, owner_id, workflow_id, stage_id)
    row = session.scalar(
        select(ResearchInputDerivation).filter_by(
            owner_id=owner_id, workflow_id=workflow_id, stage_id=stage_id
        )
    )
    verify_feedback_derivation(row, owner_id, stage, definition)
    events = events_for(session, row)
    if not events or events[-1].state in {"prepared", "started"}:
        fail()
    terminal = events[-1]
    versions = session.scalars(
        select(ResearchInputVersion).filter_by(derivation_id=row.id).limit(2)
    ).all()
    objects = ()
    exposures = ()
    if terminal.state == "succeeded":
        if len(versions) != 1 or len(terminal.outputs) != 1:
            fail()
        version = versions[0]
        record = session.get(ResearchInputRecord, version.record_id)
        obj = session.get(ResearchInputObject, version.object_id)
        ref = terminal.outputs[0]
        verified, exposures = resolve_released()
        output = project_direct_feedback(definition, verified.measurement, exposures)
        inventory = _inventory(session, owner_id, root, settings, deadline)
        if (
            record is None
            or obj is None
            or inventory.get(obj.id) is not obj
            or (
                version.owner_id,
                version.item_ordinal,
                version.ordinal,
                version.content_canonical,
                version.content_sha256,
            )
            != (owner_id, 0, 1, canonical_bytes(output).decode(), output.sha256)
            or (
                record.owner_id,
                record.origin,
                ref.record_id,
                ref.version_id,
                ref.content_sha256,
            )
            != (owner_id, output.origin, record.id, version.id, output.sha256)
            or (obj.kind, obj.sha256) != ("normalized_feedback", output.sha256)
        ):
            fail()
        objects = (VerifiedInputObject(obj.id, obj.kind, obj.sha256, obj.size_bytes),)
    elif versions or terminal.outputs:
        fail()
    return FeedbackTerminalEvidence(
        row.id,
        terminal.id,
        digest(terminal),
        terminal.state,
        terminal.error_code,
        terminal.outputs,
        objects,
        tuple(exposures),
    )

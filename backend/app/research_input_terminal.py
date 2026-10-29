"""Fresh local publication facts for the workflow's own checkpoint decision.

No process stop, spending settlement, or launch authority is manufactured here.
The caller holds the existing quota/lifecycle admission and its workflow CAS.
"""

import os
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from backend.app.core.atomic_storage import _open_directory
from backend.app.factor_services import check_deadline
from backend.app.research_input_derivations import events_for, verify_derivation
from backend.app.research_input_models import (
    ResearchInputDerivation,
    ResearchInputObject,
    ResearchInputRecord,
    ResearchInputVersion,
)
from backend.app.research_input_storage import (
    read_input_object,
    require_input_capability,
    require_publication_admission,
)
from backend.app.research_workflow_models import ResearchWorkflowStage
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_inputs.contracts import InputVersionRef
from packages.research_inputs.derivation import ExternalNormalizationDefinition
from packages.research_inputs.errors import public_error_code
from packages.research_inputs.normalization import (
    NormalizationError,
    normalize_external_json,
)


@dataclass(frozen=True)
class VerifiedInputObject:
    id: UUID
    kind: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ExternalTerminalEvidence:
    derivation_id: UUID
    terminal_event_id: UUID
    terminal_event_sha256: str
    state: str
    error_code: str | None
    outputs: tuple[InputVersionRef, ...]
    objects: tuple[VerifiedInputObject, ...]


def unavailable():
    raise DomainError(
        "input_publication_inconsistent",
        "Input publication requires verified recovery",
        409,
    )


def _inventory(session, owner_id, root, settings, admission):
    maximum = getattr(settings, "factor_max_files", 10000)
    rows = session.scalars(
        select(ResearchInputObject)
        .where(ResearchInputObject.owner_id == owner_id)
        .limit(maximum + 1)
    ).all()
    if len(rows) > maximum or any(row.owner_id != owner_id for row in rows):
        unavailable()
    directory = _open_directory(root / f"factors/{owner_id}/input-objects")
    try:
        before = os.fstat(directory)
        names = set()
        with os.scandir(directory) as entries:
            for entry in entries:
                check_deadline(admission)
                if len(names) >= maximum:
                    unavailable()
                names.add(entry.name)
        expected = set()
        for row in rows:
            extension = {
                "raw_external": "raw",
                "normalized_external": "json",
                "normalized_feedback": "feedback.json",
            }.get(row.kind)
            name = f"{row.sha256}.{extension}"
            if (
                row.kind
                not in {"raw_external", "normalized_external", "normalized_feedback"}
                or row.storage_key != f"factors/{owner_id}/input-objects/{name}"
                or name in expected
            ):
                unavailable()
            expected.add(name)
            # Re-read all registered owned files: no DB-only orphan adoption or
            # optimistic trust in a prior inventory scan's detached metadata.
            read_input_object(root, row, owner_id, deadline=float(admission))
        after = os.fstat(directory)
        if names != expected or (
            before.st_dev,
            before.st_ino,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns):
            unavailable()
    finally:
        os.close(directory)
    return {row.id: row for row in rows}


def _validate_external_terminal(
    session, owner_id, workflow_id, stage_id, settings, *, admission
):
    require_input_capability(session)
    root = require_publication_admission(session, settings, admission)
    stage = session.get(ResearchWorkflowStage, stage_id)
    if stage is None or (
        stage.owner_id,
        stage.workflow_id,
        stage.kind,
        stage.child_run_id,
    ) != (owner_id, workflow_id, "external_normalization", None):
        unavailable()
    definition = ExternalNormalizationDefinition.model_validate_json(
        stage.definition_canonical
    )
    if (
        canonical_bytes(definition).decode() != stage.definition_canonical
        or digest(definition) != stage.definition_sha256
    ):
        unavailable()
    row = session.scalar(
        select(ResearchInputDerivation).where(
            ResearchInputDerivation.owner_id == owner_id,
            ResearchInputDerivation.workflow_id == workflow_id,
            ResearchInputDerivation.stage_id == stage_id,
        )
    )
    if row is None:
        unavailable()
    verify_derivation(row, owner_id, stage, definition)
    events = events_for(session, row)
    if not events or events[-1].state in {"prepared", "started"}:
        unavailable()
    terminal = events[-1]
    objects = _inventory(session, owner_id, root, settings, admission)
    raw = objects.get(definition.raw_object_id)
    if raw is None or raw.kind != "raw_external" or raw.sha256 != definition.raw_sha256:
        unavailable()
    content = read_input_object(root, raw, owner_id, deadline=float(admission))
    versions = session.scalars(
        select(ResearchInputVersion)
        .where(ResearchInputVersion.derivation_id == row.id)
        .order_by(ResearchInputVersion.item_ordinal)
        .limit(33)
    ).all()
    selected_objects = [raw]
    if terminal.state == "succeeded":
        actual = normalize_external_json(content, definition.mapping)
        if len(versions) != len(actual.outputs) or len(terminal.outputs) != len(
            actual.outputs
        ):
            unavailable()
        for ordinal, (version, output, ref) in enumerate(
            zip(versions, actual.outputs, terminal.outputs, strict=True)
        ):
            record = session.get(ResearchInputRecord, version.record_id)
            if (
                version.owner_id,
                version.derivation_id,
                version.item_ordinal,
                version.ordinal,
                version.content_sha256,
                version.content_canonical,
            ) != (
                owner_id,
                row.id,
                ordinal,
                1,
                output.sha256,
                canonical_bytes(output).decode(),
            ):
                unavailable()
            if record is None or (record.owner_id, record.id, record.origin) != (
                owner_id,
                version.record_id,
                output.origin,
            ):
                unavailable()
            if ref != InputVersionRef(
                origin=output.origin,
                record_id=record.id,
                version_id=version.id,
                content_sha256=output.sha256,
            ):
                unavailable()
            obj = objects.get(version.object_id)
            if (
                obj is None
                or obj.kind != "normalized_external"
                or obj.sha256 != output.sha256
                or read_input_object(root, obj, owner_id, deadline=float(admission))
                != canonical_bytes(output)
            ):
                unavailable()
            selected_objects.append(obj)
    else:
        if versions or terminal.outputs:
            unavailable()
        if terminal.state == "rejected":
            try:
                normalize_external_json(content, definition.mapping)
            except NormalizationError as exc:
                if exc.code != terminal.error_code:
                    unavailable()
            else:
                unavailable()
    if len({obj.id for obj in selected_objects}) != len(selected_objects):
        unavailable()
    check_deadline(admission)
    return ExternalTerminalEvidence(
        derivation_id=row.id,
        terminal_event_id=terminal.id,
        terminal_event_sha256=digest(terminal),
        state=terminal.state,
        error_code=public_error_code(terminal.error_code),
        outputs=terminal.outputs,
        objects=tuple(
            VerifiedInputObject(
                id=obj.id, kind=obj.kind, sha256=obj.sha256, size_bytes=obj.size_bytes
            )
            for obj in selected_objects
        ),
    )


def validate_external_terminal(
    session, owner_id, workflow_id, stage_id, settings, *, admission
):
    try:
        return _validate_external_terminal(
            session, owner_id, workflow_id, stage_id, settings, admission=admission
        )
    except DomainError:
        raise
    except (ValueError, TypeError, AttributeError, OSError):
        unavailable()

"""Fresh owned input resolution, with distinct new-selection/history semantics.

No caller-provided ResolvedInputVersion is accepted as authority. Feedback uses
the fixed existing-release reader under a real native guard; cached feedback
strings never substitute for fresh source-stage and ledger verification.
"""

from hashlib import sha256

from sqlalchemy import select

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
)
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_workflow_models import ResearchWorkflowStage
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_inputs.contracts import (
    InputVersionRef,
    NormalizedExternalProposal,
)
from packages.research_inputs.derivation import ExternalNormalizationDefinition
from packages.research_inputs.normalization import normalize_external_json
from packages.research_inputs.resolved import ResolvedInputVersion


def unavailable(code="input_version_unavailable"):
    raise DomainError(code, "Input version is not available for this operation", 409)


def _manual(ref, value):
    if (
        value.steer_id != ref.record_id
        or value.version_id != ref.version_id
        or value.sha256 != ref.content_sha256
    ):
        unavailable()
    return ResolvedInputVersion(
        ref=ref,
        kind=value.kind,
        title="Manual " + value.kind,
        text=value.text,
        text_sha256=value.text_sha256,
    )


def _manual_versions(session, owner_id, refs, *, historical):
    from backend.app.rdagent_steer_models import RDSteerVersion
    from backend.app.rdagent_steer_services import (
        decode_version,
        require_schema,
        resolve_steer_snapshot,
    )
    from packages.rdagent_adapter.steer import SteerSnapshot

    require_schema(session)
    ids = tuple(ref.version_id for ref in refs)
    if historical:
        snapshot = SteerSnapshot(
            owner_id=owner_id,
            versions=tuple(
                decode_version(session.get(RDSteerVersion, identity), owner_id)
                for identity in ids
            ),
        )
    else:
        snapshot = resolve_steer_snapshot(session, owner_id, ids)
    return {value.version_id: value for value in snapshot.versions}


def _external(session, owner_id, ref, settings, *, historical):
    record = session.scalar(
        select(ResearchInputRecord).where(
            ResearchInputRecord.owner_id == owner_id,
            ResearchInputRecord.id == ref.record_id,
        )
    )
    version = session.scalar(
        select(ResearchInputVersion).where(
            ResearchInputVersion.owner_id == owner_id,
            ResearchInputVersion.id == ref.version_id,
            ResearchInputVersion.record_id == ref.record_id,
        )
    )
    if (
        record is None
        or version is None
        or record.origin != ref.origin
        or version.content_sha256 != ref.content_sha256
    ):
        unavailable()
    if not historical and (
        record.disabled or record.current_version_id != ref.version_id
    ):
        unavailable("input_selection_changed")
    row = session.get(ResearchInputDerivation, version.derivation_id)
    if (
        row is None
        or row.owner_id != owner_id
        or row.operation != "external_normalization"
    ):
        unavailable()
    stage = session.get(ResearchWorkflowStage, row.stage_id)
    if (
        stage is None
        or stage.owner_id != owner_id
        or stage.kind != "external_normalization"
        or stage.child_run_id is not None
    ):
        unavailable()
    definition = ExternalNormalizationDefinition.model_validate_json(
        stage.definition_canonical
    )
    if (
        canonical_bytes(definition).decode() != stage.definition_canonical
        or digest(definition) != stage.definition_sha256
    ):
        unavailable()
    verify_derivation(row, owner_id, stage, definition)
    history = events_for(session, row)
    if (
        len(history) != 3
        or history[-1].state != "succeeded"
        or ref not in history[-1].outputs
    ):
        unavailable()
    obj = session.get(ResearchInputObject, version.object_id)
    raw = session.get(ResearchInputObject, definition.raw_object_id)
    if (
        obj is None
        or obj.kind != "normalized_external"
        or obj.sha256 != version.content_sha256
        or raw is None
        or raw.kind != "raw_external"
        or raw.sha256 != definition.raw_sha256
    ):
        unavailable()
    payload = read_input_object(settings.storage_root, obj, owner_id)
    if payload.decode() != version.content_canonical:
        unavailable()
    value = NormalizedExternalProposal.model_validate_json(payload)
    actual = normalize_external_json(
        read_input_object(settings.storage_root, raw, owner_id), definition.mapping
    )
    if (
        not 0 <= version.item_ordinal < len(actual.outputs)
        or actual.outputs[version.item_ordinal] != value
    ):
        unavailable()
    if (
        len(history[-1].outputs) != len(actual.outputs)
        or history[-1].outputs[version.item_ordinal] != ref
    ):
        unavailable()
    return ResolvedInputVersion(
        ref=ref,
        kind=value.kind,
        title=value.title,
        text=value.text,
        text_sha256=sha256(value.text.encode()).hexdigest(),
    )


def _resolve(session, owner_id, refs, settings, *, historical):
    require_input_capability(session)
    if not historical:
        _try_neutral_lock(session, f"submit:{owner_id}")
    refs = tuple(InputVersionRef.model_validate(value) for value in refs)
    if (
        len(refs) > 32
        or sum(r.origin == "manual_user_submission" for r in refs) > 8
        or len({(r.origin, r.record_id) for r in refs}) != len(refs)
    ):
        unavailable("invalid_input_selection")
    manual_refs = tuple(ref for ref in refs if ref.origin == "manual_user_submission")
    manual = (
        _manual_versions(session, owner_id, manual_refs, historical=historical)
        if manual_refs
        else {}
    )
    resolved = []
    for ref in refs:
        if ref.origin == "manual_user_submission":
            resolved.append(_manual(ref, manual[ref.version_id]))
        elif ref.origin == "normalized_external_proposal":
            resolved.append(
                _external(session, owner_id, ref, settings, historical=historical)
            )
        elif ref.origin == "experiment_feedback_proposal":
            from backend.app.research_input_feedback_resolution import (
                resolve_feedback_version,
            )

            resolved.append(
                resolve_feedback_version(
                    session,
                    owner_id,
                    ref,
                    settings,
                    historical=historical,
                )
            )
        else:
            unavailable("unknown_input_origin")
    if (
        len(canonical_bytes([item.model_dump(mode="json") for item in resolved]))
        > 32768
    ):
        unavailable("resolved_input_limit")
    return tuple(resolved)


def resolve_model_input_versions(session, owner_id, refs, settings):
    """NEW acceptance only: manual/source selections must be active/current."""
    return _resolve(
        session,
        owner_id,
        refs,
        settings,
        historical=False,
    )


def verify_model_input_versions(session, owner_id, refs, settings):
    """Historical exact version verification; later head/disable is not consulted.

    Feedback rechecks its actual retained source and existing release ledger.
    """
    return _resolve(
        session,
        owner_id,
        refs,
        settings,
        historical=True,
    )

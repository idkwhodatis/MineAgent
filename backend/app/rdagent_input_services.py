"""Flush-only persisted candidate input closure; no v2 launch is admitted here.

Normalizers deliberately have no corresponding writer until a real candidate
stopped/extraction/HDF-publication witness exists. Owner/hash alone is insufficient.
"""

import hashlib
from dataclasses import asdict
from uuid import uuid4

from sqlalchemy import select

from backend.app.factor_models import FactorObject, FactorVersion
from backend.app.rdagent_controller_services import require_rd_controller
from backend.app.rdagent_input_models import (
    FactorCandidateInputFile,
    FactorCandidateInputInventory,
)
from backend.app.rdagent_models import (
    FactorCutoffExecution,
    FactorEvaluationStart,
    FactorGenerationOutcome,
)
from backend.app.rdagent_services import _scope_row, require_research_spec
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.research_object_models import (
    ResearchObjectOutcome,
    ResearchObjectPublication,
)
from backend.app.research_object_services import read_research_object
from backend.app.research_process_models import ResearchProcessAdmission
from backend.app.services import DomainError
from backend.app.storage import read_verified
from packages.factors.contracts import SnapshotInput
from packages.rdagent_adapter.numeric_profile import candidate_inputs
from packages.rdagent_adapter.prefix import PrefixManifest
from packages.research_runtime.process_contracts import ProcessError, canonical, digest
from packages.research_runtime.process_declarations_v2 import (
    NoIPC,
    ProcessDeclarationV2,
)
from packages.research_runtime.process_manifests import InputBinding
from packages.research_runtime.process_persistence_codec import ROW_FIELDS, decode_row

KINDS = {
    "/input/manifest.json": "factor_prefix_manifest",
    "/input/listings.json": "factor_prefix_listings",
    "/input/observations.json": "factor_prefix_observations",
}


def _error():
    return DomainError(
        "factor_candidate_input_drift",
        "Persisted mounted inputs do not close exact cutoff identity",
        409,
    )


def _owned(session, model, owner_id, object_id):
    row = session.scalar(
        select(model).where(model.owner_id == owner_id, model.id == object_id)
    )
    if row is None:
        raise _error()
    return row


def _persist_candidate_inputs(
    session, fence, authorization, *, execution_id, publications, settings
):
    require_neutral_transaction(session, fence, resource_scope="processes")
    require_rd_controller(session, fence, authorization)
    if type(publications) is not dict or set(publications) != set(KINDS):
        raise _error()
    execution = _scope_row(
        session, FactorCutoffExecution, execution_id, fence, lock=True
    )
    admission = _scope_row(
        session, ResearchProcessAdmission, execution.candidate_admission_id, fence
    )
    try:
        declaration = decode_row(
            {field: getattr(admission, field) for field in ROW_FIELDS}
        )
    except (AttributeError, ValueError, ProcessError) as error:
        raise _error() from error
    # This decodes persisted evidence only. Existing v2 persistence gate is closed,
    # so a real row cannot be manufactured by this service or a caller DTO.
    if type(declaration) is not ProcessDeclarationV2 or not isinstance(
        declaration.ipc, NoIPC
    ):
        raise _error()
    _, config = require_research_spec(session, fence)
    if (
        declaration.role,
        declaration.profile,
        declaration.image_id,
        declaration.logical_operation_id,
    ) != (
        "factor_cutoff_candidate",
        config.runtime.candidate_profile,
        config.runtime.candidate_image,
        execution.id,
    ):
        raise _error()
    ticket = _scope_row(session, FactorEvaluationStart, execution.evaluation_id, fence)
    snapshot = SnapshotInput.model_validate(ticket.snapshot_input)
    if (ticket.start_id, ticket.factor_version_id, ticket.input_sha256) != (
        execution.start_id,
        execution.factor_version_id,
        snapshot.sha256,
    ):
        raise _error()
    prefix = PrefixManifest.model_validate(execution.projection)
    if (
        prefix.candidate_image,
        prefix.candidate_profile,
        prefix.normalizer_image,
        prefix.normalizer_profile,
    ) != (
        config.runtime.candidate_image,
        config.runtime.candidate_profile,
        config.runtime.normalizer_image,
        config.runtime.normalizer_profile,
    ):
        raise _error()
    if prefix.sha256 != execution.projection_sha256:
        raise _error()
    version = _owned(
        session, FactorVersion, fence.owner_id, execution.factor_version_id
    )
    source = _owned(session, FactorObject, fence.owner_id, version.code_object_id)
    if source.object_kind != "code" or source.sha256 != version.code_sha256:
        raise _error()
    content = read_verified(
        settings.storage_root, source.storage_key, source.sha256, 131072
    )
    if len(content) != source.size_bytes or content != version.code.encode("utf-8"):
        raise _error()
    evidence = {
        "/input/source.py": {
            "object_kind": "code",
            "sha256": source.sha256,
            "size_bytes": len(content),
            "factor_object_id": source.id,
            "research_object_id": None,
            "publication_id": None,
        }
    }
    prefix_inputs = {item.name: item for item in prefix.input_files}
    projected = {}
    for destination, kind in KINDS.items():
        publication = _scope_row(
            session, ResearchObjectPublication, publications[destination], fence
        )
        outcome = session.get(ResearchObjectOutcome, publication.id)
        if outcome is None or (
            publication.object_kind,
            outcome.owner_id,
            outcome.object_kind,
            outcome.sha256,
        ) != (kind, fence.owner_id, kind, publication.sha256):
            raise _error()
        raw = read_research_object(
            session, fence, outcome.object_id, kind, publication.sha256, settings
        )
        if (
            len(raw) != publication.size_bytes
            or hashlib.sha256(raw).hexdigest() != publication.sha256
        ):
            raise _error()
        if destination == "/input/manifest.json":
            if raw != canonical(prefix.model_dump(mode="json")):
                raise _error()
        else:
            expected = prefix_inputs[destination.removeprefix("/input/")]
            if (len(raw), publication.sha256) != (expected.size_bytes, expected.sha256):
                raise _error()
        if destination != "/input/manifest.json":
            projected[destination.removeprefix("/input/")] = raw
        evidence[destination] = {
            "object_kind": kind,
            "sha256": publication.sha256,
            "size_bytes": len(raw),
            "factor_object_id": None,
            "research_object_id": outcome.object_id,
            "publication_id": publication.id,
        }
    candidate_inputs(
        prefix,
        snapshot_input=snapshot,
        source=content,
        expected_source_sha256=version.code_sha256,
        projected_files=projected,
    )
    bindings = tuple(
        InputBinding(
            destination=destination,
            sha256=item["sha256"],
            size_bytes=item["size_bytes"],
        )
        for destination, item in sorted(evidence.items())
    )
    commitment = digest([asdict(item) for item in bindings])
    if commitment != declaration.input_sha256:
        raise _error()
    existing = session.scalar(
        select(FactorCandidateInputInventory).where(
            FactorCandidateInputInventory.execution_id == execution.id
        )
    )
    if existing is not None:
        rows = session.scalars(
            select(FactorCandidateInputFile)
            .where(FactorCandidateInputFile.inventory_id == existing.id)
            .order_by(FactorCandidateInputFile.ordinal)
        ).all()
        scope = {
            key: getattr(fence, key)
            for key in ("owner_id", "run_id", "attempt_id", "generation")
        }
        if (
            any(getattr(existing, key) != value for key, value in scope.items())
            or existing.execution_id != execution.id
            or [row.ordinal for row in rows] != list(range(4))
            or any(type(row.ordinal) is not int for row in rows)
            or [row.destination for row in rows] != sorted(evidence)
            or any(
                row.inventory_id != existing.id
                or any(getattr(row, key) != value for key, value in scope.items())
                for row in rows
            )
        ):
            raise _error()
        actual = {
            row.destination: {
                key: getattr(row, key) for key in next(iter(evidence.values()))
            }
            for row in rows
        }
        if (
            existing.admission_id != admission.id
            or existing.input_sha256 != commitment
            or existing.projection_sha256 != prefix.sha256
            or existing.file_count != 4
            or len(rows) != 4
            or actual != evidence
        ):
            raise _error()
        return existing
    terminal = session.scalar(
        select(FactorGenerationOutcome).where(
            FactorGenerationOutcome.start_id == execution.start_id
        )
    )
    if terminal is not None:
        raise _error()
    from backend.app.rdagent_boundary_services import require_unobserved_generation

    require_unobserved_generation(session, fence, execution.start_id)
    scope = {
        key: getattr(fence, key)
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    }
    inventory = FactorCandidateInputInventory(
        id=uuid4(),
        **scope,
        execution_id=execution.id,
        admission_id=admission.id,
        input_sha256=commitment,
        projection_sha256=prefix.sha256,
        file_count=4,
    )
    session.add(inventory)
    session.flush()
    for ordinal, (destination, item) in enumerate(sorted(evidence.items())):
        session.add(
            FactorCandidateInputFile(
                id=uuid4(),
                **scope,
                inventory_id=inventory.id,
                destination=destination,
                ordinal=ordinal,
                **item,
            )
        )
    session.flush()
    return inventory


def persist_candidate_inputs(
    session, fence, authorization, *, execution_id, publications, settings
):
    try:
        return _persist_candidate_inputs(
            session,
            fence,
            authorization,
            execution_id=execution_id,
            publications=publications,
            settings=settings,
        )
    except (ValueError, KeyError) as error:
        raise _error() from error

"""Fresh retained parent-code reader for026; no registration or launch authority.

No transaction ownership, DB mutation or source execution. Callers retain the
usual installation maintenance admission and owner/fence locks before later
persistence. New seeded submission/plan/first-start SQL remains uninstalled.
"""

from sqlalchemy import select

from backend.app.factor_models import Factor, FactorObject, FactorVersion
from backend.app.rdagent_source_publication import source_storage_guard
from backend.app.services import DomainError
from backend.app.storage import read_verified
from packages.factors.contracts import FactorDefinition, canonical_bytes, digest
from packages.rdagent_adapter.seed_parent import ResolvedSeedParent, SeedParentReference


def resolve_seed_parent(session, owner_id, parent_version_id, settings):
    """Reread owner+factor+version+object and exact bytes on every invocation."""
    version = session.scalar(
        select(FactorVersion).filter_by(owner_id=owner_id, id=parent_version_id)
    )
    if version is None:
        raise DomainError("not_found", "Owned parent factor version not found", 404)
    factor = session.scalar(
        select(Factor).filter_by(owner_id=owner_id, id=version.factor_id)
    )
    obj = session.scalar(
        select(FactorObject).filter_by(owner_id=owner_id, id=version.code_object_id)
    )
    if (
        factor is None
        or obj is None
        or version.owner_id != owner_id
        or version.id != parent_version_id
        or factor.owner_id != owner_id
        or obj.owner_id != owner_id
        or factor.id != version.factor_id
        or obj.id != version.code_object_id
        or obj.object_kind != "code"
        or obj.media_type != "text/plain"
        or obj.sha256 != version.code_sha256
        or type(obj.size_bytes) is not int
        or not 1 <= obj.size_bytes <= 131072
        or obj.storage_key != f"factors/{owner_id}/objects/{obj.sha256}.py"
    ):
        raise DomainError(
            "seed_parent_integrity", "Parent storage identity differs", 409
        )
    try:
        if len(canonical_bytes(version.source_provenance)) > 65536:
            raise ValueError(
                "Parent provenance exceeds explicit retained metadata bound"
            )
        definition = FactorDefinition.model_validate(version.definition)
        if definition.model_dump(mode="json") != version.definition:
            raise ValueError("Noncanonical parent definition")
        with source_storage_guard(settings.storage_root):
            raw = read_verified(
                settings.storage_root, obj.storage_key, obj.sha256, obj.size_bytes
            )
        source = raw.decode("utf-8")
        if source != version.code or len(raw) != obj.size_bytes:
            raise ValueError("Parent retained source differs from version")
        value = ResolvedSeedParent(
            reference=SeedParentReference(
                owner_id=owner_id,
                factor_id=factor.id,
                factor_version_id=version.id,
                ordinal=version.ordinal,
                code_object_id=obj.id,
                code_sha256=obj.sha256,
                code_size_bytes=obj.size_bytes,
                definition_sha256=version.definition_sha256,
                source_provenance_sha256=digest(version.source_provenance),
            ),
            definition=definition,
            source=source,
        )
        value.prompt_section()  # Explicit unsupported size, not truncated context.
        return value
    except (ValueError, TypeError, UnicodeError):
        raise DomainError(
            "seed_parent_integrity",
            "Parent source or definition cannot be verified",
            409,
        ) from None


def require_seeded_submission_authority(*_args, **_kwargs):
    """No silent use of the old fresh-only writer while026 is uninstalled."""
    raise DomainError(
        "seeded_submission_not_enabled",
        "Versioned seed-parent submission is not installed",
        409,
    )


def read_saved_seed(row):
    """Decode immutable026 request/reference, never synthesize an old023 receipt."""
    from backend.app.rdagent_seed_contracts import RDSeededSubmissionRequest
    from packages.strategy_sdk import JsonLimits, parse_json

    try:
        raw = row.request_canonical.encode("utf-8")
        document = parse_json(
            raw, limits=JsonLimits(max_bytes=131072, max_nodes=10000, max_items=256)
        )
        if (
            document.get("schema_version")
            == "mineagent.rdagent-provider-seeded-submission.v1"
        ):
            from backend.app.rdagent_provider_submission_contracts import (
                RDProviderSeedRequest,
            )

            request = RDProviderSeedRequest.model_validate(document)
        else:
            request = RDSeededSubmissionRequest.model_validate(document)
        reference = SeedParentReference(
            owner_id=row.owner_id,
            factor_id=row.parent_factor_id,
            factor_version_id=row.parent_factor_version_id,
            ordinal=row.parent_ordinal,
            code_object_id=row.parent_code_object_id,
            code_sha256=row.parent_code_sha256,
            code_size_bytes=row.parent_code_size_bytes,
            definition_sha256=row.parent_definition_sha256,
            source_provenance_sha256=row.parent_provenance_sha256,
        )
        provenance_raw = row.parent_provenance_canonical.encode("utf-8")
        provenance = parse_json(
            provenance_raw,
            limits=JsonLimits(max_bytes=65536, max_nodes=10000, max_items=256),
        )
        if (
            type(provenance) is not dict
            or canonical_bytes(provenance) != provenance_raw
            or digest(provenance) != reference.source_provenance_sha256
        ):
            raise ValueError("Parent provenance commitment differs")
        if (
            canonical_bytes(request) != raw
            or digest(request) != row.request_sha256
            or reference.sha256 != row.parent_binding_sha256
            or request.parent_factor_version_id != reference.factor_version_id
        ):
            raise ValueError("Seed declaration commitment differs")
        return request, reference
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise DomainError(
            "seed_submission_drift", "Retained seed declaration differs", 409
        ) from None


def reread_saved_seed(session, owner_id, row, settings):
    """Fresh byte revalidation for future fenced admission, not a launch grant.

    Historical receipt replay uses read_saved_seed only; a missing/tampered parent
    must not erase an already accepted receipt. New physical work uses this path
    inside its current owner/fence transaction and fails before any launch.
    """
    if row.owner_id != owner_id:
        raise DomainError("not_found", "Owned seed declaration not found", 404)
    _, saved = read_saved_seed(row)
    current = resolve_seed_parent(session, owner_id, saved.factor_version_id, settings)
    if current.reference != saved:
        raise DomainError(
            "seed_parent_drift", "Parent binding changed after declaration", 409
        )
    return current


def require_initial_seed(session, fence, *, submission_id, parent_factor_version_id):
    """Exact persisted first-start declaration under the already-current fence.

    Not a byte-read or controller authority. Those are separately checked by the
    admitted seeded bootstrap and fresh publication path. Direct SQL enforces the
    same mandatory parent relationship, including omitted seed declarations.
    """
    from uuid import UUID

    from sqlalchemy import text

    from backend.app.factor_analysis_models import FactorAnalysisPlan
    from backend.app.models import ResearchRun
    from backend.app.rdagent_models import FactorResearchSpec
    from backend.app.rdagent_seed_models import FactorSeededSubmission
    from backend.app.rdagent_seed_submission import validate_saved_seed_binding
    from backend.app.research_lifecycle import require_neutral_transaction

    require_neutral_transaction(session, fence)
    if type(submission_id) is not UUID or type(parent_factor_version_id) is not UUID:
        raise DomainError(
            "factor_initial_parent", "Exact seeded declaration and parent required", 409
        )
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        ["026_rd_seeded_submissions"],
        ["027_rd_controller_v3"],
        ["027a_contract_repairs"],
        ["027b_provider_material"],
        ["027c_seeded_controller_v4"],
    ):
        raise DomainError(
            "factor_initial_parent", "Seeded generation schema unavailable", 409
        )
    row = session.scalar(
        select(FactorSeededSubmission).filter_by(
            owner_id=fence.owner_id,
            run_id=fence.run_id,
            id=submission_id,
        )
    )
    if row is None or row.parent_factor_version_id != parent_factor_version_id:
        raise DomainError(
            "factor_initial_parent",
            "Initial parent differs from immutable queued declaration",
            409,
        )
    run = session.get(ResearchRun, fence.run_id)
    validate_saved_seed_binding(
        row,
        run,
        session.get(FactorResearchSpec, row.spec_id),
        session.get(FactorAnalysisPlan, row.analysis_plan_id),
        fence.owner_id,
        run.idempotency_key,
    )
    return row


def saved_seed_metadata(session, owner_id, row):
    """Expected bootstrap bytes from immutable metadata, NOT a physical read grant.

    Queued claim and the external issuer must independently use reread_saved_seed
    before new work. This path is also usable for data-only cold reconstruction.
    """
    if row.owner_id != owner_id:
        raise DomainError("not_found", "Owned seed declaration not found", 404)
    _, reference = read_saved_seed(row)
    version = session.scalar(
        select(FactorVersion).filter_by(
            owner_id=owner_id, id=reference.factor_version_id
        )
    )
    if version is None or (
        version.factor_id,
        version.ordinal,
        version.code_object_id,
        version.code_sha256,
        version.definition_sha256,
    ) != (
        reference.factor_id,
        reference.ordinal,
        reference.code_object_id,
        reference.code_sha256,
        reference.definition_sha256,
    ):
        raise DomainError(
            "seed_parent_drift", "Parent metadata differs from declaration", 409
        )
    try:
        if (
            canonical_bytes(version.source_provenance).decode()
            != row.parent_provenance_canonical
        ):
            raise ValueError("Parent provenance drift")
        return ResolvedSeedParent(
            reference=reference, definition=version.definition, source=version.code
        )
    except (ValueError, TypeError):
        raise DomainError(
            "seed_parent_drift", "Parent source metadata differs from declaration", 409
        ) from None

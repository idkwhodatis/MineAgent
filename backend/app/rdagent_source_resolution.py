"""Fresh transaction-bound source resolution; returned values confer no authority."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from backend.app.factor_models import FactorVersion
from backend.app.rdagent_models import FactorGenerationSource
from backend.app.rdagent_services import _scope_row
from backend.app.rdagent_source_witness_services import persist_source_read_witness
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.services import DomainError


@dataclass(frozen=True)
class ResolvedSourceCode:
    owner_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int
    source_id: UUID
    start_id: UUID
    factor_version_id: UUID
    code_object_id: UUID
    code_sha256: str
    size_bytes: int
    code_utf8: bytes
    read_witness_id: UUID
    read_witness_sha256: str


def require_source_code(
    session,
    fence,
    *,
    source_id,
    expected_factor_version_id,
    expected_code_object_id,
    expected_sha256,
    settings,
):
    """Resolve actual existing evidence each call; never create/backfill a witness.

    The caller must already hold the combined factor_objects transaction scope.
    Retained model responses, assembly and source bytes are re-read by the witness
    resolver. A prior return value cannot authorize a later write or transaction.
    """
    require_neutral_transaction(session, fence, resource_scope="factor_objects")
    source = _scope_row(session, FactorGenerationSource, source_id, fence, lock=True)
    witness = persist_source_read_witness(
        session, fence, source_id=source_id, settings=settings, allow_create=False
    )
    if (
        source.factor_version_id,
        witness.source_object_id,
        witness.source_sha256,
    ) != (expected_factor_version_id, expected_code_object_id, expected_sha256):
        raise DomainError(
            "factor_source_reference_drift", "Exact retained source differs", 409
        )
    version = session.scalar(
        select(FactorVersion).filter_by(
            owner_id=fence.owner_id, id=source.factor_version_id
        )
    )
    if version is None or version.code_sha256 != witness.source_sha256:
        raise DomainError(
            "factor_source_reference_drift", "Retained source differs", 409
        )
    # The witness reread above verified this exact immutable text against its file.
    code_utf8 = version.code.encode("utf-8")
    if len(code_utf8) != witness.source_size_bytes:
        raise DomainError(
            "factor_source_reference_drift", "Retained source size differs", 409
        )
    return ResolvedSourceCode(
        owner_id=fence.owner_id,
        run_id=fence.run_id,
        attempt_id=fence.attempt_id,
        generation=fence.generation,
        source_id=source.id,
        start_id=source.start_id,
        factor_version_id=source.factor_version_id,
        code_object_id=witness.source_object_id,
        code_sha256=witness.source_sha256,
        size_bytes=witness.source_size_bytes,
        code_utf8=code_utf8,
        read_witness_id=witness.id,
        read_witness_sha256=witness.witness_sha256,
    )

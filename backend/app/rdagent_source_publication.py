"""Inactive two-phase source publication; no helper owns a DB transaction.

Pre-admission is durable before any file write. Unknown writes retain the whole
reservation. The factor_objects resource scope must be acquired before Run locks.
"""

import fcntl
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from backend.app.core.atomic_storage import _open_directory
from backend.app.core.storage_barrier import storage_guard
from backend.app.factor_models import FactorVersion
from backend.app.factor_services import physical_inventory
from backend.app.rdagent_models import (
    FactorGenerationOutcome,
    FactorGenerationStart,
    FactorSourcePublication,
    FactorSourcePublicationOutcome,
    FactorUpstreamCall,
    FactorUpstreamCallOutcome,
)
from backend.app.rdagent_services import (
    _require_orchestrator_receipt,
    _scope,
    _scope_row,
    _verify_response_parts,
    require_research_spec,
)
from backend.app.research_broker_services import require_reservation, reserve_budget
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.research_object_services import read_research_object
from backend.app.services import DomainError
from packages.rdagent_adapter.host_evidence import ResponseAssemblyDocument
from packages.research_runtime.budget_contracts import BudgetVector


def _native_aware_inventory(session, fd, fence, settings):
    from backend.app.factor_native_services import (
        native_capability,
        pending_native_usage,
        verify_native_journals,
    )

    native = native_capability(session)
    entries = []
    total, owned, count = physical_inventory(
        fd,
        fence.owner_id,
        time.monotonic() + 20,
        settings.factor_max_files,
        allow_staging=True,
        native=native,
        native_entries=entries,
        storage_root=settings.storage_root,
    )
    if native:
        verify_native_journals(session, entries, settings)
        pending_total, pending_owned, pending_entries = pending_native_usage(
            session, fence.owner_id
        )
        total += pending_total
        owned += pending_owned
        count += pending_entries
    from backend.app.execution_snapshot_capacity import (
        pending_snapshot_usage_for_schema,
    )

    extra, own, entries = pending_snapshot_usage_for_schema(session, fence.owner_id)
    return total + extra, owned + own, count + entries


# New owner + objects directories, journal final, source temporary and final.
SOURCE_PEAK_FILES = 5

_SOURCE_GUARD = ContextVar("rd_source_storage_guard", default=None)


def _error(code):
    return DomainError(code, "RD source publication could not be verified", 409)


@contextmanager
def source_storage_guard(root):
    """No directory creation or SQL lock acquisition; no transaction ownership."""
    root = Path(root).absolute()
    with storage_guard(root):
        fd = _open_directory(root / "factors")
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise _error("factor_busy") from None
            current, pinned = (
                os.stat(root / "factors", follow_symlinks=False),
                os.fstat(fd),
            )
            if (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
                raise _error("factor_storage_uncertain")
            token = _SOURCE_GUARD.set((root, fd, pinned.st_dev, pinned.st_ino))
            try:
                yield fd
            finally:
                _SOURCE_GUARD.reset(token)
        finally:
            os.close(fd)


def _require_source_reservation(session, fence, publication):
    from backend.app.research_object_models import ResearchObjectPublication

    expected = BudgetVector(storage_bytes=publication.peak_bytes)
    row = require_reservation(
        session,
        fence,
        publication.budget_reservation_id,
        purpose="object_storage",
        minimum=expected,
    )
    if (
        row.logical_key != f"rdsource:{publication.start_id}"
        or row.amounts != expected.model_dump()
    ):
        raise _error("factor_source_reservation_drift")
    if (
        session.scalar(
            select(ResearchObjectPublication).filter_by(budget_reservation_id=row.id)
        )
        is not None
    ):
        raise _error("factor_source_reservation_reused")
    return row


def reserve_source(
    session, fence, *, call_id, assembly_object_id, assembly_sha256, settings
):
    """Flush a pre-admission only; caller must commit before any physical write."""
    require_neutral_transaction(session, fence, resource_scope="factor_objects")
    _, config = require_research_spec(session, fence)
    call = _scope_row(session, FactorUpstreamCall, call_id, fence, lock=True)
    if (call.phase, call.kind) != ("generation", "chat"):
        raise _error("factor_source_call_invalid")
    closed = session.scalar(
        select(FactorUpstreamCallOutcome).filter_by(**_scope(fence), call_id=call.id)
    )
    if closed is None or closed.status != "completed":
        raise _error("factor_source_exchange_incomplete")
    start = _scope_row(session, FactorGenerationStart, call.start_id, fence)
    raw = read_research_object(
        session,
        fence,
        assembly_object_id,
        "factor_response_assembly",
        assembly_sha256,
        settings,
    )
    document = ResponseAssemblyDocument.model_validate_json(raw)
    if document.call_id != call.id:
        raise _error("factor_source_assembly_drift")
    _require_orchestrator_receipt(
        session, fence, document.orchestrator_admission_id, config
    )
    _, responses = _verify_response_parts(
        session, fence, call, document.parts, settings
    )
    from backend.app.rdagent_source_witness_services import (
        require_exact_extracted_source,
    )

    require_exact_extracted_source(responses, document)
    size = len(document.source.encode("utf-8"))
    existing = session.scalar(
        select(FactorSourcePublication).filter_by(**_scope(fence), start_id=start.id)
    )
    if existing is not None:
        if (
            existing.call_id,
            existing.assembly_object_id,
            existing.assembly_sha256,
            existing.source_sha256,
            existing.size_bytes,
        ) != (
            call.id,
            assembly_object_id,
            assembly_sha256,
            document.source_sha256,
            size,
        ):
            raise _error("factor_source_publication_drift")
        return existing
    if (
        session.scalar(
            select(FactorGenerationOutcome).filter_by(
                **_scope(fence), start_id=start.id
            )
        )
        is not None
    ):
        raise _error("factor_generation_closed")
    from backend.app.rdagent_boundary_services import require_unobserved_generation

    require_unobserved_generation(session, fence, start.id)
    factor_id = uuid4()
    if start.parent_factor_version_id is not None:
        parent = session.scalar(
            select(FactorVersion).filter_by(
                owner_id=fence.owner_id, id=start.parent_factor_version_id
            )
        )
        if parent is None:
            raise _error("factor_source_parent_missing")
        factor_id = parent.factor_id
    peak = size * 2 + 8192
    with source_storage_guard(settings.storage_root) as fd:
        from runtimes.factor_parser.supervisor import assert_quiescent

        assert_quiescent(settings.storage_root)
        total, owned, count = _native_aware_inventory(session, fd, fence, settings)
        pending = session.scalars(
            select(FactorSourcePublication)
            .outerjoin(
                FactorSourcePublicationOutcome,
                FactorSourcePublicationOutcome.publication_id
                == FactorSourcePublication.id,
            )
            .where(FactorSourcePublicationOutcome.publication_id.is_(None))
        ).all()
        if (
            total + sum(row.peak_bytes for row in pending) + peak
            > settings.factor_installation_max_bytes
            or owned
            + sum(row.peak_bytes for row in pending if row.owner_id == fence.owner_id)
            + peak
            > settings.factor_owner_max_bytes
            or count + SOURCE_PEAK_FILES * (len(pending) + 1)
            > settings.factor_max_files
        ):
            raise _error("factor_capacity")
        reservation = reserve_budget(
            session,
            fence,
            purpose="object_storage",
            logical_key=f"rdsource:{start.id}",
            amounts=BudgetVector(storage_bytes=peak),
        )
        row = FactorSourcePublication(
            id=uuid4(),
            **_scope(fence),
            start_id=start.id,
            call_id=call.id,
            assembly_object_id=assembly_object_id,
            assembly_object_kind="factor_response_assembly",
            assembly_sha256=assembly_sha256,
            budget_reservation_id=reservation.id,
            source_sha256=document.source_sha256,
            size_bytes=size,
            peak_bytes=peak,
            factor_id=factor_id,
            factor_version_id=uuid4(),
        )
        _require_source_reservation(session, fence, row)
        session.add(row)
        session.flush()
        return row


def source_publication_journal(publication):
    """Fixed bounded recovery identity; content address fits existing .json inventory."""
    import json

    fields = {
        "schema_version": "mineagent.rdagent-source-publication.v1",
        **{
            key: str(getattr(publication, key))
            for key in (
                "id",
                "owner_id",
                "run_id",
                "attempt_id",
                "start_id",
                "call_id",
                "assembly_object_id",
                "budget_reservation_id",
                "factor_id",
                "factor_version_id",
            )
        },
        "generation": publication.generation,
        "assembly_sha256": publication.assembly_sha256,
        "source_sha256": publication.source_sha256,
        "size_bytes": publication.size_bytes,
        "peak_bytes": publication.peak_bytes,
    }
    data = json.dumps(
        fields, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    if len(data) > 4096:
        raise _error("factor_source_journal_oversize")
    return data


def _require_publication_generation_open(session, fence, publication):
    if (
        session.scalar(
            select(FactorGenerationOutcome).filter_by(
                **_scope(fence), start_id=publication.start_id
            )
        )
        is not None
    ):
        raise _error("factor_generation_closed")
    from backend.app.rdagent_boundary_services import require_unobserved_generation

    require_unobserved_generation(session, fence, publication.start_id)


def finalize_source(session, fence, *, publication_id, settings):
    """Flush metadata for already verified source+journal; never writes bytes.

    Caller retains source_storage_guard across this fresh transaction and file
    publication. A failed/uncertain transaction never releases the reservation.
    Settlement requires a separately registered source-outcome proof (not N1's
    research-object proof), so this disabled draft conservatively keeps it held.
    """
    import hashlib

    from sqlalchemy import func

    from backend.app.factor_models import Factor, FactorObject
    from backend.app.rdagent_services import bind_generation_source
    from backend.app.storage import read_verified
    from packages.factors.contracts import digest
    from packages.rdagent_adapter.host_evidence import research_definition

    require_neutral_transaction(session, fence, resource_scope="factor_objects")
    _, config = require_research_spec(session, fence)
    guard = _SOURCE_GUARD.get()
    if guard is None or guard[0] != Path(settings.storage_root).absolute():
        raise _error("factor_source_storage_guard_required")
    current = os.fstat(guard[1])
    named = os.stat(guard[0] / "factors", follow_symlinks=False)
    if (current.st_dev, current.st_ino) != guard[2:] or (
        named.st_dev,
        named.st_ino,
    ) != guard[2:]:
        raise _error("factor_source_storage_guard_required")
    publication = _scope_row(
        session, FactorSourcePublication, publication_id, fence, lock=True
    )
    journal = source_publication_journal(publication)
    journal_sha = hashlib.sha256(journal).hexdigest()
    journal_key = f"factors/{fence.owner_id}/objects/{journal_sha}.json"
    if (
        read_verified(settings.storage_root, journal_key, journal_sha, len(journal))
        != journal
    ):
        raise _error("factor_source_journal_drift")
    source_key = f"factors/{fence.owner_id}/objects/{publication.source_sha256}.py"
    content = read_verified(
        settings.storage_root,
        source_key,
        publication.source_sha256,
        publication.size_bytes,
    )
    if len(content) != publication.size_bytes:
        raise _error("factor_source_size_drift")
    raw = read_research_object(
        session,
        fence,
        publication.assembly_object_id,
        "factor_response_assembly",
        publication.assembly_sha256,
        settings,
    )
    document = ResponseAssemblyDocument.model_validate_json(raw)
    if (document.call_id, document.source.encode(), document.source_sha256) != (
        publication.call_id,
        content,
        publication.source_sha256,
    ):
        raise _error("factor_source_assembly_drift")
    start = _scope_row(session, FactorGenerationStart, publication.start_id, fence)
    previous = session.scalar(
        select(FactorSourcePublicationOutcome).filter_by(
            **_scope(fence), publication_id=publication.id
        )
    )
    if previous is None:
        _require_source_reservation(session, fence, publication)
    if (
        previous is None
        and session.scalar(
            select(FactorGenerationOutcome).filter_by(
                **_scope(fence), start_id=start.id
            )
        )
        is not None
    ):
        raise _error("factor_generation_closed")
    if previous is None:
        _require_publication_generation_open(session, fence, publication)
    factor = session.scalar(
        select(Factor)
        .filter_by(owner_id=fence.owner_id, id=publication.factor_id)
        .with_for_update()
    )
    if factor is None:
        if start.parent_factor_version_id is not None:
            raise _error("factor_source_parent_missing")
        factor = Factor(
            id=publication.factor_id,
            owner_id=fence.owner_id,
            name=config.task.name,
            category="rdagent",
        )
        session.add(factor)
        session.flush()
    obj = session.scalar(
        select(FactorObject).filter_by(owner_id=fence.owner_id, storage_key=source_key)
    )
    if obj is None:
        obj = FactorObject(
            id=uuid4(),
            owner_id=fence.owner_id,
            storage_key=source_key,
            sha256=publication.source_sha256,
            size_bytes=publication.size_bytes,
            media_type="text/plain",
            object_kind="code",
        )
        session.add(obj)
        session.flush()
    elif (obj.object_kind, obj.media_type, obj.sha256, obj.size_bytes) != (
        "code",
        "text/plain",
        publication.source_sha256,
        publication.size_bytes,
    ):
        raise _error("factor_source_object_drift")
    version = session.scalar(
        select(FactorVersion).filter_by(
            owner_id=fence.owner_id, id=publication.factor_version_id
        )
    )
    if version is None:
        parent = None
        if start.parent_factor_version_id is not None:
            parent = session.scalar(
                select(FactorVersion).filter_by(
                    owner_id=fence.owner_id, id=start.parent_factor_version_id
                )
            )
            if parent is None or parent.factor_id != factor.id:
                raise _error("factor_source_parent_drift")
        ordinal = (
            session.scalar(
                select(func.max(FactorVersion.ordinal)).where(
                    FactorVersion.factor_id == factor.id
                )
            )
            or 0
        ) + 1
        definition = research_definition(config, publication.source_sha256).model_dump(
            mode="json"
        )
        version = FactorVersion(
            id=publication.factor_version_id,
            owner_id=fence.owner_id,
            factor_id=factor.id,
            ordinal=ordinal,
            parent_version_id=parent.id if parent else None,
            definition=definition,
            definition_sha256=digest(definition),
            code=document.source,
            code_sha256=publication.source_sha256,
            code_object_id=obj.id,
            source_kind="rdagent",
            source_event_key=f"rdagent:{start.id}",
            source_provenance={
                "schema_version": "mineagent.rdagent-source.v1",
                "run_id": str(fence.run_id),
                "start_id": str(start.id),
                "call_id": str(publication.call_id),
                "assembly_sha256": publication.assembly_sha256,
                "explanation_origin": "task_declaration",
            },
        )
        session.add(version)
        session.flush()
    if version.factor_id != publication.factor_id or version.code_object_id != obj.id:
        raise _error("factor_source_version_drift")
    source = bind_generation_source(
        session,
        fence,
        call_id=publication.call_id,
        factor_version_id=version.id,
        assembly_object_id=publication.assembly_object_id,
        assembly_sha256=publication.assembly_sha256,
        settings=settings,
    )
    if previous is not None:
        if (previous.source_id, previous.journal_sha256) != (source.id, journal_sha):
            raise _error("factor_source_outcome_drift")
        return previous
    result = FactorSourcePublicationOutcome(
        id=uuid4(),
        **_scope(fence),
        publication_id=publication.id,
        source_id=source.id,
        journal_sha256=journal_sha,
    )
    session.add(result)
    session.flush()
    return result


def _reject_temporary_files(fd, *, maximum):
    """No retry allocates another temporary while a crash-left write is unresolved."""
    count = 0
    deadline = time.monotonic() + 20

    def scan(directory, depth):
        nonlocal count
        if depth > 6 or time.monotonic() >= deadline:
            raise _error("factor_source_inventory_bound")
        for name in os.listdir(directory):
            count += 1
            if count > maximum or name.endswith(".tmp"):
                raise _error("factor_source_recovery_required")
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            import stat

            if stat.S_ISDIR(info.st_mode):
                child = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                )
                try:
                    scan(child, depth + 1)
                finally:
                    os.close(child)
            elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise _error("factor_source_inventory_unsafe")

    scan(fd, 0)


def publish_reserved_source(factory, fence, *, publication_id, content, settings):
    """Write only committed bounded source admission, then freshly fence metadata.

    Filesystem guard survives transaction loss. This wrapper owns short transactions;
    reserve/finalize helpers never commit/rollback. Any error leaves durable peak
    reservation and files/journal for recovery; there is no exception refund.
    """
    import hashlib

    from backend.app.core.atomic_storage import publish_bytes
    from backend.app.core.maintenance import shared_lifecycle
    from backend.app.research_lifecycle import neutral_transaction

    if type(content) is not bytes or not 1 <= len(content) <= 131072:
        raise _error("factor_source_content_invalid")
    content.decode("utf-8", errors="strict")
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        source_storage_guard(settings.storage_root) as fd,
    ):
        with neutral_transaction(
            factory, fence, resource_scope="factor_objects"
        ) as session:
            require_research_spec(session, fence)
            publication = _scope_row(
                session, FactorSourcePublication, publication_id, fence, lock=True
            )
            if (hashlib.sha256(content).hexdigest(), len(content)) != (
                publication.source_sha256,
                publication.size_bytes,
            ):
                raise _error("factor_source_content_drift")
            if (
                session.scalar(
                    select(FactorSourcePublicationOutcome).filter_by(
                        **_scope(fence), publication_id=publication.id
                    )
                )
                is not None
            ):
                return finalize_source(
                    session, fence, publication_id=publication.id, settings=settings
                )
            _require_publication_generation_open(session, fence, publication)
            _require_source_reservation(session, fence, publication)
            from runtimes.factor_parser.supervisor import assert_quiescent

            assert_quiescent(settings.storage_root)
            total, owned, count = _native_aware_inventory(session, fd, fence, settings)
            _reject_temporary_files(fd, maximum=settings.factor_max_files)
            pending = session.scalars(
                select(FactorSourcePublication)
                .outerjoin(
                    FactorSourcePublicationOutcome,
                    FactorSourcePublicationOutcome.publication_id
                    == FactorSourcePublication.id,
                )
                .where(FactorSourcePublicationOutcome.publication_id.is_(None))
            ).all()
            # Conservative: even current existing final bytes are not credited.
            if (
                total + sum(row.peak_bytes for row in pending)
                > settings.factor_installation_max_bytes
                or owned
                + sum(
                    row.peak_bytes for row in pending if row.owner_id == fence.owner_id
                )
                > settings.factor_owner_max_bytes
                or count + SOURCE_PEAK_FILES * len(pending) > settings.factor_max_files
            ):
                raise _error("factor_capacity")
            journal = source_publication_journal(publication)
            source_sha = publication.source_sha256
            journal_sha = hashlib.sha256(journal).hexdigest()
            source_key = f"factors/{fence.owner_id}/objects/{source_sha}.py"
            journal_key = f"factors/{fence.owner_id}/objects/{journal_sha}.json"
        publish_bytes(
            Path(settings.storage_root) / journal_key,
            journal,
            sha256=journal_sha,
            durable_root=Path(settings.storage_root),
        )
        publish_bytes(
            Path(settings.storage_root) / source_key,
            content,
            sha256=source_sha,
            durable_root=Path(settings.storage_root),
        )
        with neutral_transaction(
            factory, fence, resource_scope="factor_objects"
        ) as session:
            result = finalize_source(
                session, fence, publication_id=publication_id, settings=settings
            )
        return result


def settle_source_conservatively(session, fence, *, publication_id, settings):
    """Exact verified source outcome is required; charge full peak, never refund.

    This uses existing conservative_unknown semantics, not a fabricated measured
    source/cleanup proof. A future measured settlement adapter needs independent
    temporary/orphan cleanup and final physical inventory evidence.
    """
    from backend.app.research_broker_services import settle_budget

    outcome = finalize_source(
        session, fence, publication_id=publication_id, settings=settings
    )
    publication = _scope_row(
        session, FactorSourcePublication, outcome.publication_id, fence
    )
    return settle_budget(
        session,
        fence,
        publication.budget_reservation_id,
        charge=BudgetVector(storage_bytes=publication.peak_bytes),
        outcome="conservative_unknown",
    )

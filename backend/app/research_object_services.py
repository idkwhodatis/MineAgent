"""Bounded neutral publication with durable pre-admission and charged uncertainty.

No helper commits, rolls back, reconnects or releases a physical reservation.
The orchestrator owns fresh transactions around (never across) filesystem writes.
Files and immutable journals survive a lost outcome commit for cold closure.
"""

import fcntl
import hashlib
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select

from backend.app.core.atomic_storage import _open_directory, publish_bytes
from backend.app.core.maintenance import shared_lifecycle
from backend.app.core.storage_barrier import storage_guard
from backend.app.research_lifecycle import (
    neutral_transaction,
    require_neutral_transaction,
)
from backend.app.research_object_models import (
    ResearchObject,
    ResearchObjectOutcome,
    ResearchObjectPublication,
)
from backend.app.services import DomainError
from packages.rdagent_provider.object_identity import (
    identity_for_content,
    parse_storage_key,
    publication_journal,
)
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.object_identity import (
    MAX_JOURNAL_BYTES,
    MAX_OBJECT_BYTES,
)

INSTALLATION_MAX_BYTES = 2 * 1024**3
OWNER_MAX_BYTES = 512 * 1024**2
MAX_FILES = 10000


def _versioned_identity(session, owner, kind, content):
    if kind == "rd_provider_process_receipt":
        from sqlalchemy import text

        if session.scalars(
            text("SELECT version_num FROM alembic_version")
        ).all() not in (
            ["026_rd_seeded_submissions"],
            ["027_rd_controller_v3"],
            ["027a_contract_repairs"],
        ):
            raise _error("rd_provider_receipt_revision")
    if kind == "factor_generation_feedback":
        if type(content) is not bytes or not 1 <= len(content) <= 65536:
            raise _error("rd_feedback_body_bound")
        import json

        try:
            version = json.loads(content).get("schema_version")
        except (ValueError, AttributeError, UnicodeError, RecursionError):
            version = None
        if version == "mineagent.rd-execution-feedback.v2":
            from sqlalchemy import text

            from packages.rdagent_adapter.analysis_object_codec import (
                identity_for_content as analysis_identity,
            )

            if session.scalars(
                text("SELECT version_num FROM alembic_version")
            ).all() not in (
                ["022_factor_analysis"],
                ["023_rd_submissions"],
                ["024_openevolve_initial"],
                ["025_structured_factor_strategy"],
                ["026_rd_seeded_submissions"],
                ["027_rd_controller_v3"],
                ["027a_contract_repairs"],
            ):
                raise _error("rd_analysis_feedback_revision")
            return analysis_identity(owner, kind, content)
    return identity_for_content(owner, kind, content)


def _error(code="neutral_object_uncertain"):
    return DomainError(code, "Neutral object publication could not be verified", 409)


@dataclass(frozen=True)
class PhysicalObject:
    storage_key: str
    size_bytes: int
    owner_id: UUID
    temporary: bool


def physical_inventory(
    root: Path, *, max_files: int = MAX_FILES
) -> tuple[PhysicalObject, ...]:
    """Bounded no-follow inventory including every orphan, journal and temp byte."""
    root = Path(root).absolute()
    records = []
    count = 0

    def scan(fd, parts):
        nonlocal count
        before = os.fstat(fd)
        for name in os.listdir(fd):
            count += 1
            if count > max_files * 2:
                raise _error("neutral_object_capacity")
            childparts = (*parts, name)
            child = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
            )
            try:
                info = os.fstat(child)
                if stat.S_ISDIR(info.st_mode):
                    if len(childparts) == 1:
                        if str(UUID(name)) != name:
                            raise _error()
                    elif len(childparts) == 2:
                        if name not in {"by-kind", "journals"}:
                            raise _error()
                    elif len(childparts) == 3:
                        if childparts[1] != "by-kind" or name not in {
                            "model_request",
                            "model_response",
                            "process_receipt",
                            "rd_provider_process_receipt",
                            "factor_response_assembly",
                            "factor_generation_proposal",
                            "factor_generation_feedback",
                            "factor_adoption_receipt",
                            "search_checkpoint_chunk",
                            "search_checkpoint_file",
                            "search_checkpoint_inventory",
                            "search_evaluation_input",
                            "search_source",
                            "search_rendered_prompt",
                            "search_proposal_source",
                            "search_checkpoint_closure",
                            "search_worker_identity",
                            "search_population",
                            "search_context",
                            "search_controller_event",
                            "search_worker_outcome",
                            "search_native_observation",
                            "search_native_failure",
                            "search_objective",
                        }:
                            raise _error()
                    else:
                        raise _error()
                    scan(child, childparts)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    key = "research-objects/" + "/".join(childparts)
                    domain, owner, _, _, temporary = parse_storage_key(key)
                    maximum = (
                        MAX_JOURNAL_BYTES if domain == "journal" else MAX_OBJECT_BYTES
                    )
                    if info.st_size > maximum:
                        raise _error("neutral_object_capacity")
                    records.append(PhysicalObject(key, info.st_size, owner, temporary))
                    if len(records) > max_files:
                        raise _error("neutral_object_capacity")
                else:
                    raise _error()
            finally:
                os.close(child)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise _error()

    from backend.app.core.atomic_storage import _open_directory

    try:
        fd = _open_directory(root / "research-objects")
    except FileNotFoundError:
        return ()
    try:
        scan(fd, ())
    except (OSError, ValueError) as exc:
        raise _error() from exc
    finally:
        os.close(fd)
    return tuple(records)


@contextmanager
def object_storage_guard(root):
    """Filesystem fence retained across transaction loss and publication cleanup."""
    root = Path(root).absolute()
    with storage_guard(root) as root_fd:
        fd = _open_directory(root / "research-objects")
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise _error("neutral_object_busy") from None
            current = os.stat(root / "research-objects", follow_symlinks=False)
            pinned = os.fstat(fd)
            if (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
                raise _error()
            yield (root_fd, fd)
        finally:
            os.close(fd)


def _read_exact(root, key, sha256, size):
    """Descriptor-pinned immutable reads reject links, extra bytes and corruption."""
    from backend.app.core.atomic_storage import _open_directory

    parse_storage_key(key)
    parent = _open_directory(Path(root).absolute() / Path(key).parent)
    try:
        fd = os.open(
            Path(key).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size != size
            ):
                raise _error()
            content = bytearray()
            while chunk := os.read(fd, min(65536, size + 1 - len(content))):
                content.extend(chunk)
                if len(content) > size:
                    raise _error()
            if len(content) != size or hashlib.sha256(content).hexdigest() != sha256:
                raise _error()
            return bytes(content)
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def _check_write_capacity(session, fence, publication, settings):
    """Re-admit every physical write under the retained namespace fence.

    Crash-left temporary files are evidence of an unresolved earlier writer, not
    reusable headroom. No retry creates another temporary until explicit recovery.
    Counting all existing bytes plus every outstanding peak is conservative.
    """
    inventory = physical_inventory(
        settings.storage_root, max_files=settings.neutral_object_max_files
    )
    if any(item.temporary for item in inventory):
        raise _error("neutral_object_recovery_required")
    held = session.scalars(
        select(ResearchObjectPublication)
        .outerjoin(
            ResearchObjectOutcome,
            ResearchObjectOutcome.publication_id == ResearchObjectPublication.id,
        )
        .where(ResearchObjectOutcome.publication_id.is_(None))
    ).all()
    # Credit only exact current final files already covered by this peak. Never
    # credit orphan/temp bytes or another publication's unresolved reservation.
    journal = publication_journal(publication)
    current = {
        publication.storage_key: (publication.sha256, publication.size_bytes),
        f"research-objects/{fence.owner_id}/journals/{publication.id}.json": (
            hashlib.sha256(journal).hexdigest(),
            len(journal),
        ),
    }
    credited = []
    for item in inventory:
        if item.storage_key in current:
            digest, size = current[item.storage_key]
            _read_exact(settings.storage_root, item.storage_key, digest, size)
            credited.append(item)
    others = [row for row in held if row.id != publication.id]
    total = (
        sum(item.size_bytes for item in inventory)
        - sum(item.size_bytes for item in credited)
        + publication.peak_bytes
        + sum(row.peak_bytes for row in others)
    )
    owned = (
        sum(item.size_bytes for item in inventory if item.owner_id == fence.owner_id)
        - sum(item.size_bytes for item in credited)
        + publication.peak_bytes
        + sum(row.peak_bytes for row in others if row.owner_id == fence.owner_id)
    )
    from backend.app.openevolve_process_quota import combined_holds

    upload_capacity = combined_holds(session, settings)
    total += sum(x[1] for x in upload_capacity)
    owned += sum(x[1] for x in upload_capacity if x[0] == fence.owner_id)
    if (
        total > settings.neutral_object_installation_max_bytes
        or owned > settings.neutral_object_owner_max_bytes
        or len(inventory)
        - len(credited)
        + 4 * (len(others) + 1)
        + sum(x[2] for x in upload_capacity)
        > settings.neutral_object_max_files
    ):
        raise _error("neutral_object_capacity")


def reserve_object(session, fence, kind, content, budget_reservation_id, settings):
    """Create immutable pre-admission inside caller's committed neutral boundary."""
    require_neutral_transaction(session, fence, resource_scope="objects")
    from backend.app.research_broker_services import require_reservation

    if kind == "rd_provider_process_receipt":
        from backend.app.rdagent_services import require_research_spec
        from packages.rdagent_provider.process_receipts import parse_receipt

        _, config = require_research_spec(session, fence)
        document = parse_receipt(content)
        if (
            config.schema_version != "mineagent.factor-research.v2"
            or any(
                getattr(document, key) != getattr(fence, key)
                for key in ("owner_id", "run_id", "attempt_id", "generation")
            )
            or document.model_binding_sha256 != config.model_binding.sha256
        ):
            raise _error("rd_provider_receipt_scope")
    if kind == "factor_response_assembly":
        from backend.app.rdagent_services import require_research_spec

        require_research_spec(session, fence)
    from packages.rdagent_adapter.execution_object_identity import KINDS

    if kind in KINDS:
        from sqlalchemy import text

        from backend.app.rdagent_services import require_research_spec
        from packages.rdagent_adapter.execution_outcomes import parse_document

        revisions = session.scalars(
            text("SELECT version_num FROM alembic_version")
        ).all()
        if revisions not in (
            ["021_execution_snapshots"],
            ["022_factor_analysis"],
            ["023_rd_submissions"],
            ["024_openevolve_initial"],
            ["025_structured_factor_strategy"],
            ["026_rd_seeded_submissions"],
            ["027_rd_controller_v3"],
            ["027a_contract_repairs"],
        ):
            raise _error("rd_execution_object_revision")
        require_research_spec(session, fence)
        if revisions in (
            ["022_factor_analysis"],
            ["023_rd_submissions"],
            ["024_openevolve_initial"],
            ["025_structured_factor_strategy"],
            ["026_rd_seeded_submissions"],
            ["027_rd_controller_v3"],
            ["027a_contract_repairs"],
        ):
            from packages.rdagent_adapter.analysis_object_codec import parse_document
        document = parse_document(kind, content)
        if any(
            getattr(document, key) != getattr(fence, key)
            for key in ("owner_id", "run_id", "attempt_id", "generation")
        ):
            raise _error("rd_execution_object_scope")
    from packages.openevolve_adapter.object_identity import KINDS as OE_KINDS

    if kind in OE_KINDS:
        from sqlalchemy import text

        from backend.app.openevolve_services import _current_search
        from packages.openevolve_adapter.object_identity import (
            parse_document as parse_oe,
        )
        from packages.openevolve_adapter.persisted_identity import (
            require_search_identity,
        )

        if session.scalars(
            text("SELECT version_num FROM alembic_version")
        ).all() not in (
            ["024_openevolve_initial"],
            ["025_structured_factor_strategy"],
            ["026_rd_seeded_submissions"],
            ["027_rd_controller_v3"],
            ["027a_contract_repairs"],
        ):
            raise _error("oe_object_revision")
        require_search_identity(_current_search(session, fence))
        document = parse_oe(kind, content)
        if hasattr(document, "owner_id") and any(
            getattr(document, key) != getattr(fence, key)
            for key in ("owner_id", "run_id", "attempt_id", "generation")
        ):
            raise _error("oe_object_scope")
    identity = _versioned_identity(session, fence.owner_id, kind, content)
    peak = 2 * len(content) + 2 * MAX_JOURNAL_BYTES
    require_reservation(
        session,
        fence,
        budget_reservation_id,
        purpose="object_storage",
        minimum=BudgetVector(storage_bytes=peak),
    )
    previous = session.scalar(
        select(ResearchObjectPublication).where(
            ResearchObjectPublication.budget_reservation_id == budget_reservation_id
        )
    )
    if previous is not None:
        if (
            previous.owner_id,
            previous.run_id,
            previous.attempt_id,
            previous.generation,
            previous.object_kind,
            previous.sha256,
            previous.size_bytes,
        ) != (
            fence.owner_id,
            fence.run_id,
            fence.attempt_id,
            fence.generation,
            kind,
            identity.sha256,
            len(content),
        ):
            raise _error("neutral_object_request_drift")
        return previous
    with object_storage_guard(settings.storage_root):
        inventory = physical_inventory(
            settings.storage_root, max_files=settings.neutral_object_max_files
        )
        pending = (
            select(ResearchObjectPublication)
            .outerjoin(
                ResearchObjectOutcome,
                ResearchObjectOutcome.publication_id == ResearchObjectPublication.id,
            )
            .where(ResearchObjectOutcome.publication_id.is_(None))
        )
        held = session.scalars(pending).all()
        total = sum(item.size_bytes for item in inventory) + sum(
            row.peak_bytes for row in held
        )
        owned = sum(
            item.size_bytes for item in inventory if item.owner_id == fence.owner_id
        ) + sum(row.peak_bytes for row in held if row.owner_id == fence.owner_id)
        from backend.app.openevolve_process_quota import combined_holds

        upload_capacity = combined_holds(session, settings)
        total += sum(x[1] for x in upload_capacity)
        owned += sum(x[1] for x in upload_capacity if x[0] == fence.owner_id)
        if (
            total + peak > settings.neutral_object_installation_max_bytes
            or owned + peak > settings.neutral_object_owner_max_bytes
            or len(inventory) + 4 * (len(held) + 1) + sum(x[2] for x in upload_capacity)
            > settings.neutral_object_max_files
        ):
            raise _error("neutral_object_capacity")
        publication = ResearchObjectPublication(
            id=uuid4(),
            owner_id=fence.owner_id,
            run_id=fence.run_id,
            attempt_id=fence.attempt_id,
            generation=fence.generation,
            budget_reservation_id=budget_reservation_id,
            object_kind=kind,
            sha256=identity.sha256,
            size_bytes=len(content),
            storage_key=identity.storage_key,
            peak_bytes=peak,
        )
        session.add(publication)
        session.flush()
        return publication


def _publication(session, fence, publication_id):
    row = session.scalar(
        select(ResearchObjectPublication).where(
            ResearchObjectPublication.id == publication_id,
            ResearchObjectPublication.owner_id == fence.owner_id,
            ResearchObjectPublication.run_id == fence.run_id,
            ResearchObjectPublication.attempt_id == fence.attempt_id,
            ResearchObjectPublication.generation == fence.generation,
        )
    )
    if row is None:
        raise _error("neutral_object_not_found")
    return row


def finalize_object(session, fence, publication_id, settings):
    """Reference only verified bytes; caller commits outcome or retains uncertainty."""
    require_neutral_transaction(session, fence, resource_scope="objects")
    publication = _publication(session, fence, publication_id)
    journal = publication_journal(publication)
    journal_hash = hashlib.sha256(journal).hexdigest()
    _read_exact(
        settings.storage_root,
        f"research-objects/{fence.owner_id}/journals/{publication.id}.json",
        journal_hash,
        len(journal),
    )
    content = _read_exact(
        settings.storage_root,
        publication.storage_key,
        publication.sha256,
        publication.size_bytes,
    )
    _versioned_identity(session, fence.owner_id, publication.object_kind, content)
    existing = session.scalar(
        select(ResearchObject).where(
            ResearchObject.owner_id == fence.owner_id,
            ResearchObject.object_kind == publication.object_kind,
            ResearchObject.sha256 == publication.sha256,
        )
    )
    if existing is None:
        existing = ResearchObject(
            id=uuid4(),
            owner_id=fence.owner_id,
            object_kind=publication.object_kind,
            sha256=publication.sha256,
            size_bytes=publication.size_bytes,
            media_type="application/json",
            storage_key=publication.storage_key,
        )
        session.add(existing)
        session.flush()
    elif (existing.size_bytes, existing.storage_key, existing.media_type) != (
        publication.size_bytes,
        publication.storage_key,
        "application/json",
    ):
        raise _error()
    outcome = session.get(ResearchObjectOutcome, publication.id)
    if outcome is None:
        session.add(
            ResearchObjectOutcome(
                publication_id=publication.id,
                owner_id=fence.owner_id,
                object_id=existing.id,
                object_kind=publication.object_kind,
                sha256=publication.sha256,
                journal_sha256=journal_hash,
            )
        )
        session.flush()
    elif outcome.object_id != existing.id or outcome.journal_sha256 != journal_hash:
        raise _error()
    # Verified publication closes the writer, but does not refund one byte of its
    # conservative physical peak. This lets completed evidence advance safely;
    # missing outcomes still retain unresolved reservations and block resume.
    from backend.app.research_broker_models import ResearchBudgetReservation
    from backend.app.research_broker_services import settle_budget

    reservation = session.get(
        ResearchBudgetReservation, publication.budget_reservation_id
    )
    if reservation is None:
        raise _error()
    settle_budget(
        session,
        fence,
        reservation.id,
        charge=BudgetVector.model_validate(reservation.amounts),
        outcome="conservative_unknown",
    )
    return existing.id


def publish_reserved_object(factory, fence, publication_id, content, settings):
    """Publish only an already committed reservation; lease loss leaves charged files.

    Never call from another transaction. The lifecycle/filesystem guards span both
    short DB transactions and writes. No process/model work occurs in this helper.
    """
    from backend.app.openevolve_lock_admission import publication_storage_admission

    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        publication_storage_admission(settings.storage_root, fence),
    ):
        with neutral_transaction(factory, fence, resource_scope="objects") as session:
            publication = _publication(session, fence, publication_id)
            identity = _versioned_identity(
                session, fence.owner_id, publication.object_kind, content
            )
            if (
                identity.storage_key != publication.storage_key
                or len(content) != publication.size_bytes
            ):
                raise _error("neutral_object_request_drift")
            # Completed exact replay performs verified reads only, never another
            # physical publication or fresh temporary allocation.
            if session.get(ResearchObjectOutcome, publication.id) is not None:
                return finalize_object(session, fence, publication.id, settings)
            _check_write_capacity(session, fence, publication, settings)
            from backend.app.research_broker_services import require_reservation

            require_reservation(
                session,
                fence,
                publication.budget_reservation_id,
                purpose="object_storage",
                minimum=BudgetVector(storage_bytes=publication.peak_bytes),
            )
            journal = publication_journal(publication)
            key = publication.storage_key
            journal_key = (
                f"research-objects/{fence.owner_id}/journals/{publication.id}.json"
            )
        # The publication row has been re-read in a fresh committed transaction.
        # DB loss cannot release this independent filesystem fence mid-write.
        publish_bytes(
            Path(settings.storage_root) / journal_key,
            journal,
            sha256=hashlib.sha256(journal).hexdigest(),
            durable_root=Path(settings.storage_root),
        )
        publish_bytes(
            Path(settings.storage_root) / key,
            content,
            sha256=identity.sha256,
            durable_root=Path(settings.storage_root),
        )
        with neutral_transaction(factory, fence, resource_scope="objects") as session:
            result = finalize_object(session, fence, publication_id, settings)
        return result


def read_research_object(session, fence, object_id, kind, sha256, settings):
    """Read exact owner/kind/digest evidence without broad arbitrary-file access."""
    require_neutral_transaction(session, fence)
    row = session.scalar(
        select(ResearchObject).where(
            ResearchObject.owner_id == fence.owner_id,
            ResearchObject.id == object_id,
            ResearchObject.object_kind == kind,
            ResearchObject.sha256 == sha256,
        )
    )
    if row is None:
        raise _error("neutral_object_not_found")
    content = _read_exact(
        settings.storage_root, row.storage_key, row.sha256, row.size_bytes
    )
    _versioned_identity(session, fence.owner_id, kind, content)
    return content

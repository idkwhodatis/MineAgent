"""Atomic immutable two-upload composition under existing publication admission."""

import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid5

from sqlalchemy import select, text

from backend.app.core.atomic_storage import publish_bytes
from backend.app.core.maintenance import _lifecycle
from backend.app.declared_upload_io import MAX_BYTES, MAX_FILES, read_dataset_bytes
from backend.app.declared_upload_schemas import (
    PROFILE,
    REVISION,
    DeclaredUploadBasketCreate,
)
from backend.app.factor_services import PublicationAdmission, check_deadline, try_lock
from backend.app.models import DatasetVersion
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest

PUBLICATION_PEAK_BYTES = 2 * MAX_BYTES
PUBLICATION_PEAK_FILES = 2 * (2 * MAX_FILES + 8)


def fail():
    raise DomainError(
        "declared_upload_unavailable",
        "Exact owned declared upload sources are unavailable or incompatible",
        409,
    )


def require_revision(session):
    if session.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one_or_none() not in {
        REVISION,
        "031_workflow_structured_stage",
        "032_normal_oe_note_pins",
        "032a_rd_terminal_results",
        "032b_oe_append_run_state",
    }:
        raise DomainError(
            "declared_upload_schema_unavailable",
            "Declared upload composition is unavailable on this schema",
            409,
        )


def publish_basket(session, owner_id, command, settings, *, admission):
    from backend.app.declared_upload_models import (
        DeclaredUploadBasket,
        DeclaredUploadBasketMember,
    )
    from packages.rdagent_adapter.declared_upload import (
        UploadSource,
        build_declared_basket,
        validate_declared_basket,
    )

    command = DeclaredUploadBasketCreate.model_validate_json(canonical_bytes(command))
    if (
        type(owner_id) is not UUID
        or type(admission) is not PublicationAdmission
        or admission.include_dataset_inventory is not True
        or _lifecycle.get()
        != (session.get_bind(), Path(settings.storage_root).absolute())
    ):
        fail()
    require_revision(session)
    check_deadline(admission)
    try_lock(session, f"submit:{owner_id}")
    request_hash = digest(command)
    previous = session.scalar(
        select(DeclaredUploadBasket).filter_by(
            owner_id=owner_id, idempotency_key=command.idempotency_key
        )
    )
    if previous is not None and previous.request_sha256 != request_hash:
        raise DomainError(
            "idempotency_conflict",
            "Creation key already binds a different upload selection",
            409,
        )
    sources = []
    try:
        for identity in command.source_dataset_ids:
            row = session.scalar(
                select(DatasetVersion)
                .filter_by(id=identity, owner_id=owner_id)
                .with_for_update()
            )
            if (
                row is None
                or row.source != "upload"
                or row.is_synthetic is not False
                or row.market != "US"
                or row.storage_key != f"datasets/{owner_id}/{identity}"
            ):
                fail()
            from backend.app.models import OwnerListing

            listings = row.manifest.get("listings")
            if type(listings) is not list or len(listings) != 1:
                fail()
            listing_id = UUID(listings[0]["listing_id"])
            if session.get(OwnerListing, (owner_id, listing_id)) is None:
                fail()
            raw, artifacts = read_dataset_bytes(settings.storage_root, row)
            sources.append(
                UploadSource(
                    dataset_id=row.id,
                    owner_id=owner_id,
                    manifest_sha256=row.content_sha256,
                    manifest_bytes=raw,
                    artifacts=artifacts,
                )
            )
            check_deadline(admission)
        identity = uuid5(owner_id, "declared-upload-basket:" + command.idempotency_key)
        built = build_declared_basket(
            owner_id=owner_id, dataset_id=identity, sources=tuple(sources)
        )
        files = dict(built.files)
        files.setdefault("manifest.json", built.manifest_bytes)
        if sum(map(len, files.values())) > MAX_BYTES or len(files) > 2 * MAX_FILES + 8:
            fail()
        manifest = json.loads(built.manifest_bytes)
        storage_key = f"datasets/{owner_id}/{identity}"
        row = session.get(DatasetVersion, identity)
        if previous is not None:
            if (
                row is None
                or previous.dataset_id != identity
                or previous.manifest_sha256 != built.sha256
                or row.content_sha256 != built.sha256
                or row.manifest != manifest
            ):
                fail()
            verify_basket_relations(session, owner_id, row)
            raw, artifacts = read_dataset_bytes(settings.storage_root, row)
            validate_declared_basket(
                raw, artifacts, dataset_id=identity, owner_id=owner_id
            )
            return row
        if row is not None or os.path.lexists(
            Path(settings.storage_root) / storage_key
        ):
            fail()  # Never adopt/repair bytes from an uncertain old publication.
        for name, raw in sorted(files.items()):
            check_deadline(admission)
            publish_bytes(
                Path(settings.storage_root) / storage_key / name,
                raw,
                sha256=hashlib.sha256(raw).hexdigest(),
                durable_root=settings.storage_root,
            )
        from datetime import date

        row = DatasetVersion(
            id=identity,
            owner_id=owner_id,
            name="Declared upload research basket (unverified)",
            market="US",
            start_date=date.fromisoformat(manifest["start"]),
            end_date=date.fromisoformat(manifest["end"]),
            source="declared_upload_basket",
            is_synthetic=False,
            content_sha256=built.sha256,
            manifest=manifest,
            storage_key=storage_key,
        )
        session.add(row)
        session.flush()
        header = DeclaredUploadBasket(
            id=uuid5(identity, "publication"),
            owner_id=owner_id,
            idempotency_key=command.idempotency_key,
            request_sha256=request_hash,
            dataset_id=identity,
            manifest_sha256=built.sha256,
            profile=PROFILE,
        )
        session.add(header)
        session.flush()
        for ordinal, source in enumerate(sources):
            session.add(
                DeclaredUploadBasketMember(
                    basket_id=header.id,
                    owner_id=owner_id,
                    ordinal=ordinal,
                    source_dataset_id=source.dataset_id,
                    source_manifest_sha256=source.manifest_sha256,
                )
            )
        session.flush()
        check_deadline(admission)
        return row
    except (OSError, ValueError, TypeError, KeyError):
        fail()


def verify_basket_relations(session, owner_id, row):
    """Recheck actual retained ownership/lineage, not just a JSON profile label."""
    from backend.app.declared_upload_models import (
        DeclaredUploadBasket,
        DeclaredUploadBasketMember,
    )

    if (
        row.owner_id != owner_id
        or row.source != "declared_upload_basket"
        or row.is_synthetic is not False
        or row.storage_key != f"datasets/{owner_id}/{row.id}"
    ):
        fail()
    header = session.scalar(
        select(DeclaredUploadBasket).filter_by(owner_id=owner_id, dataset_id=row.id)
    )
    if (
        header is None
        or header.profile != PROFILE
        or header.manifest_sha256 != row.content_sha256
    ):
        fail()
    members = list(
        session.scalars(
            select(DeclaredUploadBasketMember)
            .filter_by(basket_id=header.id)
            .order_by(DeclaredUploadBasketMember.ordinal)
        )
    )
    if len(members) != 2:
        fail()
    refs = row.manifest.get("sources")
    if type(refs) is not list or len(refs) != 2:
        fail()
    for ordinal, (member, ref) in enumerate(zip(members, refs, strict=True)):
        source = session.scalar(
            select(DatasetVersion).filter_by(
                id=member.source_dataset_id, owner_id=owner_id
            )
        )
        if (
            member.owner_id != owner_id
            or member.ordinal != ordinal
            or source is None
            or source.content_sha256 != member.source_manifest_sha256
            or source.source != "upload"
            or source.is_synthetic is not False
            or (source.start_date, source.end_date) != (row.start_date, row.end_date)
            or ref.get("dataset_version_id") != str(source.id)
            or ref.get("manifest_sha256") != source.content_sha256
        ):
            fail()
    return header

"""Real host publication and PostgreSQL transactions; artificial declared CSV data.

Requires the PostgreSQL integration gate. No SQLite/session-double fallback.
"""

import json
from datetime import date
from uuid import UUID, uuid5

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.maintenance import shared_lifecycle
from backend.app.data_services import persist_listings
from backend.app.declared_upload_models import (
    DeclaredUploadBasket,
    DeclaredUploadBasketMember,
)
from backend.app.declared_upload_schemas import DeclaredUploadBasketCreate
from backend.app.declared_upload_services import (
    PUBLICATION_PEAK_BYTES,
    PUBLICATION_PEAK_FILES,
    publish_basket,
)
from backend.app.factor_services import publication
from backend.app.models import DatasetVersion, User
from backend.app.services import DomainError
from packages.marketdata.contracts import Listing
from tests.rdagent.test_declared_upload_source import OWNER, source

pytestmark = pytest.mark.postgres


@pytest.fixture
def uploaded_sources(migrated_engine, settings):
    sources = (source(), source(1))
    with Session(migrated_engine) as session:
        session.add(
            User(
                id=OWNER,
                email="declared-upload@example.test",
                hashed_password="not-a-login",
            )
        )
        session.commit()
        for item in sources:
            manifest = json.loads(item.manifest_bytes)
            persist_listings(
                session, OWNER, [Listing.model_validate(manifest["listings"][0])]
            )
            directory = settings.storage_root / f"datasets/{OWNER}/{item.dataset_id}"
            directory.mkdir(parents=True)
            for name, raw in {
                "manifest.json": item.manifest_bytes,
                **item.artifacts,
            }.items():
                path = directory / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            session.add(
                DatasetVersion(
                    id=item.dataset_id,
                    owner_id=OWNER,
                    name="Artificial declared CSV",
                    market="US",
                    start_date=date.fromisoformat(manifest["request"]["start"]),
                    end_date=date.fromisoformat(manifest["request"]["end"]),
                    source="upload",
                    is_synthetic=False,
                    content_sha256=item.manifest_sha256,
                    manifest=manifest,
                    storage_key=f"datasets/{OWNER}/{item.dataset_id}",
                )
            )
        session.commit()
    return sources


def publish(engine, settings, command, *, owner=OWNER, abort=False):
    with (
        Session(engine) as session,
        shared_lifecycle(engine, settings.storage_root),
        publication(
            session,
            owner,
            settings,
            peak_bytes=PUBLICATION_PEAK_BYTES,
            peak_files=PUBLICATION_PEAK_FILES,
            include_dataset_inventory=True,
        ) as admission,
    ):
        row = publish_basket(session, owner, command, settings, admission=admission)
        identity = row.id
        if abort:
            raise RuntimeError("deliberate test transaction rollback")
    return identity


def test_actual_upload_basket_publication_idempotency_rollback_and_owner(
    migrated_engine, settings, uploaded_sources
):
    command = DeclaredUploadBasketCreate(
        source_dataset_ids=tuple(
            sorted((x.dataset_id for x in uploaded_sources), key=str)
        ),
        acknowledge_unverified=True,
        idempotency_key="first",
    )
    identity = publish(migrated_engine, settings, command)
    assert publish(migrated_engine, settings, command) == identity
    with Session(migrated_engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(DeclaredUploadBasket)) == 1
        )
        assert (
            session.scalar(select(func.count()).select_from(DeclaredUploadBasketMember))
            == 2
        )
        assert session.get(DatasetVersion, identity).is_synthetic is False
    # A new key's failed transaction must not retain a partially published DB graph.
    rollback = command.model_copy(update={"idempotency_key": "rollback"})
    with pytest.raises(RuntimeError, match="deliberate test"):
        publish(migrated_engine, settings, rollback, abort=True)
    expected = uuid5(OWNER, "declared-upload-basket:rollback")
    with Session(migrated_engine) as session:
        assert session.get(DatasetVersion, expected) is None
        assert (
            session.scalar(select(func.count()).select_from(DeclaredUploadBasket)) == 1
        )
    # Published files outlive a DB rollback. Do not silently adopt orphan bytes.
    directory = settings.storage_root / f"datasets/{OWNER}/{expected}"
    original = {
        str(p.relative_to(directory)): p.read_bytes()
        for p in directory.rglob("*")
        if p.is_file()
    }
    assert original
    with pytest.raises(DomainError) as uncertain:
        publish(migrated_engine, settings, rollback)
    assert uncertain.value.code == "declared_upload_unavailable"
    assert {
        str(p.relative_to(directory)): p.read_bytes()
        for p in directory.rglob("*")
        if p.is_file()
    } == original
    with Session(migrated_engine) as session:
        assert session.get(DatasetVersion, expected) is None
        assert (
            session.scalar(select(func.count()).select_from(DeclaredUploadBasket)) == 1
        )
    # Same key cannot be reused for another selection, even a nonexistent source.
    drift = command.model_copy(
        update={"source_dataset_ids": (command.source_dataset_ids[0], UUID(int=999999))}
    )
    with pytest.raises(DomainError) as error:
        publish(migrated_engine, settings, drift)
    assert error.value.code == "idempotency_conflict"
    other = UUID(int=999998)
    with Session(migrated_engine) as session:
        session.add(
            User(
                id=other,
                email="other-upload@example.test",
                hashed_password="not-a-login",
            )
        )
        session.commit()
    with pytest.raises(DomainError):
        publish(migrated_engine, settings, command, owner=other)
    # Both the committed basket and the rolled-back immutable orphan retain quota.
    import os
    import time

    from backend.app.declared_upload_capacity import dataset_physical_usage

    fd = os.open(settings.storage_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        _, retained, _ = dataset_physical_usage(fd, OWNER, time.monotonic() + 5, 10000)
    finally:
        os.close(fd)
    assert retained > 1024
    limited = settings.model_copy(
        update={"factor_owner_max_bytes": retained + PUBLICATION_PEAK_BYTES - 1}
    )
    another = command.model_copy(update={"idempotency_key": "quota-rejected"})
    with pytest.raises(DomainError) as quota:
        publish(migrated_engine, limited, another)
    assert quota.value.code == "factor_capacity"
    assert not (
        settings.storage_root
        / f"datasets/{OWNER}/{uuid5(OWNER, 'declared-upload-basket:quota-rejected')}"
    ).exists()
    # No explicit new flag: the ordinary factor publisher must also count them.
    limited = settings.model_copy(update={"factor_owner_max_bytes": retained - 1})
    with (
        pytest.raises(DomainError) as ordinary,
        Session(migrated_engine) as session,
        shared_lifecycle(migrated_engine, settings.storage_root),
        publication(session, OWNER, limited, peak_bytes=1),
    ):
        raise AssertionError("Ordinary publisher ignored retained dataset capacity")
    assert ordinary.value.code == "factor_capacity"

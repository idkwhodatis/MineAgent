"""Owned demo dataset publication inside an explicit caller-held transaction.

Caller must hold shared_lifecycle and factor_services.publication with the exact
bounded reservation below. This function never commits, rolls back, deletes,
repairs existing bytes, enables an engine, or creates research/results. The caller
may acknowledge only after its publication context successfully commits.
"""

import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

from backend.app.core.atomic_storage import _open_directory, publish_bytes
from backend.app.core.maintenance import _lifecycle
from backend.app.factor_services import PublicationAdmission, check_deadline, try_lock
from backend.app.models import DatasetVersion, User
from backend.app.rdagent_configured_source import resolve_dataset_preflight
from backend.app.services import DomainError
from backend.app.storage import read_verified
from packages.rdagent_adapter.configured_source import ConfiguredSourcePolicy
from packages.structured_factor_strategy.demo import (
    END,
    SOURCE,
    START,
    dataset_id,
    encoded_demo_market,
)

PUBLICATION_PEAK_BYTES = 2 * 5 * 65536
PUBLICATION_PEAK_FILES = 9


def _fail(message):
    raise DomainError("structured_demo_unavailable", message, 409)


def publish_demo_dataset(session, owner_id, settings, *, admission):
    if type(owner_id) is not UUID or type(admission) is not PublicationAdmission:
        _fail("Exact owner and retained publication admission required")
    root = Path(settings.storage_root).absolute()
    if _lifecycle.get() != (session.get_bind(), root):
        _fail("Caller-held database and storage maintenance lifecycle required")
    for fd, path in (
        (admission.root_fd, root),
        (admission.factor_fd, root / "factors"),
    ):
        retained, named = os.fstat(fd), os.stat(path, follow_symlinks=False)
        if (retained.st_dev, retained.st_ino) != (named.st_dev, named.st_ino):
            _fail("Publication namespace changed")
    check_deadline(admission)
    if not session.is_active or session.connection().invalidated:
        _fail("Publication transaction is unavailable")
    user = session.get(User, owner_id)
    if user is None or user.is_active is not True:
        raise DomainError("unauthorized", "Invalid or inactive user", 401)
    try_lock(session, f"submit:{owner_id}")
    market = encoded_demo_market()
    if (
        market.peak_bytes > PUBLICATION_PEAK_BYTES
        or market.peak_files > PUBLICATION_PEAK_FILES
    ):
        _fail("Demo publication exceeds its fixed reservation")
    identity = dataset_id(owner_id)
    key = f"datasets/{owner_id}/{identity}"
    row = session.get(DatasetVersion, identity)
    manifest = json.loads(market.manifest_bytes)
    expected = {
        "owner_id": owner_id,
        "source": SOURCE,
        "is_synthetic": True,
        "market": "US",
        "start_date": START,
        "end_date": END,
        "storage_key": key,
        "content_sha256": market.sha256,
        "manifest": manifest,
    }
    if row is not None:
        if any(getattr(row, field) != value for field, value in expected.items()):
            _fail("Existing demo dataset identity differs")
        for name, raw in market.files:
            check_deadline(admission)
            if (
                read_verified(
                    root, f"{key}/{name}", hashlib.sha256(raw).hexdigest(), len(raw)
                )
                != raw
            ):
                _fail("Retained demo bytes differ")
    else:
        # Any pre-existing reserved tree without its row is an uncertain previous
        # publication. Do not adopt it, replace it, or fill its missing files.
        if os.path.lexists(root / key):
            _fail("Unreferenced demo dataset requires explicit recovery")
        for name, raw in market.files:
            check_deadline(admission)
            publish_bytes(
                root / key / name,
                raw,
                sha256=hashlib.sha256(raw).hexdigest(),
                durable_root=root,
            )
        row = DatasetVersion(
            id=identity,
            name="Tiny synthetic factor-search demo (not market evidence)",
            **expected,
        )
        session.add(row)
        session.flush()
    from backend.app.structured_demo_storage import verify_demo_directory

    directory = _open_directory(root / key)
    try:
        verify_demo_directory(directory, os.listdir(directory))
    finally:
        os.close(directory)
    # Actual owner-filtered retained reader, not a detached dataset declaration.
    resolve_dataset_preflight(
        session,
        owner_id=owner_id,
        dataset_id=identity,
        policy=ConfiguredSourcePolicy(acknowledged_unverified=True),
        input_names=("close", "volume"),
        settings=settings,
    )
    check_deadline(admission)
    return row

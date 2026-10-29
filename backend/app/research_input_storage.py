"""Closed typed input objects for the existing factor publisher/quota integration.

This namespace is not active until a future capability and every central quota
caller explicitly includes it. No registration as legacy FactorObject is allowed.
"""

import hashlib
import os
import re
import stat
import time
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select, text

from backend.app.core.atomic_storage import _open_directory, publish_bytes
from backend.app.core.maintenance import _lifecycle
from backend.app.factor_services import PublicationAdmission, check_deadline
from backend.app.models import User
from backend.app.research_input_models import ResearchInputObject
from backend.app.schema_capabilities import capabilities_for
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes
from packages.research_inputs.contracts import NormalizedExternalProposal
from packages.research_inputs.feedback import ExperimentFeedbackProposal

INPUT_SCHEMA = "mineagent.research-inputs.v1"
OBJECT_LIMITS = {
    "raw_external": 65536,
    "normalized_external": 32768,
    "normalized_feedback": 32768,
}


def unavailable():
    raise DomainError(
        "research_input_storage_unavailable",
        "Typed input storage is unavailable or changed",
        409,
    )


def require_input_capability(session):
    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    try:
        enabled = (
            len(revisions) == 1
            and getattr(capabilities_for(revisions[0]), "research_input_schema", None)
            == INPUT_SCHEMA
        )
    except ValueError:
        enabled = False
    if not enabled:
        unavailable()


def verify_payload(kind, content):
    if (
        kind not in OBJECT_LIMITS
        or type(content) is not bytes
        or len(content) > OBJECT_LIMITS[kind]
    ):
        raise ValueError("Bounded typed research input bytes required")
    if kind == "normalized_external":
        value = NormalizedExternalProposal.model_validate_json(content)
        if canonical_bytes(value) != content:
            raise ValueError("Exact normalized proposal canonical bytes required")
    if kind == "normalized_feedback":
        value = ExperimentFeedbackProposal.model_validate_json(content)
        if canonical_bytes(value) != content:
            raise ValueError("Exact feedback proposal canonical bytes required")
    return hashlib.sha256(content).hexdigest()


def _read(directory, name, maximum, deadline):
    descriptor = os.open(
        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 <= before.st_size <= maximum
        ):
            raise ValueError("Bounded singleton input object required")
        content = bytearray()
        while chunk := os.read(descriptor, min(65536, maximum + 1 - len(content))):
            if time.monotonic() >= deadline:
                raise ValueError("Input object read deadline")
            content.extend(chunk)
            if len(content) > maximum:
                raise ValueError("Input object byte limit")

        def identity(info):
            return (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_nlink,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )

        if (
            identity(before) != identity(os.fstat(descriptor))
            or identity(before)
            != identity(os.stat(name, dir_fd=directory, follow_symlinks=False))
            or len(content) != before.st_size
            or time.monotonic() >= deadline
        ):
            raise ValueError("Input object changed")
        return bytes(content)
    finally:
        os.close(descriptor)


def read_input_object(root, row, owner_id, *, deadline=None):
    """Owner-checked actual byte read; callers separately authorize their operation."""
    if (
        type(owner_id) is not UUID
        or row.owner_id != owner_id
        or row.kind not in OBJECT_LIMITS
    ):
        unavailable()
    extension = {
        "raw_external": "raw",
        "normalized_external": "json",
        "normalized_feedback": "feedback.json",
    }[row.kind]
    key = f"factors/{owner_id}/input-objects/{row.sha256}.{extension}"
    if row.storage_key != key or re.fullmatch(r"[a-f0-9]{64}", row.sha256) is None:
        unavailable()
    directory = _open_directory(
        Path(root).absolute() / f"factors/{owner_id}/input-objects"
    )
    try:
        content = _read(
            directory,
            f"{row.sha256}.{extension}",
            OBJECT_LIMITS[row.kind],
            time.monotonic() + 10 if deadline is None else deadline,
        )
        if (
            len(content) != row.size_bytes
            or verify_payload(row.kind, content) != row.sha256
        ):
            unavailable()
        return content
    finally:
        os.close(directory)


def require_publication_admission(session, settings, admission):
    """Verify the existing in-process quota/lifecycle guard, never a user flag."""
    if type(admission) is not PublicationAdmission:
        unavailable()
    root = Path(settings.storage_root).absolute()
    if _lifecycle.get() != (session.get_bind(), root) or not session.is_active:
        unavailable()
    for fd, path in (
        (admission.root_fd, root),
        (admission.factor_fd, root / "factors"),
    ):
        before, current = os.fstat(fd), os.stat(path, follow_symlinks=False)
        if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
            unavailable()
    check_deadline(admission)
    return root


def reject_unregistered_target(root, key):
    target = Path(root) / key
    try:
        directory = _open_directory(target.parent)
    except FileNotFoundError:
        return
    try:
        try:
            os.stat(target.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return
        unavailable()
    finally:
        os.close(directory)


def publish_input_object(session, owner_id, kind, content, settings, *, admission):
    """Flush only inside caller's existing factor publication/lifecycle context.

    Raw malformed/empty JSON remains raw evidence. Only normalization validates it.
    Activation must also wire the central inventory to charge this new namespace.
    """
    require_input_capability(session)
    if type(owner_id) is not UUID:
        unavailable()
    root = require_publication_admission(session, settings, admission)
    user = session.get(User, owner_id)
    if user is None or user.is_active is not True:
        raise DomainError("unauthorized", "Inactive owner", 401)
    check_deadline(admission)
    sha = verify_payload(kind, content)
    extension = {
        "raw_external": "raw",
        "normalized_external": "json",
        "normalized_feedback": "feedback.json",
    }[kind]
    key = f"factors/{owner_id}/input-objects/{sha}.{extension}"
    row = session.scalar(
        select(ResearchInputObject).filter_by(owner_id=owner_id, storage_key=key)
    )
    if row is not None:
        if (row.kind, row.sha256, row.size_bytes) != (kind, sha, len(content)):
            unavailable()
        read_input_object(root, row, owner_id, deadline=float(admission))
        return row
    # A prior failed publication may have left immutable bytes but no DB row.
    # Preserve that uncertain outcome for recovery; ordinary intake must not
    # silently manufacture provenance merely because the bytes happen to match.
    reject_unregistered_target(root, key)
    publish_bytes(root / key, content, sha256=sha, durable_root=root)
    row = ResearchInputObject(
        id=uuid4(),
        owner_id=owner_id,
        kind=kind,
        storage_key=key,
        sha256=sha,
        size_bytes=len(content),
    )
    session.add(row)
    session.flush()
    read_input_object(root, row, owner_id, deadline=float(admission))
    check_deadline(admission)
    return row


def input_object_inventory(directory, *, deadline, maximum_files):
    """Central scanner calls only for an enabled, descriptor-pinned namespace.

    Counts every physical object including unreferenced bytes. Unknown/temporary
    entries require recovery; this routine never repairs or deletes them.
    """
    before = os.fstat(directory)

    def bounded_names():
        result = []
        with os.scandir(directory) as entries:
            for entry in entries:
                if time.monotonic() >= deadline or len(result) >= maximum_files:
                    raise ValueError("Input object file/deadline budget")
                result.append(entry.name)
        return result

    names = bounded_names()
    total = 0
    for name in names:
        match = re.fullmatch(r"([a-f0-9]{64})\.(raw|json|feedback\.json)", name)
        if match is None:
            raise ValueError("Unknown input object requires recovery")
        kind = {
            "raw": "raw_external",
            "json": "normalized_external",
            "feedback.json": "normalized_feedback",
        }[match[2]]
        content = _read(directory, name, OBJECT_LIMITS[kind], deadline)
        if verify_payload(kind, content) != match[1]:
            raise ValueError("Input object content address differs")
        total += len(content)
    after = os.fstat(directory)
    if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ) or set(bounded_names()) != set(names):
        raise ValueError("Input object namespace changed")
    return total, len(names)

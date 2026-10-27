"""DB-only steer CRUD; callers commit. Never edits a run or invokes a model.

Session's existing maintenance hook acquires the global shared DB barrier first;
owner advisory lock precedes steer row locks. No filesystem bytes are mutated.
"""

from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import func, select, text

from backend.app.models import utcnow
from backend.app.rdagent_steer_models import RDSteer, RDSteerVersion
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.schema_capabilities import capabilities_for
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.steer import SteerSnapshot, SteerVersion

SCHEMA = "mineagent.rd-steer-closure.v1"


def require_schema(session):
    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    try:
        enabled = (
            len(revisions) == 1
            and getattr(capabilities_for(revisions[0]), "rd_steer_schema", None)
            == SCHEMA
        )
    except ValueError:
        enabled = False
    if not enabled:
        raise DomainError(
            "steer_schema_unavailable", "Persistent steer schema is unavailable", 409
        )


def decode_version(row, owner_id):
    if row is None or row.owner_id != owner_id:
        raise DomainError("not_found", "Steer version not found", 404)
    try:
        value = SteerVersion.model_validate_json(row.content_canonical)
        if (
            (
                value.steer_id,
                value.version_id,
                value.ordinal,
                value.kind,
                value.text,
                value.text_sha256,
            )
            != (row.steer_id, row.id, row.ordinal, row.kind, row.text, row.text_sha256)
            or value.sha256 != row.content_sha256
            or canonical_bytes(value).decode() != row.content_canonical
        ):
            raise ValueError("steer commitment differs")
        return value
    except (ValueError, TypeError, AttributeError):
        raise DomainError(
            "steer_version_inconsistent", "Immutable steer commitment differs", 409
        ) from None


def resolve_steer_snapshot(session, owner_id, version_ids):
    """NEW acceptance only. Ordered explicit active/current versions; no inference."""
    require_schema(session)
    if (
        len(version_ids) > 8
        or len(set(version_ids)) != len(version_ids)
        or any(not isinstance(value, UUID) for value in version_ids)
    ):
        raise DomainError(
            "invalid_steer_selection", "Choose at most eight distinct version IDs", 422
        )
    _try_neutral_lock(session, f"submit:{owner_id}")
    versions = []
    for version_id in version_ids:
        row = session.get(RDSteerVersion, version_id)
        value = decode_version(row, owner_id)
        root = session.get(RDSteer, value.steer_id)
        if root is None or root.owner_id != owner_id:
            raise DomainError("not_found", "Steer not found", 404)
        if root.disabled or root.current_version_id != version_id:
            raise DomainError(
                "steer_selection_changed",
                "Select the currently active steer version and preflight again",
                409,
            )
        versions.append(value)
    try:
        return SteerSnapshot(owner_id=owner_id, versions=tuple(versions))
    except ValueError:
        raise DomainError(
            "invalid_steer_selection", "Selected steer snapshot exceeds its bound", 422
        ) from None


def verify_steer_snapshot(session, owner_id, snapshot):
    """Accepted run/worker/replay/history only: current head/disabled NEVER read."""
    snapshot = SteerSnapshot.model_validate(snapshot)
    if snapshot.owner_id != owner_id:
        raise DomainError("not_found", "Steer snapshot not found", 404)
    for expected in snapshot.versions:
        actual = decode_version(
            session.get(RDSteerVersion, expected.version_id), owner_id
        )
        if actual != expected:
            raise DomainError(
                "steer_snapshot_changed",
                "Accepted steer snapshot differs from immutable version",
                409,
            )
    return snapshot


def owned_root(session, owner_id, steer_id, *, lock=False):
    statement = select(RDSteer).where(
        RDSteer.id == steer_id, RDSteer.owner_id == owner_id
    )
    if lock:
        statement = statement.with_for_update()
    row = session.scalar(statement)
    if row is None:
        raise DomainError("not_found", "Steer not found", 404)
    return row


def checked_revision(root, expected):
    if root.revision != expected:
        raise DomainError(
            "steer_edit_conflict", "Steer changed; reload before editing", 409
        )


def new_version(session, owner_id, steer_id, ordinal, body, now):
    try:
        value = SteerVersion(
            steer_id=steer_id,
            version_id=uuid4(),
            ordinal=ordinal,
            kind=body.kind,
            text=body.text,
            text_sha256=sha256(body.text.encode()).hexdigest(),
        )
    except ValueError:
        raise DomainError(
            "invalid_steer_text", "Bounded nonempty steer text required", 422
        ) from None
    row = RDSteerVersion(
        id=value.version_id,
        owner_id=owner_id,
        steer_id=steer_id,
        ordinal=ordinal,
        kind=value.kind,
        text=value.text,
        text_sha256=value.text_sha256,
        content_canonical=canonical_bytes(value).decode(),
        content_sha256=value.sha256,
        created_at=now,
    )
    session.add(row)
    return row


def create_steer(session, owner_id, body):
    require_schema(session)
    _try_neutral_lock(session, f"submit:{owner_id}")
    count = session.scalar(
        select(func.count()).select_from(RDSteer).where(RDSteer.owner_id == owner_id)
    )
    if count >= 100:
        raise DomainError(
            "steer_limit", "At most 100 retained steer histories per owner", 409
        )
    now = utcnow()
    steer_id = uuid4()
    version = new_version(session, owner_id, steer_id, 1, body, now)
    root = RDSteer(
        id=steer_id,
        owner_id=owner_id,
        current_version_id=version.id,
        disabled=False,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    session.add(root)
    session.flush()
    return root, version


def edit_steer(session, owner_id, steer_id, body):
    require_schema(session)
    _try_neutral_lock(session, f"submit:{owner_id}")
    root = owned_root(session, owner_id, steer_id, lock=True)
    checked_revision(root, body.expected_revision)
    previous = decode_version(
        session.get(RDSteerVersion, root.current_version_id), owner_id
    )
    if previous.ordinal >= 64:
        raise DomainError(
            "steer_version_limit", "At most 64 immutable versions per steer", 409
        )
    now = utcnow()
    version = new_version(session, owner_id, root.id, previous.ordinal + 1, body, now)
    root.current_version_id = version.id
    root.revision += 1
    root.updated_at = now
    session.flush()
    return root, version


def set_disabled(session, owner_id, steer_id, body):
    require_schema(session)
    _try_neutral_lock(session, f"submit:{owner_id}")
    root = owned_root(session, owner_id, steer_id, lock=True)
    checked_revision(root, body.expected_revision)
    if root.disabled != body.disabled:
        root.disabled = body.disabled
        root.revision += 1
        root.updated_at = utcnow()
        session.flush()
    return root, session.get(RDSteerVersion, root.current_version_id)

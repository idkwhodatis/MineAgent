"""Explicit owner-requested retained content; audit listings stay metadata-only.

HTTP must use attachments or escaped text/JSON with no-store/nosniff. This module
never renders HTML, logs content, puts it in a public artifact, or grants model
consumption. Raw malformed/non-UTF8 bytes remain an opaque bounded attachment.
"""

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from backend.app.core.maintenance import shared_lifecycle
from backend.app.models import User
from backend.app.research_input_models import (
    ResearchInputObject,
    ResearchInputRecord,
    ResearchInputVersion,
)
from backend.app.research_input_resolution import verify_model_input_versions
from backend.app.research_input_storage import (
    read_input_object,
    require_input_capability,
)
from backend.app.services import DomainError
from packages.research_inputs.contracts import InputVersionRef


@dataclass(frozen=True)
class RetainedInputContent:
    content: bytes
    sha256: str
    size_bytes: int
    media_type: str
    filename: str


def _check_request(owner_id, identity, expected_sha256):
    if (
        type(owner_id) is not UUID
        or type(identity) is not UUID
        or type(expected_sha256) is not str
        or re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None
    ):
        raise DomainError(
            "invalid_input_content_reference",
            "Exact input identity and SHA are required",
            422,
        )


def _owner(session, owner_id):
    require_input_capability(session)
    owner = session.get(User, owner_id)
    if owner is None or owner.is_active is not True:
        raise DomainError("unauthorized", "Inactive owner", 401)


def _missing():
    raise DomainError("not_found", "Owned retained input content not found", 404)


def _inconsistent():
    raise DomainError(
        "input_content_inconsistent", "Retained input commitment differs", 409
    )


def read_external_content(factory, settings, *, owner_id, object_id, expected_sha256):
    _check_request(owner_id, object_id, expected_sha256)
    try:
        with (
            shared_lifecycle(factory.kw["bind"], settings.storage_root),
            factory() as session,
        ):
            _owner(session, owner_id)
            row = session.scalar(
                select(ResearchInputObject).where(
                    ResearchInputObject.owner_id == owner_id,
                    ResearchInputObject.id == object_id,
                    ResearchInputObject.kind == "raw_external",
                )
            )
            if (
                row is None
                or row.owner_id != owner_id
                or row.id != object_id
                or row.kind != "raw_external"
            ):
                _missing()
            if row.sha256 != expected_sha256:
                _inconsistent()
            raw = read_input_object(settings.storage_root, row, owner_id)
            result = RetainedInputContent(
                content=raw,
                sha256=row.sha256,
                size_bytes=len(raw),
                media_type="application/octet-stream",
                filename=f"external-input-{row.id}.bin",
            )
        return result
    except DomainError:
        raise
    except (ValueError, TypeError, OSError, AttributeError):
        _inconsistent()


def read_normalized_content(
    factory, settings, *, owner_id, version_id, expected_sha256
):
    _check_request(owner_id, version_id, expected_sha256)
    try:
        with (
            shared_lifecycle(factory.kw["bind"], settings.storage_root),
            factory() as session,
        ):
            _owner(session, owner_id)
            version = session.scalar(
                select(ResearchInputVersion).where(
                    ResearchInputVersion.owner_id == owner_id,
                    ResearchInputVersion.id == version_id,
                )
            )
            if (
                version is None
                or version.owner_id != owner_id
                or version.id != version_id
            ):
                _missing()
            record = session.get(ResearchInputRecord, version.record_id)
            if record is None or record.owner_id != owner_id:
                _missing()
            # Feedback must use its future release-qualified two-phase reader;
            # this explicit normalized-external route never exposes cached feedback.
            if record.origin != "normalized_external_proposal":
                raise DomainError(
                    "input_content_reader_unavailable",
                    "Use the exact source-qualified content reader",
                    409,
                )
            if version.content_sha256 != expected_sha256:
                _inconsistent()
            ref = InputVersionRef(
                origin=record.origin,
                record_id=record.id,
                version_id=version.id,
                content_sha256=version.content_sha256,
            )
            verify_model_input_versions(session, owner_id, (ref,), settings)
            obj = session.get(ResearchInputObject, version.object_id)
            if (
                obj is None
                or obj.kind != "normalized_external"
                or obj.sha256 != version.content_sha256
            ):
                _inconsistent()
            content = read_input_object(settings.storage_root, obj, owner_id)
            if content.decode("utf-8") != version.content_canonical:
                _inconsistent()
            result = RetainedInputContent(
                content=content,
                sha256=obj.sha256,
                size_bytes=len(content),
                media_type="application/json",
                filename=f"normalized-input-{version.id}.json",
            )
        return result
    except DomainError:
        raise
    except (ValueError, TypeError, OSError, AttributeError):
        _inconsistent()

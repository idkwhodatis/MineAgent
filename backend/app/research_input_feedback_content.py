"""Explicit owned feedback download through fresh existing-release authority.

No release is created here. Cached text is never the authority, and the generic
normalized-external reader remains unchanged. HTTP exposes only passive bounded
bytes after immutable source/ledger reprojection under the actual native guard.
"""

from sqlalchemy import select

from backend.app.execution_snapshot_history import historical_session
from backend.app.factor_native_io import require_native_storage_guard
from backend.app.research_input_content import (
    RetainedInputContent,
    _check_request,
    _inconsistent,
    _missing,
    _owner,
)
from backend.app.research_input_feedback_resolution import resolve_feedback_version
from backend.app.research_input_models import (
    ResearchInputObject,
    ResearchInputRecord,
    ResearchInputVersion,
)
from backend.app.research_input_storage import read_input_object
from backend.app.services import DomainError
from packages.research_inputs.contracts import InputVersionRef


def read_feedback_content(factory, settings, *, owner_id, version_id, expected_sha256):
    """Read an exact historical output, never select it or grant new disclosure."""
    _check_request(owner_id, version_id, expected_sha256)
    try:
        with historical_session(factory, owner_id, settings) as session:
            require_native_storage_guard(settings.storage_root)
            _owner(session, owner_id)
            version = session.scalar(
                select(ResearchInputVersion).where(
                    ResearchInputVersion.owner_id == owner_id,
                    ResearchInputVersion.id == version_id,
                )
            )
            if version is None or (version.owner_id, version.id) != (
                owner_id,
                version_id,
            ):
                _missing()
            record = session.get(ResearchInputRecord, version.record_id)
            if record is None or (record.owner_id, record.id) != (
                owner_id,
                version.record_id,
            ):
                _missing()
            if record.origin != "experiment_feedback_proposal":
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
                content_sha256=expected_sha256,
            )
            # Existing exposures are checked BEFORE source values; actual source
            # bytes and exact event/version/output bindings are then revalidated.
            # Historical disable/current-selection changes do not rewrite output.
            resolved = resolve_feedback_version(
                session, owner_id, ref, settings, historical=True
            )
            if resolved.ref != ref:
                _inconsistent()
            obj = session.get(ResearchInputObject, version.object_id)
            if obj is None or (
                obj.owner_id,
                obj.id,
                obj.kind,
                obj.sha256,
            ) != (owner_id, version.object_id, "normalized_feedback", expected_sha256):
                _inconsistent()
            content = read_input_object(settings.storage_root, obj, owner_id)
            if (
                not 1 <= len(content) <= 32768
                or len(content) != obj.size_bytes
                or content.decode("utf-8") != version.content_canonical
            ):
                _inconsistent()
            require_native_storage_guard(settings.storage_root)
            result = RetainedInputContent(
                content=content,
                sha256=expected_sha256,
                size_bytes=len(content),
                media_type="application/json",
                filename=f"feedback-input-{version_id}.json",
            )
        return result
    except DomainError:
        raise
    except (ValueError, TypeError, OSError, AttributeError):
        _inconsistent()

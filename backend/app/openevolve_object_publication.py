"""Actual retained object publication with additive storage and fresh fences.

Each publication has its own immutable reservation/journal. Completed replay is
verified read-only before asking the publisher to use an already-settled hold.
The returned object is immutable data, never a detached publication authority.
"""

import hashlib
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from backend.app.openevolve_services import _current_search, _scope
from backend.app.research_broker_services import reserve_budget
from backend.app.research_lifecycle import neutral_transaction
from backend.app.research_object_models import (
    ResearchObject,
    ResearchObjectOutcome,
    ResearchObjectPublication,
)
from backend.app.research_object_services import (
    publish_reserved_object,
    read_research_object,
    reserve_object,
)
from packages.openevolve_adapter.object_identity import KINDS, parse_document
from packages.openevolve_adapter.persisted_identity import require_search_identity
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.object_identity import (
    MAX_JOURNAL_BYTES,
    canonical_object,
)


@dataclass(frozen=True)
class PublishedEvidence:
    id: UUID
    object_kind: str
    sha256: str


def _read(session, fence, identity, kind, content, settings):
    row = session.get(ResearchObject, identity)
    sha = hashlib.sha256(content).hexdigest()
    if (
        row is None
        or (row.owner_id, row.object_kind, row.sha256, row.size_bytes)
        != (fence.owner_id, kind, sha, len(content))
        or read_research_object(session, fence, row.id, kind, sha, settings) != content
    ):
        raise ValueError("retained search publication differs")
    return PublishedEvidence(row.id, kind, sha)


def publish_search_object(factory, fence, *, kind, content, logical_key, settings):
    if kind in KINDS:
        parse_document(kind, content)
    elif kind in {"model_request", "model_response"}:
        canonical_object(content)
    else:
        raise ValueError("closed operational search object")
    sha = hashlib.sha256(content).hexdigest()
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        require_search_identity(_current_search(session, fence))
        reservation = reserve_budget(
            session,
            fence,
            purpose="object_storage",
            logical_key=logical_key,
            amounts=BudgetVector(
                storage_bytes=2 * len(content) + 2 * MAX_JOURNAL_BYTES
            ),
        )
        previous = session.scalar(
            select(ResearchObjectPublication).filter_by(
                budget_reservation_id=reservation.id
            )
        )
        if previous is not None:
            if any(getattr(previous, k) != v for k, v in _scope(fence).items()) or (
                previous.object_kind,
                previous.sha256,
                previous.size_bytes,
            ) != (kind, sha, len(content)):
                raise ValueError("publication replay drift")
            outcome = session.get(ResearchObjectOutcome, previous.id)
            if outcome is not None:
                return _read(session, fence, outcome.object_id, kind, content, settings)
        publication = reserve_object(
            session, fence, kind, content, reservation.id, settings
        )
        publication_id = publication.id
    identity = publish_reserved_object(
        factory, fence, publication_id, content, settings
    )
    # Shared publisher returns UUID, not ORM object. Resolve exact retained bytes
    # once more under a fresh fence rather than leaking a detached mutable row.
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        return _read(session, fence, identity, kind, content, settings)

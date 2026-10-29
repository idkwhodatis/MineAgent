"""Fresh scoped SDK evidence reads; receipt DTOs alone never grant authority."""

from uuid import uuid4

from sqlalchemy import select

from backend.app.rdagent_provider_models import RDProviderTransportReceipt
from backend.app.rdagent_services import _scope, _scope_row, require_research_spec
from backend.app.research_broker_models import (
    ResearchModelOperation,
    ResearchModelTransport,
)
from backend.app.research_object_services import read_research_object
from packages.rdagent_provider.codec import PROTOCOLS
from packages.rdagent_provider.process_receipts import (
    OBJECT_KIND,
    parse_receipt,
    receipt_bytes,
    validate_chain,
)


def _transport(session, fence, transport_id):
    _, config = require_research_spec(session, fence)
    transport = _scope_row(session, ResearchModelTransport, transport_id, fence)
    operation = _scope_row(
        session, ResearchModelOperation, transport.operation_id, fence
    )
    if (
        config.schema_version
        not in ("mineagent.factor-research.v2", "mineagent.factor-research.v3")
        or operation.protocol not in PROTOCOLS
    ):
        raise ValueError("Exact configured provider transport required")
    return config, transport, operation


def bind_receipt(session, fence, *, transport_id, object_id, sha256, settings):
    config, transport, operation = _transport(session, fence, transport_id)
    raw = read_research_object(session, fence, object_id, OBJECT_KIND, sha256, settings)
    document = parse_receipt(raw)
    if any(getattr(document, k) != v for k, v in _scope(fence).items()) or (
        document.transport_id != transport.id
        or document.request_sha256 != operation.request_sha256
        or document.model_binding_sha256 != config.model_binding.sha256
    ):
        raise ValueError("SDK receipt differs from actual persisted transport")
    existing = session.scalar(
        select(RDProviderTransportReceipt).filter_by(
            transport_id=transport.id, kind=document.kind
        )
    )
    values = dict(
        **_scope(fence),
        transport_id=transport.id,
        kind=document.kind,
        receipt_object_id=object_id,
        receipt_object_kind=OBJECT_KIND,
        receipt_sha256=sha256,
        size_bytes=len(raw),
    )
    if existing is not None:
        if any(getattr(existing, k) != v for k, v in values.items()):
            raise ValueError("Immutable SDK receipt replay drift")
        return existing
    if transport.state != "submitted":
        raise ValueError("New SDK evidence requires submitted transport")
    previous = session.scalars(
        select(RDProviderTransportReceipt).filter_by(transport_id=transport.id)
    ).all()
    expected = {
        "intent": set(),
        "started": {"intent"},
        "stopped": {"intent", "started"},
    }
    if {row.kind for row in previous} != expected[document.kind]:
        raise ValueError("SDK evidence publication order differs")
    row = RDProviderTransportReceipt(id=uuid4(), **values)
    session.add(row)
    session.flush()
    return row


def read_chain(session, fence, transport_id, settings, *, completed):
    config, transport, operation = _transport(session, fence, transport_id)
    rows = session.scalars(
        select(RDProviderTransportReceipt).filter_by(transport_id=transport.id)
    ).all()
    if len(rows) != 3 or {row.kind for row in rows} != {"intent", "started", "stopped"}:
        raise ValueError("Complete actual SDK physical chain required")
    documents = {}
    for row in rows:
        if (
            any(getattr(row, k) != v for k, v in _scope(fence).items())
            or row.receipt_object_kind != OBJECT_KIND
        ):
            raise ValueError("SDK evidence scope drift")
        raw = read_research_object(
            session,
            fence,
            row.receipt_object_id,
            OBJECT_KIND,
            row.receipt_sha256,
            settings,
        )
        document = parse_receipt(raw)
        if (
            len(raw) != row.size_bytes
            or document.kind != row.kind
            or document.transport_id != transport.id
            or document.request_sha256 != operation.request_sha256
            or document.model_binding_sha256 != config.model_binding.sha256
            or any(getattr(document, k) != v for k, v in _scope(fence).items())
        ):
            raise ValueError("SDK retained receipt binding drift")
        receipt_bytes(document)
        documents[row.kind] = document
    if (
        documents["intent"].maximum_duration_ms
        != config.model_binding.request_deadline_ms
    ):
        raise ValueError("SDK deadline declaration differs from immutable model")
    validate_chain(
        documents["intent"],
        documents["started"],
        documents["stopped"],
        completed=completed,
    )
    return documents

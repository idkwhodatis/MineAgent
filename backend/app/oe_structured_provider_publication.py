"""Charged immutable OE request/response/SDK files; no model-call authority."""

import hashlib
from uuid import uuid4

from sqlalchemy import select, text

from backend.app.oe_structured_controller_context import fail, scoped
from backend.app.oe_structured_identity import decode_spec
from backend.app.oe_structured_operational_models import (
    OEStructuredProviderReceipt,
    OEStructuredProviderTransport,
)
from backend.app.research_broker_models import (
    ResearchModelOperation,
    ResearchModelTransport,
)
from backend.app.research_broker_services import reserve_budget
from backend.app.research_lifecycle import (
    neutral_transaction,
    require_neutral_transaction,
)
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
from packages.openevolve_structured.provider_receipts import (
    OBJECT_KIND,
    parse_receipt,
    receipt_bytes,
)
from packages.research_runtime.budget_contracts import BudgetVector

KINDS = {
    "oe_structured_provider_request",
    "oe_structured_provider_response",
    OBJECT_KIND,
}


def publish_evidence(factory, fence, settings, kind, content, key):
    if (
        kind not in KINDS
        or type(content) is not bytes
        or not 0 < len(content) <= 262144
    ):
        fail()
    sha = hashlib.sha256(content).hexdigest()
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        if session.scalars(
            text("SELECT version_num FROM alembic_version")
        ).all() not in (
            ["028_oe_structured_search"],
            ["029_research_workflow"],
            ["030_declared_upload_basket"],
            ["031_workflow_structured_stage"],
            ["032_normal_oe_note_pins"],
            ["032a_rd_terminal_results"],
            ["032b_oe_append_run_state"],
        ):
            fail()
        held = reserve_budget(
            session,
            fence,
            purpose="object_storage",
            logical_key=key,
            amounts=BudgetVector(storage_bytes=2 * len(content) + 8192),
            settings=settings,
        )
        previous = session.scalar(
            select(ResearchObjectPublication).filter_by(budget_reservation_id=held.id)
        )
        if previous is not None:
            scoped(previous, fence)
            if (previous.object_kind, previous.sha256, previous.size_bytes) != (
                kind,
                sha,
                len(content),
            ):
                fail()
            outcome = session.get(ResearchObjectOutcome, previous.id)
            if outcome is not None:
                obj = session.get(ResearchObject, outcome.object_id)
                if (
                    obj is None
                    or read_research_object(session, fence, obj.id, kind, sha, settings)
                    != content
                ):
                    fail()
                return obj.id
        publication = reserve_object(session, fence, kind, content, held.id, settings)
        publication_id = publication.id
    return publish_reserved_object(factory, fence, publication_id, content, settings)


def bind_physical_receipt(session, fence, membership_id, object_id, sha256, settings):
    run, _ = require_neutral_transaction(session, fence, resource_scope="objects")
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        ["028_oe_structured_search"],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
        ["032b_oe_append_run_state"],
    ):
        fail()
    _, config = decode_spec(session, run)
    member = scoped(session.get(OEStructuredProviderTransport, membership_id), fence)
    transport = scoped(session.get(ResearchModelTransport, member.transport_id), fence)
    operation = scoped(session.get(ResearchModelOperation, member.operation_id), fence)
    raw = read_research_object(session, fence, object_id, OBJECT_KIND, sha256, settings)
    document = parse_receipt(raw)
    scoped(document, fence)
    if (
        document.transport_id != transport.id
        or transport.operation_id != operation.id
        or document.request_sha256 != operation.request_sha256
        or document.request_sha256 != member.request_sha256
        or document.model_binding_sha256 != config.model_binding.sha256
        or member.model_binding_sha256 != config.model_binding.sha256
        or operation.protocol != "mineagent.oe-structured-provider-chat.v1"
    ):
        fail()
    rows = session.scalars(
        select(OEStructuredProviderReceipt).filter_by(membership_id=member.id)
    ).all()
    existing = [row for row in rows if row.kind == document.kind]
    scope = {
        k: getattr(fence, k) for k in ("owner_id", "run_id", "attempt_id", "generation")
    }
    expected = dict(
        **scope,
        membership_id=member.id,
        kind=document.kind,
        receipt_object_id=object_id,
        receipt_object_kind=OBJECT_KIND,
        receipt_sha256=sha256,
        size_bytes=len(raw),
    )
    if existing:
        if len(existing) != 1 or any(
            getattr(existing[0], k) != v for k, v in expected.items()
        ):
            fail()
        return existing[0]
    before = {"intent": set(), "started": {"intent"}, "stopped": {"intent", "started"}}
    if (
        transport.state != "submitted"
        or {row.kind for row in rows} != before[document.kind]
        or len(rows) != len(before[document.kind])
    ):
        fail()
    result = OEStructuredProviderReceipt(id=uuid4(), **expected)
    session.add(result)
    session.flush()
    return result


def retain_physical_receipt(factory, fence, settings, membership_id, document, key):
    raw = receipt_bytes(document)
    oid = publish_evidence(
        factory, fence, settings, OBJECT_KIND, raw, key + ":" + document.kind
    )
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        bind_physical_receipt(
            session,
            fence,
            membership_id,
            oid,
            hashlib.sha256(raw).hexdigest(),
            settings,
        )

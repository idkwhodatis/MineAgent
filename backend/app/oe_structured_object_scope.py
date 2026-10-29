"""027 publication scope is not an SDK/process grant or result membership."""

from sqlalchemy import text

from backend.app.oe_structured_controller_context import scoped
from backend.app.oe_structured_identity import decode_spec, fail
from backend.app.oe_structured_operational_models import OEStructuredEpoch
from backend.app.research_lifecycle import require_neutral_transaction
from packages.openevolve_structured.object_identity import parse_document


def require_object_scope(session, fence, kind, content):
    run, _ = require_neutral_transaction(session, fence, resource_scope="objects")
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        ["028_oe_structured_search"],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
    ):
        fail()
    _, config = decode_spec(session, run)
    document = parse_document(kind, content)
    if hasattr(document, "owner_id"):
        scoped(document, fence)
    if hasattr(document, "epoch_id"):
        scoped(session.get(OEStructuredEpoch, document.epoch_id), fence)
    if (
        kind in {"oe_structured_provider_request", "oe_structured_provider_receipt"}
        and document.model_binding_sha256 != config.model_binding.sha256
    ):
        fail()
    if kind == "oe_structured_provider_request":
        if document.execution_binding_sha256 != config.search.execution_binding_sha256:
            fail()
        from packages.openevolve_structured.provider_contracts import (
            require_request_binding,
        )

        require_request_binding(document, config.model_binding)
    return document

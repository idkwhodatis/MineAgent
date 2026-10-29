"""Rebuild a normal SDK request from current real worker and retained rendering."""

from dataclasses import dataclass

from backend.app.oe_structured_candidate_services import require_parent_candidate
from backend.app.oe_structured_controller_context import fail, scoped
from backend.app.oe_structured_operational_models import OEStructuredIterationIntent
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.contracts import SearchOperationContext
from packages.openevolve_structured.provider_contracts import (
    OEChatRequest,
    RenderedPrompt,
)
from packages.openevolve_structured.provider_response import build_request


@dataclass(frozen=True)
class ResolvedProviderOperation:
    intent: object
    config: object
    request: OEChatRequest
    context: SearchOperationContext


def require_provider_operation(session, fence, intent_id, settings, *, peer):
    intent = scoped(session.get(OEStructuredIterationIntent, intent_id), fence)
    parent = require_parent_candidate(
        session,
        fence,
        intent.parent_candidate_id,
        settings,
        peer=peer,
        current_intent_id=intent.id,
    )
    from backend.app.oe_structured_population import require_selection

    require_selection(session, fence, intent, parent.config, settings, peer=peer)
    context = SearchOperationContext.model_validate_json(intent.context_canonical)
    rendered = RenderedPrompt.model_validate_json(intent.rendered_canonical)
    if (
        canonical_bytes(context).decode("ascii") != intent.context_canonical
        or digest(context) != intent.context_sha256
        or canonical_bytes(rendered).decode("ascii") != intent.rendered_canonical
        or digest(rendered) != intent.rendered_sha256
        or context.rendered_context_sha256 != intent.rendered_sha256
        or context.selection_snapshot_sha256 != intent.selection_sha256
        or context.iteration_intent_id != intent.id
        or context.epoch_id != intent.epoch_id
        or context.native_iteration != intent.ordinal
        or context.owner_id != fence.owner_id
        or context.run_id != fence.run_id
        or context.parent_candidate_id != parent.candidate_id
        or context.execution_binding_sha256
        != parent.config.search.execution_binding_sha256
    ):
        fail()
    from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig

    if type(parent.config) is WorkflowStructuredRunConfig:
        from backend.app.oe_workflow_prompt_guard import require_workflow_prompt

        require_workflow_prompt(session, fence, parent.config, rendered, settings)
    request = build_request(
        binding=parent.config.model_binding,
        context=context,
        domain=parent.config.search.domain,
        parent_candidate=parent.candidate_bytes,
        rendered=rendered,
    )
    if (
        require_parent_candidate(
            session,
            fence,
            parent.candidate_id,
            settings,
            peer=peer,
            current_intent_id=intent.id,
        )
        != parent
    ):
        fail()
    return ResolvedProviderOperation(intent, parent.config, request, context)

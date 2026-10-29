"""Bind actual rendered observer messages to the one-use local socket client.

These are client consistency checks, not host source/proposal/launch authority.
The durable host rebuilds the request using actual retained parent bytes.
"""

from packages.factors.contracts import canonical_bytes, digest

from .provider_broker_client import ProviderOperationGrant, bind_operation
from .provider_contracts import JSONObject, OEChatRequest, ProviderMessage
from .workflow_bootstrap import parse_provider_bootstrap


def bind_rendered_operation(
    initial, descriptor, context, rendered, *, process_ticket, context_ticket
):
    bootstrap = parse_provider_bootstrap(canonical_bytes(initial))
    binding = bootstrap.model_binding
    if (
        str(bootstrap.admission_id) != descriptor["admission_id"]
        or (context.owner_id, context.run_id, context.epoch_id)
        != (bootstrap.spec.owner_id, bootstrap.spec.run_id, bootstrap.epoch_id)
        or rendered.current_candidate_sha256 != context.parent_candidate_sha256
        or digest(rendered) != context.rendered_context_sha256
        or context.execution_binding_sha256 != bootstrap.spec.execution_binding_sha256
    ):
        raise ValueError("Exact observed context/bootstrap/parent required")
    request = OEChatRequest(
        model_profile_id=binding.profile_id,
        model_binding_sha256=binding.sha256,
        model=binding.model,
        context_sha256=digest(context),
        candidate_domain_sha256=digest(bootstrap.spec.domain),
        parent_candidate_sha256=context.parent_candidate_sha256,
        execution_binding_sha256=context.execution_binding_sha256,
        rendered_context_sha256=digest(rendered),
        messages=(
            ProviderMessage(role="system", content=rendered.system),
            ProviderMessage(role="user", content=rendered.user),
        ),
        max_output_tokens=binding.max_output_tokens,
        response_format=JSONObject(),
    )
    return bind_operation(
        ProviderOperationGrant(
            admission_id=bootstrap.admission_id,
            context=context,
            request=request,
            binding=binding,
            domain=bootstrap.spec.domain,
            process_ticket=process_ticket,
            context_ticket=context_ticket,
        )
    )

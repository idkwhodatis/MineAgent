"""Host-only explicit OE protocol on the shared bounded trusted SDK mechanism.

Not a broker grant: callers must resolve operator/owner/fence and commit intent,
then persist returned response before candidate parsing. No RD DTO impersonation.
"""

from dataclasses import dataclass

from packages.rdagent_provider.transport import ProviderResult, execute

from .provider_contracts import OEChatResponse, require_request_binding

PROTOCOL = "mineagent.oe-structured-provider-chat.v1"


@dataclass(frozen=True)
class ProviderCompletion:
    response: OEChatResponse
    physical: ProviderResult


def complete_chat(
    request,
    *,
    binding,
    base_url,
    secret,
    deadline_monotonic,
    boundary=None,
    identity_boundary=None,
    python_executable=None,
):
    request = require_request_binding(request, binding)
    observed = execute(
        request,
        binding=binding,
        base_url=base_url,
        secret=secret,
        deadline_monotonic=deadline_monotonic,
        boundary=boundary,
        identity_boundary=identity_boundary,
        python_executable=python_executable,
        protocol=PROTOCOL,
    )
    if observed.vectors is not None or observed.stop_evidence is None:
        raise ValueError("Observed stopped OE chat result required")
    response = OEChatResponse(
        request_sha256=request.sha256,
        content=observed.content,
        finish_reason=observed.finish_reason,
        provider_request_id=observed.provider_request_id,
        input_tokens=observed.input_tokens,
        output_tokens=observed.output_tokens,
        total_tokens=observed.total_tokens,
        usage_source=observed.usage_source,
        billing_status=observed.billing_status,
    )
    # A valid provider completion can still contain an invalid/truncated program.
    # Return it for durable accounting first; parse_response is a separate step.
    return ProviderCompletion(response, observed)

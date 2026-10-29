"""Bind exact installed-rendered prompts and whole JSON proposals; pure data only.

No permissive upstream parse_full_rewrite fallback, markdown extraction, code
execution or whitespace normalization. Persist the original response first;
truncated/invalid proposals consume the call but never become candidates.
"""

from dataclasses import dataclass

from packages.factors.contracts import canonical_bytes, digest

from .candidate import TypedFrozenCandidateDomain, _raw_fields, project_candidate
from .contracts import SearchOperationContext
from .provider_contracts import (
    JSONObject,
    OEChatBinding,
    OEChatRequest,
    OEChatResponse,
    ProviderMessage,
    RenderedPrompt,
    require_request_binding,
)


def build_request(*, binding, context, domain, parent_candidate, rendered):
    if (
        type(binding) is not OEChatBinding
        or type(context) is not SearchOperationContext
        or type(domain) is not TypedFrozenCandidateDomain
        or type(rendered) is not RenderedPrompt
    ):
        raise ValueError("Exact typed OE prompt inputs required")
    binding = OEChatBinding.model_validate(_raw_fields(binding))
    if type(context.operation_ordinal) is not int:
        raise ValueError("Exact context operation ordinal required")
    context = SearchOperationContext.model_validate(_raw_fields(context))
    domain = TypedFrozenCandidateDomain.model_validate(_raw_fields(domain))
    rendered = RenderedPrompt.model_validate(_raw_fields(rendered))
    parent = project_candidate(parent_candidate, domain)
    if (
        context.parent_candidate_sha256 != parent.candidate_sha256
        or rendered.current_candidate_sha256 != parent.candidate_sha256
        or context.rendered_context_sha256 != digest(rendered)
    ):
        raise ValueError("Actual rendered prompt/parent differs from context")
    # Do not prepend a new prompt or infer messages from a task description. This
    # is the exact pair already rendered and observed by the installed worker.
    return require_request_binding(
        OEChatRequest(
            model_profile_id=binding.profile_id,
            model_binding_sha256=binding.sha256,
            model=binding.model,
            context_sha256=digest(context),
            candidate_domain_sha256=digest(domain),
            parent_candidate_sha256=parent.candidate_sha256,
            execution_binding_sha256=context.execution_binding_sha256,
            rendered_context_sha256=digest(rendered),
            messages=(
                ProviderMessage(role="system", content=rendered.system),
                ProviderMessage(role="user", content=rendered.user),
            ),
            max_output_tokens=binding.max_output_tokens,
            response_format=JSONObject(),
        ),
        binding,
    )


@dataclass(frozen=True)
class ParsedProposal:
    """Exact data projection, never a candidate row, process ticket or score."""

    request_sha256: str
    response_sha256: str
    parent_candidate_sha256: str
    candidate_bytes: bytes
    candidate_sha256: str
    program_bytes: bytes
    program_sha256: str


def parse_response(*, request, response, binding, domain):
    request = require_request_binding(request, binding)
    if (
        type(response) is not OEChatResponse
        or type(domain) is not TypedFrozenCandidateDomain
    ):
        raise ValueError("Exact typed response and frozen domain required")
    response = OEChatResponse.model_validate(_raw_fields(response))
    domain = TypedFrozenCandidateDomain.model_validate(_raw_fields(domain))
    if (
        response.request_sha256 != request.sha256
        or request.candidate_domain_sha256 != digest(domain)
        or response.usage_source
        != ("synthetic_provider" if binding.synthetic else "provider")
    ):
        raise ValueError("Response/request/domain identity differs")
    if response.finish_reason != "stop":
        raise ValueError("Incomplete OE proposal cannot register a candidate")
    raw = response.content.encode("utf-8")
    projected = project_candidate(raw, domain)
    return ParsedProposal(
        request.sha256,
        digest(response),
        request.parent_candidate_sha256,
        raw,
        projected.candidate_sha256,
        projected.program_bytes,
        projected.program_sha256,
    )


def response_bytes(response):
    """Canonical host envelope, not a claim to retain the original HTTP body."""
    if type(response) is not OEChatResponse:
        raise ValueError("Exact OE response required")
    return canonical_bytes(OEChatResponse.model_validate(_raw_fields(response)))

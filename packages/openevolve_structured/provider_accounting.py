"""Explicit027 SDK reservation and observed usage; not an invoice or free-call claim."""

from packages.research_runtime.budget_contracts import BudgetVector

from .candidate import _raw_fields
from .provider_contracts import OEChatBinding, OEChatResponse, require_request_binding

INPUT_TOKEN_BOUND = 262144
PIPE_PEAK_BYTES = 2 * 262144 + 65536


def request_reservation(request, binding):
    request = require_request_binding(request, binding)
    binding = OEChatBinding.model_validate(_raw_fields(binding))
    rates = binding.pricing
    if rates.status != "operator_declared":
        raise ValueError("Unknown pricing cannot support a cost-capped request")
    estimate = (
        INPUT_TOKEN_BOUND * rates.input_microusd_per_million
        + request.max_output_tokens * rates.output_microusd_per_million
    )
    return BudgetVector(
        calls=1,
        tokens=INPUT_TOKEN_BOUND + request.max_output_tokens,
        cost_microunits=(estimate + 999999) // 1000000,
        wall_milliseconds=binding.request_deadline_ms,
        storage_bytes=PIPE_PEAK_BYTES,
        process_slots=1,
    )


def validate_completed_usage(request, response, binding):
    """Only data/accounting; caller ALSO requires actual immutable SDK stop proof.

    Missing/over-limit usage is not evidence that a failed call was free: retain
    its whole hold conservatively. Provider billing remains explicitly unknown.
    """
    held = request_reservation(request, binding)
    if type(response) is not OEChatResponse:
        raise ValueError("Exact OE response required")
    response = OEChatResponse.model_validate(_raw_fields(response))
    if (
        response.request_sha256 != request.sha256
        or response.usage_source
        != ("synthetic_provider" if binding.synthetic else "provider")
        or any(
            value is None
            for value in (
                response.input_tokens,
                response.output_tokens,
                response.total_tokens,
            )
        )
        or response.input_tokens > INPUT_TOKEN_BOUND
        or response.output_tokens > request.max_output_tokens
        or response.total_tokens != response.input_tokens + response.output_tokens
    ):
        raise ValueError(
            "Incomplete or over-limit provider usage remains conservatively charged"
        )
    return response, held

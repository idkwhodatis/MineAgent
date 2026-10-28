"""Explicit conservative reservation, separate from provider usage or billing.

The input cap is an admission bound, not a tokenizer/pricing guarantee for an
arbitrary remote service. A provider reporting more than this bound is rejected
and its whole held vector remains charged/uncertain; billed money stays unknown.
Operator rates describe an estimate only, never an invoice or free service.
"""

from packages.rdagent_provider.codec import canonical_bytes, validate_response
from packages.rdagent_provider.contracts import (
    DeclaredTokenPricing,
    require_request_binding,
)
from packages.research_runtime.budget_contracts import BudgetVector

ACCOUNTING = "mineagent.rd-provider-conservative-reservation.v1"
INPUT_TOKEN_BOUND = 262144
PIPE_PEAK_BYTES = 2 * 262144 + 65536


def _ceil_million(value):
    return (value + 999999) // 1000000


def request_reservation(request, binding):
    require_request_binding(request, binding)
    if type(binding.pricing) is not DeclaredTokenPricing:
        raise ValueError("Unknown pricing cannot support a cost-capped request")
    rates = binding.pricing
    output = request.max_output_tokens if request.kind == "chat" else 0
    cost = (
        INPUT_TOKEN_BOUND * rates.chat_input_microusd_per_million
        + output * rates.chat_output_microusd_per_million
        if request.kind == "chat"
        else INPUT_TOKEN_BOUND * rates.embedding_microusd_per_million
    )
    return BudgetVector(
        calls=1,
        tokens=INPUT_TOKEN_BOUND + output,
        cost_microunits=_ceil_million(cost),
        wall_milliseconds=binding.request_deadline_ms,
        storage_bytes=PIPE_PEAK_BYTES,
        process_slots=1,
    )


def expected_usage(protocol, request, response, binding):
    """Return validated response plus conservative charge, not reported usage.

    Completion needs actual whole-process receipts independently. Persisted
    provider token counts are never replaced with this reservation vector.
    """
    held = request_reservation(request, binding)
    response = validate_response(protocol, request, response)
    usage = response.usage
    if any(
        value is None
        for value in (
            (usage.input_tokens, usage.output_tokens, usage.total_tokens)
            if request.kind == "chat"
            else (usage.input_tokens, usage.total_tokens)
        )
    ):
        raise ValueError("Missing provider usage cannot complete a transport")
    # Embeddings report input/total only; absence of completion tokens is part
    # of that protocol, not missing billing evidence and never a fabricated zero.
    if request.kind == "embedding" and (
        usage.output_tokens is not None or usage.input_tokens != usage.total_tokens
    ):
        raise ValueError("Embedding usage must report equal input/total only")
    if usage.usage_source != (
        "synthetic_provider" if binding.synthetic else "provider"
    ):
        raise ValueError("Provider usage provenance differs from declared transport")
    output = request.max_output_tokens if request.kind == "chat" else 0
    if (
        usage.input_tokens is not None
        and usage.input_tokens > INPUT_TOKEN_BOUND
        or usage.output_tokens is not None
        and usage.output_tokens > output
        or usage.total_tokens is not None
        and usage.total_tokens > held.tokens
    ):
        raise ValueError("Provider reported usage exceeds admitted bound")
    canonical_bytes(protocol, response)
    return response, held

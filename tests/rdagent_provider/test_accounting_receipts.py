from uuid import UUID

import pytest

from packages.rdagent_provider.accounting import expected_usage, request_reservation
from packages.rdagent_provider.contracts import (
    DeclaredTokenPricing,
    PricingUnavailable,
    RDModelBinding,
    RDProviderChatRequest,
    RDProviderChatResponse,
    RDProviderUsage,
)
from packages.rdagent_provider.process_receipts import (
    SDKIdentity,
    SDKIntent,
    SDKStarted,
    SDKStopped,
    parse_receipt,
    receipt_bytes,
    validate_chain,
)


def pair(pricing=None):
    binding = RDModelBinding(
        profile_id="synthetic",
        profile_sha256="a" * 64,
        chat_model="chat",
        embedding_model="embed",
        embedding_dimensions=2,
        max_output_tokens=100,
        request_deadline_ms=8000,
        synthetic=True,
        pricing=pricing
        or DeclaredTokenPricing(
            chat_input_microusd_per_million=1,
            chat_output_microusd_per_million=2,
            embedding_microusd_per_million=3,
        ),
    )
    request = RDProviderChatRequest(
        model_profile_id=binding.profile_id,
        model_binding_sha256=binding.sha256,
        model=binding.chat_model,
        messages=(
            {"role": "user", "content": "Research intraday range, not momentum"},
        ),
        max_output_tokens=100,
        response_format=None,
    )
    return binding, request


def chain():
    scope = {
        "owner_id": UUID(int=1),
        "run_id": UUID(int=2),
        "attempt_id": UUID(int=3),
        "generation": 1,
        "transport_id": UUID(int=4),
        "request_sha256": "a" * 64,
        "model_binding_sha256": "b" * 64,
    }
    intent = SDKIntent(
        **scope,
        admitted_monotonic_ns=1000,
        deadline_monotonic_ns=8000001000,
        maximum_duration_ms=8000,
        boot_id=UUID(int=5),
    )
    identity = SDKIdentity(
        pid=10,
        start_ticks=1,
        process_group=10,
        session_id=10,
        parent_pid=9,
        boot_id=UUID(int=5),
        deadline_monotonic_ns=intent.deadline_monotonic_ns,
    )
    return (
        intent,
        SDKStarted(**scope, identity=identity),
        SDKStopped(
            **scope,
            identity=identity,
            observed_monotonic_ns=2000,
            leader_reaped=True,
            process_group_absent=True,
        ),
    )


def test_unknown_pricing_never_becomes_free():
    binding, request = pair(PricingUnavailable())
    with pytest.raises(ValueError, match="Unknown pricing"):
        request_reservation(request, binding)


def test_real_usage_and_unknown_billing_remain_distinct_from_full_charge():
    binding, request = pair()
    response = RDProviderChatResponse(
        content="generated non-momentum source",
        finish_reason="stop",
        usage=RDProviderUsage(
            input_tokens=7,
            output_tokens=4,
            total_tokens=11,
            usage_source="synthetic_provider",
        ),
    )
    verified, charge = expected_usage(
        request.schema_version, request, response, binding
    )
    assert verified.usage.total_tokens == 11
    assert verified.usage.billing_status == "unknown"
    assert charge == request_reservation(request, binding)
    assert charge.process_slots == 1 and charge.wall_milliseconds == 8000
    assert charge.tokens == 262244 and charge.cost_microunits == 1


@pytest.mark.parametrize("tokens", [None, 0])
def test_missing_usage_is_not_fabricated(tokens):
    binding, request = pair()
    response = RDProviderChatResponse(
        content="code",
        finish_reason="stop",
        usage=RDProviderUsage(
            input_tokens=tokens,
            output_tokens=tokens,
            total_tokens=tokens,
            usage_source="synthetic_provider",
        ),
    )
    if tokens is None:
        with pytest.raises(ValueError, match="Missing provider usage"):
            expected_usage(request.schema_version, request, response, binding)
        assert response.usage.input_tokens is None
    else:
        verified, charge = expected_usage(
            request.schema_version, request, response, binding
        )
        assert verified.usage.input_tokens == 0
        assert charge.tokens > 0


def test_receipts_roundtrip_and_exact_original_chain():
    rows = chain()
    parsed = tuple(parse_receipt(receipt_bytes(row)) for row in rows)
    assert validate_chain(*parsed, completed=True) == rows[-1]
    with pytest.raises(ValueError, match="Noncanonical"):
        parse_receipt(receipt_bytes(rows[0]) + b" ")


@pytest.mark.parametrize(
    "field,value",
    [("transport_id", UUID(int=99)), ("request_sha256", "c" * 64), ("generation", 2)],
)
def test_receipt_scope_drift_rejected(field, value):
    intent, start, stop = chain()
    stop = SDKStopped.model_validate({**stop.model_dump(), field: value})
    with pytest.raises(ValueError, match="scope drift"):
        validate_chain(intent, start, stop, completed=True)


def test_late_positive_stop_retained_but_never_completed():
    intent, start, stop = chain()
    stop = SDKStopped.model_validate(
        {**stop.model_dump(), "observed_monotonic_ns": intent.deadline_monotonic_ns + 1}
    )
    assert validate_chain(intent, start, stop, completed=False) == stop
    with pytest.raises(ValueError, match="Late SDK"):
        validate_chain(intent, start, stop, completed=True)


@pytest.mark.parametrize("field", ["leader_reaped", "process_group_absent"])
def test_no_partial_stop_receipt(field):
    stop = chain()[-1].model_dump()
    stop[field] = False
    with pytest.raises(ValueError, match="whole-group"):
        SDKStopped.model_validate(stop)

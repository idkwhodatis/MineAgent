"""Conservative SDK cost/physical accounting, not a remote billing claim."""

import pytest

from backend.app.oe_structured_preflight import budget_floor
from packages.openevolve_structured.provider_accounting import (
    request_reservation,
    validate_completed_usage,
)
from packages.openevolve_structured.provider_contracts import ChatPricing
from tests.openevolve.test_structured_provider_contracts import declared, response
from tests.openevolve.test_structured_provider_submission import request as submission


def priced():
    _, binding, _, _, raw, _, _, request = declared()
    binding = binding.model_copy(
        update={
            "pricing": ChatPricing(
                status="operator_declared",
                input_microusd_per_million=3,
                output_microusd_per_million=7,
            )
        }
    )
    request = request.model_copy(update={"model_binding_sha256": binding.sha256})
    return binding, request, raw


def test_unknown_price_is_not_zero():
    _, binding, _, _, _, _, _, request = declared()
    with pytest.raises(ValueError, match="Unknown pricing"):
        request_reservation(request, binding)


def test_full_conservative_charge_is_separate_from_small_actual_usage():
    binding, request, raw = priced()
    value = response(request, raw, input_tokens=7, output_tokens=11, total_tokens=18)
    observed, held = validate_completed_usage(request, value, binding)
    assert observed.total_tokens == 18
    assert observed.billing_status == "unknown"
    assert held.tokens == 262144 + 4096
    assert held.cost_microunits == 1
    assert held.process_slots == 1
    assert held.storage_bytes == 589824


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_tokens", None),
        ("output_tokens", None),
        ("total_tokens", None),
        ("input_tokens", 262145),
        ("output_tokens", 4097),
        ("total_tokens", 19),
        ("usage_source", "synthetic_provider"),
    ],
)
def test_incomplete_or_changed_usage_cannot_reduce_unknown_hold(field, value):
    binding, request, raw = priced()
    observed = response(request, raw, input_tokens=7, output_tokens=11, total_tokens=18)
    observed = observed.model_copy(update={field: value})
    with pytest.raises(ValueError):
        validate_completed_usage(request, observed, binding)


@pytest.mark.parametrize("mutations,units,evaluations", [(1, 4, 2), (2, 6, 3)])
def test_normal_budget_covers_controller_native_and_each_sdk(
    mutations, units, evaluations
):
    binding, _, _ = priced()
    request = submission().model_copy(update={"maximum_mutation_slots": mutations})
    floor = budget_floor(request, binding)
    assert floor.process_slots == units
    assert floor.evaluator_executions == evaluations
    assert floor.calls == mutations
    assert floor.cost_microunits == mutations
    assert floor.wall_milliseconds == 600000 + 90000 * evaluations + 8000 * mutations

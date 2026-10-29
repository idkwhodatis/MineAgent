"""Normal operator request grammar, not a claim of installed027 database support."""

from uuid import uuid4

import pytest

from backend.app.oe_structured_provider_submission import (
    ProviderStructuredSearchRequest,
    ProviderStructuredSearchSubmit,
    ProviderStructuredSubmissionReceipt,
)
from packages.openevolve_structured.provider_contracts import OEChatBinding
from tests.openevolve.test_structured_submission import request as fixture_request


def binding():
    return OEChatBinding(
        profile_id="local",
        profile_sha256="a" * 64,
        model="chat",
        max_output_tokens=4096,
        request_deadline_ms=8000,
    )


def request():
    raw = fixture_request().model_dump()
    raw.pop("schema_version")
    raw.pop("profile")
    return ProviderStructuredSearchRequest(
        **raw, model_profile_id="local", model_binding_sha256=binding().sha256
    )


@pytest.mark.parametrize("slots,count", [(1, 4), (2, 6)])
def test_normal_counts_include_sdk_process_per_mutation(slots, count):
    raw = request().model_dump()
    raw["maximum_mutation_slots"] = slots
    value = ProviderStructuredSearchRequest.model_validate(raw)
    assert value.required_process_units == count
    assert value.require_binding(binding()) == binding()


@pytest.mark.parametrize(
    "field",
    [
        "base_url",
        "api_key",
        "python_executable",
        "engine_image_id",
        "approved",
        "origin",
        "pin",
    ],
)
def test_request_cannot_supply_private_operator_or_launch_authority(field):
    raw = request().model_dump()
    raw[field] = "caller"
    with pytest.raises(ValueError):
        ProviderStructuredSearchRequest.model_validate(raw)


def test_fixture_request_is_not_normal_operator_request():
    with pytest.raises(ValueError):
        ProviderStructuredSearchRequest.model_validate(fixture_request().model_dump())


def test_changed_binding_requires_fresh_preflight():
    other = binding().model_copy(update={"model": "changed"})
    with pytest.raises(ValueError, match="preflight"):
        request().require_binding(other)


def test_unknown_pricing_stays_unknown_not_zero():
    assert binding().pricing.status == "unavailable"
    assert binding().pricing.input_microusd_per_million is None
    assert binding().pricing.output_microusd_per_million is None


def test_submit_preserves_exact_authenticated_request():
    original = request()
    submit = ProviderStructuredSearchSubmit(
        **original.model_dump(), preflight_sha256="b" * 64
    )
    assert submit.request() == original
    receipt = ProviderStructuredSubmissionReceipt(
        id=uuid4(),
        run_id=uuid4(),
        origin_user_id=uuid4(),
        request_sha256=original.sha256,
        preflight_sha256="b" * 64,
        normalized_request=original,
        required_process_units=4,
    )
    assert receipt.origin == "authenticated_user_submission"
    with pytest.raises(ValueError):
        ProviderStructuredSubmissionReceipt.model_validate(
            {**receipt.model_dump(), "required_process_units": 3}
        )

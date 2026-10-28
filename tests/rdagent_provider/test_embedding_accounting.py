"""Real SDK primitive/parent validation to accounting; no HTTP/provider calls."""

import pytest

from packages.rdagent_provider.accounting import expected_usage, request_reservation
from packages.rdagent_provider.contracts import (
    RDProviderEmbeddingRequest,
    RDProviderEmbeddingResponse,
    RDProviderUsage,
)
from packages.rdagent_provider.sdk_process import RejectedResponse, validate_document
from packages.rdagent_provider.transport import _validated_result
from tests.rdagent_provider.test_accounting_receipts import pair


def fixture():
    binding, _ = pair()
    request = RDProviderEmbeddingRequest(
        model_profile_id=binding.profile_id,
        model_binding_sha256=binding.sha256,
        model=binding.embedding_model,
        dimensions=2,
        inputs=("actual experiment feedback",),
    )
    document = {
        "object": "list",
        "model": request.model,
        "data": [{"object": "embedding", "index": 0, "embedding": [1.0, 0.0]}],
        "usage": {"prompt_tokens": 100, "total_tokens": 100},
    }
    return binding, request, document


def response_from_sdk(request, document):
    raw_request = request.model_dump(mode="json")
    child = validate_document(document, raw_request, True)
    result = _validated_result(child, raw_request, True)
    return RDProviderEmbeddingResponse(
        vectors=result.vectors,
        usage=RDProviderUsage(
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            total_tokens=result.total_tokens,
            provider_request_id=result.provider_request_id,
            usage_source=result.usage_source,
            billing_status=result.billing_status,
        ),
    )


@pytest.mark.parametrize("tokens", [0, 100, 262144])
def test_actual_embedding_primitive_completes_without_fabricating_output(tokens):
    binding, request, document = fixture()
    document["usage"] = {"prompt_tokens": tokens, "total_tokens": tokens}
    response = response_from_sdk(request, document)
    verified, charge = expected_usage(
        request.schema_version, request, response, binding
    )
    assert verified.usage.input_tokens == verified.usage.total_tokens == tokens
    assert verified.usage.output_tokens is None
    assert verified.usage.billing_status == "unknown"
    assert charge == request_reservation(request, binding)
    assert charge.tokens == 262144 and charge.calls == 1
    assert charge.wall_milliseconds == 8000 and charge.process_slots == 1
    assert charge.cost_microunits == 1


@pytest.mark.parametrize(
    "usage",
    [
        {},
        {"prompt_tokens": 100},
        {"total_tokens": 100},
        {"prompt_tokens": None, "total_tokens": None},
        {"prompt_tokens": 1, "total_tokens": 2},
        {"prompt_tokens": -1, "total_tokens": -1},
        {"prompt_tokens": True, "total_tokens": True},
        {"prompt_tokens": 1.0, "total_tokens": 1.0},
        {"prompt_tokens": "1", "total_tokens": "1"},
        {"prompt_tokens": 1, "total_tokens": 1, "completion_tokens": 0},
        {"prompt_tokens": 262145, "total_tokens": 262145},
    ],
)
def test_invalid_or_missing_embedding_usage_never_completes(usage):
    binding, request, document = fixture()
    document["usage"] = usage
    with pytest.raises((ValueError, RejectedResponse)):
        response = response_from_sdk(request, document)
        expected_usage(request.schema_version, request, response, binding)


@pytest.mark.parametrize(
    "changes",
    [
        {"input_tokens": None},
        {"total_tokens": None},
        {"total_tokens": 101},
        {"output_tokens": 0},
        {"usage_source": "provider"},
    ],
)
def test_accounting_independently_rejects_typed_embedding_usage_drift(changes):
    binding, request, document = fixture()
    response = response_from_sdk(request, document)
    usage = response.usage.model_copy(update=changes)
    response = response.model_copy(update={"usage": usage})
    with pytest.raises(ValueError):
        expected_usage(request.schema_version, request, response, binding)


def test_chat_missing_output_is_still_missing_not_embedding_zero():
    from packages.rdagent_provider.contracts import RDProviderChatResponse

    binding, request = pair()
    response = RDProviderChatResponse(
        content="code",
        finish_reason="stop",
        usage=RDProviderUsage(
            input_tokens=100,
            output_tokens=None,
            total_tokens=100,
            usage_source="synthetic_provider",
        ),
    )
    with pytest.raises(ValueError, match="Missing provider usage"):
        expected_usage(request.schema_version, request, response, binding)

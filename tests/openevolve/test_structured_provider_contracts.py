"""Distinct normal-provider grammar and exact JSON proposals; no provider calls."""

import hashlib
import json
from uuid import uuid4

import pytest

from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.contracts import SearchOperationContext
from packages.openevolve_structured.provider_contracts import (
    ChatPricing,
    OEChatBinding,
    OEChatRequest,
    OEChatResponse,
    RenderedPrompt,
)
from packages.openevolve_structured.provider_operator import OEOperatorChatProfile
from packages.openevolve_structured.provider_response import (
    build_request,
    parse_response,
    response_bytes,
)
from tests.openevolve.test_structured_candidate import setup_candidate


def declared():
    owner = uuid4()
    profile = OEOperatorChatProfile(
        connection={
            "id": "operator-chat",
            "label": "Operator chat",
            "model": "chosen-model",
            "base_url": "https://provider.example/v1",
            "secret_env": "MODEL_SECRET",
            "owner_ids": (owner,),
            "temperature": 0,
            "max_output_tokens": 4096,
            "request_timeout_seconds": 8,
            "max_request_bytes": 262144,
            "max_response_bytes": 262144,
        },
        pricing=ChatPricing(),
    )
    binding = profile.public_binding(owner)
    domain, value = setup_candidate()
    raw = canonical_bytes(value)
    rendered = RenderedPrompt(
        system="Return a canonical JSON candidate.",
        user="Current exact candidate:\n" + raw.decode(),
        references=(),
        current_candidate_sha256=hashlib.sha256(raw).hexdigest(),
        artifact_section_sha256=None,
    )
    context = SearchOperationContext(
        owner_id=owner,
        run_id=uuid4(),
        epoch_id=uuid4(),
        iteration_intent_id=uuid4(),
        native_iteration=1,
        parent_candidate_id=uuid4(),
        parent_candidate_sha256=hashlib.sha256(raw).hexdigest(),
        execution_binding_sha256="a" * 64,
        selection_snapshot_sha256="b" * 64,
        rendered_context_sha256=digest(rendered),
    )
    request = build_request(
        binding=binding,
        context=context,
        domain=domain,
        parent_candidate=raw,
        rendered=rendered,
    )
    return profile, binding, domain, value, raw, rendered, context, request


def response(request, raw, **changes):
    fields = {
        "request_sha256": request.sha256,
        "content": raw.decode(),
        "finish_reason": "stop",
        "usage_source": "provider",
        "provider_request_id": "observed-provider-request",
    }
    fields.update(changes)
    return OEChatResponse(**fields)


def test_exact_rendered_pair_and_original_response_are_bound_not_rewritten():
    _, binding, domain, value, parent, rendered, context, request = declared()
    value["program"]["threshold"] = 0.03
    raw = canonical_bytes(value)
    reply = response(request, raw)
    projected = parse_response(
        request=request, response=reply, binding=binding, domain=domain
    )
    assert tuple(m.content for m in request.messages) == (
        rendered.system,
        rendered.user,
    )
    assert request.context_sha256 == digest(context)
    assert request.rendered_context_sha256 == digest(rendered)
    assert request.parent_candidate_sha256 == hashlib.sha256(parent).hexdigest()
    assert projected.candidate_bytes == raw
    assert projected.program_bytes == canonical_bytes(value["program"])
    assert (
        projected.response_sha256 == hashlib.sha256(response_bytes(reply)).hexdigest()
    )
    assert projected.candidate_sha256 != projected.parent_candidate_sha256
    assert reply.input_tokens is reply.output_tokens is reply.total_tokens is None
    assert reply.billing_status == "unknown" and binding.pricing.status == "unavailable"
    assert request.schema_version != "mineagent.rdagent-provider-chat.v1"
    assert "fixture" not in request.schema_version


def test_operator_allowlist_and_public_binding_never_disclose_endpoint_or_secret_reference():
    profile, binding, *_ = declared()
    raw = canonical_bytes(binding)
    assert b"provider.example" not in raw and b"MODEL_SECRET" not in raw
    with pytest.raises(ValueError):
        profile.public_binding(uuid4())
    changed = profile.model_copy(
        update={
            "pricing": ChatPricing(
                status="operator_declared",
                input_microusd_per_million=0,
                output_microusd_per_million=0,
            )
        }
    )
    assert (
        changed.public_binding(profile.connection.owner_ids[0]).sha256 != binding.sha256
    )
    assert (
        changed.public_binding(profile.connection.owner_ids[0]).pricing.status
        == "operator_declared"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("base_url", "https://example.invalid/v1"),
        ("secret", "private"),
        ("secret_env", "KEY"),
        ("owner_ids", []),
        ("grant", True),
    ],
)
def test_public_binding_rejects_private_authority_fields(field, value):
    _, binding, *_ = declared()
    values = binding.model_dump(mode="json") | {field: value}
    with pytest.raises(ValueError):
        OEChatBinding.model_validate_json(json.dumps(values))


@pytest.mark.parametrize(
    "field,value",
    [
        ("temperature_millionths", False),
        ("max_output_tokens", True),
        ("max_output_tokens", 1.5),
        ("schema_version", "mineagent.rdagent-provider-chat.v1"),
        ("schema_version", "mineagent.oe-structured-model-request.v1"),
    ],
)
def test_protocol_and_numeric_slots_do_not_alias_old_fixture_or_rd(field, value):
    *_, request = declared()
    with pytest.raises(ValueError):
        OEChatRequest.model_validate_json(
            json.dumps(request.model_dump(mode="json") | {field: value})
        )


@pytest.mark.parametrize(
    "change", ["messages", "rendered_hash", "parent", "domain", "ordinal"]
)
def test_prompt_or_context_drift_fails_before_request(change):
    _, binding, domain, value, raw, rendered, context, _ = declared()
    if change == "messages":
        rendered = rendered.model_copy(update={"user": rendered.user + " changed"})
    elif change == "rendered_hash":
        context = context.model_copy(update={"rendered_context_sha256": "f" * 64})
    elif change == "parent":
        value["program"]["threshold"] = 0.02
        raw = canonical_bytes(value)
    elif change == "domain":
        value["program"]["missing_value_policy"] = "invented"
        raw = canonical_bytes(value)
    else:
        context = context.model_copy(update={"operation_ordinal": False})
    with pytest.raises(ValueError):
        build_request(
            binding=binding,
            context=context,
            domain=domain,
            parent_candidate=raw,
            rendered=rendered,
        )


@pytest.mark.parametrize(
    "change",
    [
        "markdown",
        "python",
        "pretty",
        "newline",
        "duplicate",
        "nan",
        "extra",
        "operator",
    ],
)
def test_raw_proposal_is_strict_whole_program_not_permissive_rewrite_fallback(change):
    _, binding, domain, value, raw, _, _, request = declared()
    if change == "markdown":
        raw = b"```json\n" + raw + b"\n```"
    elif change == "python":
        raw = b"def strategy(): return 1"
    elif change == "pretty":
        raw = json.dumps(value, indent=2).encode()
    elif change == "newline":
        raw += b"\n"
    elif change == "duplicate":
        raw = raw.replace(b'"threshold":0.0', b'"threshold":0.0,"threshold":0.0')
    elif change == "nan":
        raw = raw.replace(b'"threshold":0.0', b'"threshold":NaN')
    elif change == "extra":
        raw = canonical_bytes(value | {"claimed_score": 100})
    else:
        value["program"]["score_operator"] = "arbitrary_python"
        raw = canonical_bytes(value)
    with pytest.raises(ValueError):
        parse_response(
            request=request,
            response=response(request, raw),
            binding=binding,
            domain=domain,
        )


@pytest.mark.parametrize("change", ["length", "request", "source", "binding"])
def test_incomplete_or_wrong_response_never_registers_candidate(change):
    _, binding, domain, _, raw, _, _, request = declared()
    reply = response(request, raw)
    if change == "length":
        reply = reply.model_copy(update={"finish_reason": "length"})
    elif change == "request":
        reply = reply.model_copy(update={"request_sha256": "e" * 64})
    elif change == "source":
        reply = reply.model_copy(update={"usage_source": "synthetic_provider"})
    else:
        binding = binding.model_copy(update={"model": "different-model"})
    with pytest.raises(ValueError):
        parse_response(request=request, response=reply, binding=binding, domain=domain)


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_tokens", True),
        ("output_tokens", -1),
        ("total_tokens", 1.2),
        ("billing_status", "free"),
        ("provider_request_id", "x\nsecret"),
    ],
)
def test_provider_accounting_never_coerces_unknown_to_zero_or_actual_price(
    field, value
):
    *_, request = declared()
    with pytest.raises(ValueError):
        response(request, b"{}", **{field: value})


def test_partial_usage_preserved_and_inconsistent_totals_rejected():
    *_, request = declared()
    reply = response(request, b"{}", input_tokens=4)
    assert reply.output_tokens is None and reply.total_tokens is None
    with pytest.raises(ValueError):
        response(request, b"{}", input_tokens=4, output_tokens=2, total_tokens=7)


def test_forged_nested_operator_boolean_and_literal_bytes_rejected():
    profile, binding, *_ = declared()
    bad_connection = profile.connection.model_copy(update={"max_output_tokens": True})
    with pytest.raises(ValueError):
        profile.model_copy(update={"connection": bad_connection}).public_binding(
            profile.connection.owner_ids[0]
        )
    with pytest.raises(ValueError):
        OEChatBinding.model_validate_json(
            json.dumps(
                binding.model_dump(mode="json") | {"max_request_bytes": 262144.0}
            )
        )


def test_rendered_unicode_byte_bound_is_not_character_bound():
    _, _, _, _, _, rendered, _, _ = declared()
    with pytest.raises(ValueError):
        RenderedPrompt.model_validate(
            {**rendered.model_dump(), "system": "界" * 100000}
        )

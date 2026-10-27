import pytest

from packages.rdagent_adapter.response_parser import parse_selected_responses
from packages.rdagent_provider.contracts import RDProviderChatResponse, RDProviderUsage
from packages.rdagent_provider.source_parser import parse_selected_provider_responses
from packages.research_runtime.engine_broker_contracts import RDChatResponse


@pytest.mark.parametrize(
    "parts",
    [
        ('{"code":"x=1"}',),
        ('```json\n{"code":"x=1"}\n```',),
        ('{"code":"x=0","code":"x=1"}',),
        ('{"code":', '"x=1"}'),
        ('<think>reason</think>{"code":"x=1"}',),
        ("broken",),
        ('{"other":"x"}',),
        ('{"code":7}',),
    ],
)
def test_provider_parser_matches_pinned_legacy_content_algorithm(parts):
    usage = RDProviderUsage(
        input_tokens=2,
        output_tokens=2,
        total_tokens=4,
        usage_source="synthetic_provider",
    )
    old = tuple(
        RDChatResponse(
            content=value, finish_reason="stop" if i == len(parts) - 1 else "length"
        )
        for i, value in enumerate(parts)
    )
    new = tuple(
        RDProviderChatResponse(
            content=row.content, finish_reason=row.finish_reason, usage=usage
        )
        for row in old
    )
    try:
        expected = parse_selected_responses(old)
    except ValueError as error:
        with pytest.raises(type(error)) as actual:
            parse_selected_provider_responses(new)
        assert actual.value.stage == error.stage
    else:
        assert parse_selected_provider_responses(new) == expected
    assert all(row.usage.total_tokens == 4 for row in new)


def test_provider_parser_never_accepts_legacy_response_envelope():
    with pytest.raises(ValueError):
        parse_selected_provider_responses(
            (RDChatResponse(content='{"code":"x=1"}', finish_reason="stop"),)
        )


@pytest.mark.parametrize("provider", [False, True])
@pytest.mark.parametrize("foreign", [False, True])
def test_actual_host_publish_dispatches_only_matching_retained_response_protocol(
    monkeypatch, tmp_path, provider, foreign
):
    from contextlib import contextmanager
    from types import SimpleNamespace as NS
    from uuid import uuid4

    from backend.app import rdagent_operational_events as events
    from backend.app.services import DomainError
    from packages.factors.contracts import source_sha256
    from packages.rdagent_adapter.host_evidence import ResponseAssemblyDocument
    from packages.research_runtime.fences import AttemptFence

    source = "x=1"
    actual_provider = provider != foreign
    protocol = (
        "mineagent.rdagent-provider-chat.v1"
        if actual_provider
        else "mineagent.rdagent-chat.v1"
    )
    response = (
        RDProviderChatResponse(
            content='{"code":"x=1"}',
            finish_reason="stop",
            usage=RDProviderUsage(
                input_tokens=2,
                output_tokens=2,
                total_tokens=4,
                usage_source="synthetic_provider",
            ),
        )
        if actual_provider
        else RDChatResponse(content='{"code":"x=1"}', finish_reason="stop")
    )
    fence = AttemptFence(uuid4(), uuid4(), uuid4(), 1, uuid4())
    call = NS(id=uuid4())
    member = NS(id=uuid4(), relation="initial", protocol=protocol)
    outcome = NS(id=uuid4(), response_object_id=uuid4(), response_sha256="a" * 64)
    session = NS(scalars=lambda query: [call])
    launch = NS(admission_id=uuid4())
    config = NS(
        schema_version=(
            "mineagent.factor-research.v2"
            if provider
            else "mineagent.factor-research.v1"
        )
    )
    seen = []

    @contextmanager
    def transaction(*a, **kw):
        yield session

    @contextmanager
    def storage(*a, **kw):
        yield

    monkeypatch.setattr(events, "neutral_transaction", transaction)
    monkeypatch.setattr(events, "shared_lifecycle", storage)
    monkeypatch.setattr(events, "require_rd_controller", lambda *a: launch)
    monkeypatch.setattr(events, "require_research_spec", lambda *a: (None, config))
    monkeypatch.setattr(events, "_scope_row", lambda *a, **kw: None)
    monkeypatch.setattr(events, "_call_members", lambda *a: [member])
    monkeypatch.setattr(
        events, "_completed_member_response", lambda *a: (outcome, response)
    )

    def publish(factory, current, settings, kind, raw, key):
        assert current == fence and kind == "factor_response_assembly"
        document = ResponseAssemblyDocument.model_validate_json(raw)
        assert document.source == source
        assert document.parts[0].member_id == member.id
        assert document.parts[0].response_object_id == outcome.response_object_id
        assert document.parts[0].response_sha256 == outcome.response_sha256
        seen.append(document)
        return uuid4()

    monkeypatch.setattr(events, "_publish", publish)
    monkeypatch.setattr(events, "reserve_source", lambda *a, **kw: NS(id=uuid4()))
    monkeypatch.setattr(
        events, "publish_reserved_source", lambda *a, **kw: kw["content"]
    )
    args = {
        "start_id": uuid4(),
        "task_index": 0,
        "source": source,
        "source_sha256": source_sha256(source),
        "settings": NS(storage_root=tmp_path),
    }
    if foreign:
        with pytest.raises(DomainError) as error:
            events.publish_generated_source(
                NS(kw={"bind": object()}), fence, object(), **args
            )
        assert error.value.code == "rd_operational_source_protocol"
        assert seen == []
    else:
        assert (
            events.publish_generated_source(
                NS(kw={"bind": object()}), fence, object(), **args
            )
            == source.encode()
        )
        assert len(seen) == 1

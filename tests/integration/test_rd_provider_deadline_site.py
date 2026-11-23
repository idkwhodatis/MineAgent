"""Diagnostic classification against the real broker with local doubles."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from backend.app import rdagent_provider_broker as module
from packages.rdagent_provider.transport import ProviderFailure
from tests.integration.rd_rpc_diagnostic import _queue_failure
from tests.rdagent.test_rdagent_provider_broker import broker as broker  # noqa: PLC0414


@pytest.mark.parametrize(
    ("source", "site"),
    [
        ("pre", "pre_request_publish"),
        ("post", "post_sdk_pre_response_publish"),
        ("nested", None),
    ],
)
def test_exact_broker_provider_deadline_raise(broker, monkeypatch, source, site):
    from tests.integration import rd_rpc_diagnostic

    clock = [broker.args["original_deadline"] - 1]
    real_sdk = module.execute
    monkeypatch.setattr(
        module,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock[0], monotonic_ns=module.time.monotonic_ns
        ),
    )
    monkeypatch.setattr(
        module, "_positive_sdk_stop_admission", lambda *_: nullcontext()
    )

    def sdk(*args, **kwargs):
        if source == "nested":
            raise ProviderFailure("original_controller_deadline", contained=True)
        result = real_sdk(*args, **kwargs)
        clock[0] = broker.args["original_deadline"]
        return result

    monkeypatch.setattr(module, "execute", sdk)
    if source == "pre":
        clock[0] = broker.args["original_deadline"]

    with pytest.raises(ProviderFailure) as failure:
        module.submit_provider_request(broker.factory, broker.fence, **broker.args)
    assert failure.value.code == "original_controller_deadline"
    assert _queue_failure(failure.value).get("provider_deadline_site") is None
    monkeypatch.setattr(
        rd_rpc_diagnostic,
        "_closed_trace_context",
        lambda _: {"rpc_method": "submit_chat"},
    )
    assert _queue_failure(failure.value).get("provider_deadline_site") == site
    assert broker.state.calls == (1 if source == "post" else 0)
    failure.value.contained = not failure.value.contained
    assert _queue_failure(failure.value).get("provider_deadline_site") is None

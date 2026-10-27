"""Client protocol tests only; no host execution or provider success claim."""

from uuid import UUID

import pytest

from packages.research_runtime.engine_broker_contracts import (
    canonical_bytes,
    parse_request,
)
from runtimes.rdagent_operational.client import OperationalHost, decode_evaluation_frame
from runtimes.rdagent_operational.rpc import RPCError


class Channel:
    def __init__(self):
        self.calls = []

    def call(self, method, body):
        self.calls.append((method, body))
        return {
            "begin_step": {"start_id": str(UUID(int=1))},
            "begin_exchange": {"exchange_id": str(UUID(int=2))},
            "submit_chat": {
                "operation_id": str(UUID(int=3)),
                "response": {
                    "kind": "chat",
                    "content": "retained fixture",
                    "finish_reason": "stop",
                },
            },
            "submit_embedding": {
                "operation_id": str(UUID(int=4)),
                "response": {"kind": "embedding", "vectors": [[1.0, 0.0]]},
            },
            "check_control": {"current": True},
            "end_exchange": {},
        }[method]


def ready(kind):
    channel = Channel()
    host = OperationalHost(channel)
    host.begin_step(0)
    exchange = host.begin_exchange(kind=kind, phase="generation")
    return channel, host, exchange


def test_no_host_operation_or_placeholder_before_exact_request_submission():
    channel, host, exchange = ready("chat")
    before = list(channel.calls)
    nonce = host.new_request(exchange, relation="initial")
    assert channel.calls == before  # nonce is NOT authority or a host operation
    assert host.chat(
        nonce,
        messages=[{"role": "user", "content": "source?"}],
        response_format={"type": "json_object"},
    ) == ("retained fixture", "stop")
    method, body = channel.calls[-1]
    assert method == "submit_chat" and "operation_id" not in body
    assert body["idempotency_key"] == str(nonce)
    raw = body["request_utf8"].encode()
    parsed = parse_request(body["protocol"], raw)
    assert canonical_bytes(body["protocol"], parsed) == raw
    assert parsed.response_format.type == "json_object"
    with pytest.raises(RPCError, match="request_nonce"):
        host.chat(
            nonce, messages=[{"role": "user", "content": "again"}], response_format=None
        )


def test_embedding_uses_same_atomic_exact_request_path():
    channel, host, exchange = ready("embedding")
    nonce = host.new_request(exchange, relation="initial")
    assert host.embedding(nonce, inputs=["fixed"]) == [[1.0, 0.0]]
    assert channel.calls[-1][0] == "submit_embedding"


def test_unresolved_pending_request_cannot_be_replaced_or_exchange_closed():
    _, host, exchange = ready("chat")
    host.new_request(exchange, relation="initial")
    with pytest.raises(RPCError, match="request_order"):
        host.new_request(exchange, relation="continuation")
    with pytest.raises(RPCError, match="exchange_order"):
        host.end_exchange(exchange, succeeded=True)


def test_no_exchange_before_persisted_generation_start():
    host = OperationalHost(Channel())
    with pytest.raises(RPCError, match="missing_start"):
        host.begin_exchange(kind="chat", phase="generation")


def test_primitive_frame_validation_without_native_input():
    from packages.factors.contracts import digest
    from packages.factors.fixtures import demo_fixture

    fixed = demo_fixture(factor_version_id=UUID(int=9), dataset_version_id=UUID(int=8))
    rows = [row.model_dump(mode="json") for row in fixed.rows[-3:]]
    result = {
        "schema_version": "mineagent.rdagent-evaluation-frame.v1",
        "source_id": str(UUID(int=5)),
        "evaluation_id": str(UUID(int=6)),
        "snapshot_id": str(UUID(int=7)),
        "factor_version_id": str(UUID(int=9)),
        "rows": rows,
        "rows_sha256": digest(rows),
        "feedback": "Verified retained fixture rows",
    }

    feedback, parsed = decode_evaluation_frame(
        result, source_id=UUID(int=5), evaluation_ticket=UUID(int=6)
    )
    assert feedback == result["feedback"] and len(parsed) == 3
    result["rows_sha256"] = "0" * 64
    with pytest.raises(RPCError, match="evaluation_rows"):
        decode_evaluation_frame(
            result, source_id=UUID(int=5), evaluation_ticket=UUID(int=6)
        )

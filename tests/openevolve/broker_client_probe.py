"""Installed-runtime client contract probe; mocked transport is not acceptance."""

import asyncio
import hashlib
import json
from unittest.mock import patch
from uuid import uuid4

from openevolve.config import LLMModelConfig

from packages.openevolve_adapter import broker_client as client
from packages.openevolve_adapter.contracts import SearchOperationContext
from packages.research_runtime.control import BrokerAbort


def model():
    return client.init_client(
        LLMModelConfig(
            name="openevolve-fixture.v1",
            api_base=None,
            retries=0,
            max_tokens=32768,
            temperature=0,
        )
    )


def grant():
    context = SearchOperationContext(
        owner_id=uuid4(),
        run_id=uuid4(),
        epoch_id=uuid4(),
        iteration_intent_id=uuid4(),
        native_iteration=1,
        operation_ordinal=0,
        parent_candidate_id=uuid4(),
        parent_source_sha256="a" * 64,
        selection_snapshot_sha256="b" * 64,
        rendered_context_sha256="c" * 64,
    )
    request = {
        "kind": "chat",
        "model": "openevolve-fixture.v1",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "prompt"},
        ],
        "max_output_tokens": 32768,
        "temperature_millionths": 0,
    }
    return client.OperationGrant(
        uuid4(),
        context,
        hashlib.sha256(client._canonical(request)).hexdigest(),
        "d" * 64,
        "e" * 64,
    )


def reply(payload):
    request = json.loads(payload)
    assert request["schema_version"] == "mineagent.openevolve-broker-call.v1"
    return client._canonical(
        {
            "schema_version": "mineagent.openevolve-broker-result.v1",
            "request_sha256": request["request_sha256"],
            "context_sha256": hashlib.sha256(
                client._canonical(request["context"])
            ).hexdigest(),
            "receipt_id": str(uuid4()),
            "content": "a bounded response",
            "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
        }
    )


def aborted(call, code):
    try:
        asyncio.run(call)
    except BrokerAbort as exc:
        assert exc.code == code
    else:
        raise AssertionError("Expected terminal broker abort")


def main():
    instance = model()
    aborted(
        instance.generate_with_context(
            "system", [{"role": "user", "content": "prompt"}]
        ),
        "request_drift",
    )
    with (
        client.bind_operation(grant()),
        patch.object(client, "_exchange", side_effect=reply) as exchange,
    ):
        assert (
            asyncio.run(
                instance.generate_with_context(
                    "system", [{"role": "user", "content": "prompt"}]
                )
            )
            == "a bounded response"
        )
        assert exchange.call_count == 1
        assert instance.last_usage == {
            "prompt_tokens": 10,
            "completion_tokens": 3,
            "total_tokens": 13,
        }
    with (
        client.bind_operation(grant()),
        patch.object(
            client, "_exchange", side_effect=BrokerAbort("uncertain_transport")
        ) as exchange,
    ):
        aborted(
            instance.generate_with_context(
                "system", [{"role": "user", "content": "prompt"}]
            ),
            "uncertain_transport",
        )
        assert exchange.call_count == 1
    with client.bind_operation(grant()), patch.object(client, "_exchange") as exchange:
        aborted(
            instance.generate_with_context(
                "system", [{"role": "user", "content": "x" * 262145}]
            ),
            "budget_exhausted",
        )
        exchange.assert_not_called()

    def forged(payload):
        body = json.loads(reply(payload))
        body["usage"]["prompt_tokens"] = True
        return client._canonical(body)

    with (
        client.bind_operation(grant()),
        patch.object(client, "_exchange", side_effect=forged),
    ):
        aborted(
            instance.generate_with_context(
                "system", [{"role": "user", "content": "prompt"}]
            ),
            "request_drift",
        )
    print(
        "Five installed-runtime broker client contracts passed; transport mocked, no host acceptance claim"
    )


if __name__ == "__main__":
    main()

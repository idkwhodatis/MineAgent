"""Exact protocol dispatch for persisted envelopes; no transport/provider registry."""

from packages.research_runtime import broker_contracts as old
from packages.research_runtime import engine_broker_contracts as engine
from packages.research_runtime.budget_contracts import BudgetVector

NEUTRAL_PROTOCOL = "mineagent.neutral-model.v1"
RD_PROTOCOLS = frozenset(
    {"mineagent.rdagent-chat.v1", "mineagent.rdagent-embedding.v1"}
)


def parse_request(protocol, value):
    if protocol == NEUTRAL_PROTOCOL:
        return old.parse_request(value)
    if protocol not in RD_PROTOCOLS:
        raise ValueError("Unsupported persisted model protocol")
    return engine.parse_request(protocol, value)


def validate_response(protocol, request, response):
    if protocol == NEUTRAL_PROTOCOL:
        return old.validate_response(request, response)
    if protocol not in RD_PROTOCOLS:
        raise ValueError("Unsupported persisted model protocol")
    return engine.validate_response(protocol, request, response)


def request_reservation(protocol, value):
    request = parse_request(protocol, value)
    if request.kind == "chat":
        tokens = (
            sum(len(m.content.encode()) for m in request.messages)
            + request.max_output_tokens
        )
    else:
        tokens = sum(len(item.encode()) for item in request.inputs)
    return BudgetVector(calls=1, tokens=tokens)


def validate_context(value):
    from packages.research_runtime.broker_contracts import FixtureContext, digest

    if type(value) is not dict:
        raise ValueError("Exact context object required")
    if value.get("schema_version") != "mineagent.rdagent-context.v1":
        return FixtureContext.model_validate(value)
    import re

    if set(value) != {
        "schema_version",
        "spec_sha256",
        "input_sha256",
        "logical_step",
        "task_index",
    }:
        raise ValueError("Unsupported RD context fields")
    if any(
        type(value[k]) is not str or re.fullmatch(r"[a-f0-9]{64}", value[k]) is None
        for k in ("spec_sha256", "input_sha256")
    ):
        raise ValueError("Invalid RD context identity")
    if (
        type(value["logical_step"]) is not int
        or not 0 <= value["logical_step"] <= 7
        or type(value["task_index"]) is not int
        or value["task_index"] != 0
    ):
        raise ValueError("Invalid RD context ordinal")
    digest(value)
    return value

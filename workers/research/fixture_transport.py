"""Closed deterministic, network-free transport; not an upstream engine substitute."""

import hashlib
from dataclasses import dataclass

from packages.research_runtime.broker_contracts import (
    ChatResponse,
    EmbeddingResponse,
    FixtureContext,
    canonical_bytes,
    parse_request,
    validate_response,
)
from packages.research_runtime.budget_contracts import BudgetVector


@dataclass(frozen=True)
class FixtureOutcome:
    response: ChatResponse | EmbeddingResponse | None
    usage: BudgetVector


def submit_fixture(
    context: FixtureContext, request, transport_ordinal: int
) -> FixtureOutcome:
    if type(transport_ordinal) is not int or not 1 <= transport_ordinal <= 1000000:
        raise ValueError("Invalid physical transport ordinal")
    request = parse_request(request.model_dump(mode="json"))
    if (context.scenario == "embedding") != (request.kind == "embedding"):
        raise ValueError("Unknown fixture operation purpose")
    inputs = (
        [m.content for m in request.messages]
        if request.kind == "chat"
        else request.inputs
    )
    input_usage = sum((len(value.encode()) + 3) // 4 for value in inputs)
    if context.scenario == "retry" and transport_ordinal <= 2:
        return FixtureOutcome(None, BudgetVector(calls=1, tokens=input_usage))
    if request.kind == "chat":
        response = ChatResponse(content="x" * min(16, request.max_output_tokens))
        usage = BudgetVector(calls=1, tokens=input_usage + len(response.content))
    else:
        vectors = tuple(
            tuple(
                int.from_bytes(
                    hashlib.sha256((str(i) + ":" + value).encode()).digest()[:4], "big"
                )
                / 2**32
                for i in range(request.dimensions)
            )
            for value in request.inputs
        )
        response = EmbeddingResponse(vectors=vectors)
        usage = BudgetVector(calls=1, tokens=input_usage)
    validate_response(request, response)
    canonical_bytes(response)
    return FixtureOutcome(response, usage)

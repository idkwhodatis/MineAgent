"""024 exact OE protocol successor; all historical dispatch remains unchanged."""

from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.contracts import SearchOperationContext
from packages.rdagent_adapter import persisted_broker as prior
from packages.research_runtime import engine_broker_contracts as engine
from packages.research_runtime.budget_contracts import BudgetVector

PROTOCOL = "mineagent.openevolve-chat.v1"


def parse_request(protocol, value):
    return (
        engine.parse_request(protocol, value)
        if protocol == PROTOCOL
        else prior.parse_request(protocol, value)
    )


def validate_response(protocol, request, response):
    return (
        engine.validate_response(protocol, request, response)
        if protocol == PROTOCOL
        else prior.validate_response(protocol, request, response)
    )


def request_reservation(protocol, value):
    if protocol != PROTOCOL:
        return prior.request_reservation(protocol, value)
    request = parse_request(protocol, value)
    return BudgetVector(
        calls=1,
        tokens=sum(len(m.content.encode()) for m in request.messages)
        + request.max_output_tokens,
    )


def validate_context(value):
    if (
        type(value) is dict
        and value.get("schema_version") == "mineagent.openevolve-context.v1"
    ):
        parsed = SearchOperationContext.model_validate_json(canonical(value))
        if parsed.model_dump(mode="json") != value:
            raise ValueError("full exact OE context")
        return parsed
    return prior.validate_context(value)

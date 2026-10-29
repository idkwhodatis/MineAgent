"""Explicitv11 ledger dispatch with real strict request/binding, no physical claim."""

import copy

import pytest

from packages.openevolve_structured.provider_accounting import request_reservation
from tests.openevolve.test_backup_accounting import graph, validate
from tests.openevolve.test_structured_provider_accounting import priced


def normal_graph(completed=False):
    tables = graph(completed=completed)
    binding, request, _ = priced()
    operation = tables["research_model_operations"][0]
    context = tables["research_operation_contexts"][0]
    request = request.model_copy(update={"context_sha256": context["context_sha256"]})
    amount = request_reservation(request, binding).model_dump()
    operation.update(
        protocol=request.schema_version,
        request=request.model_dump(mode="json"),
        request_sha256=request.sha256,
        request_object_kind="oe_structured_provider_request",
    )
    tables["research_objects"][0].update(
        object_kind="oe_structured_provider_request", sha256=request.sha256
    )
    tables["research_budget_reservations"][0]["amounts"] = amount
    control = tables["research_budget_controls"][0]
    control["limits"] = amount
    control["used" if completed else "reserved"] = amount
    if completed:
        tables["research_budget_settlements"][0].update(
            charge=amount, outcome="conservative_unknown"
        )
        tables["research_model_outcomes"][0].update(
            usage=amount, response_object_kind="oe_structured_provider_response"
        )
        tables["research_objects"][1]["object_kind"] = "oe_structured_provider_response"
    return tables, {operation["run_id"]: binding}


def check(tables, bindings, **kwargs):
    validate(
        tables,
        rd_source=True,
        openevolve=True,
        rd_provider=True,
        oe_structured=True,
        model_bindings=bindings,
        **kwargs,
    )


@pytest.mark.parametrize("completed", [False, True])
def test_exact_v11_new_provider_and_full_conservative_ledger(completed):
    tables, bindings = normal_graph(completed)
    check(tables, bindings)


def test_v10_and_older_do_not_acquire_normal_provider_protocol():
    tables, bindings = normal_graph()
    with pytest.raises(
        ValueError, match="Historical accounting cannot acquire structured"
    ):
        validate(
            tables,
            rd_source=True,
            openevolve=True,
            rd_provider=True,
            model_bindings=bindings,
        )


@pytest.mark.parametrize(
    "change", ["refund", "rd_kind", "missing_binding", "wrong_request_hash", "retry"]
)
def test_wrong_cost_binding_kind_or_retry_is_not_accepted(change):
    tables, bindings = normal_graph(True)
    if change == "refund":
        tables["research_budget_settlements"][0]["outcome"] = "measured"
    elif change == "rd_kind":
        tables["research_model_outcomes"][0]["response_object_kind"] = "model_response"
        tables["research_objects"][1]["object_kind"] = "model_response"
    elif change == "missing_binding":
        bindings = {}
    elif change == "wrong_request_hash":
        tables["research_model_operations"][0]["request_sha256"] = "0" * 64
    else:
        extra = copy.deepcopy(tables["research_model_transports"][0])
        extra.update(id="other", ordinal=2)
        tables["research_model_transports"].append(extra)
    with pytest.raises(ValueError):
        check(tables, bindings)


@pytest.mark.parametrize("flag", [None, 1, "true"])
def test_flag_is_exact_bool(flag):
    tables, bindings = normal_graph()
    with pytest.raises(ValueError, match="exact v11"):
        validate(
            tables,
            rd_source=True,
            openevolve=True,
            rd_provider=True,
            oe_structured=flag,
            model_bindings=bindings,
        )


@pytest.mark.parametrize(
    "field,value", [("purpose", "continuation"), ("ordinal", 1), ("ordinal", False)]
)
def test_normal_provider_is_exact_chat_zero_ordinal(field, value):
    tables, bindings = normal_graph()
    tables["research_model_operations"][0][field] = value
    with pytest.raises(ValueError, match="exact rendered operation"):
        check(tables, bindings)


def test_rehashed_request_cannot_select_another_context():
    from packages.factors.contracts import canonical_bytes
    from packages.openevolve_structured.provider_contracts import OEChatRequest

    tables, bindings = normal_graph()
    op = tables["research_model_operations"][0]
    op["request"]["context_sha256"] = "f" * 64
    request = OEChatRequest.model_validate_json(canonical_bytes(op["request"]))
    op["request_sha256"] = request.sha256
    tables["research_objects"][0]["sha256"] = request.sha256
    with pytest.raises(ValueError, match="exact rendered operation"):
        check(tables, bindings)

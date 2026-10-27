"""Real typed accounting graphs; no database, transport or response-byte claim."""

from uuid import uuid4

import pytest

from backend.app.research_broker_services import validate_accounting_closure
from packages.openevolve_adapter.contracts import SearchOperationContext
from packages.openevolve_adapter.persisted_broker import request_reservation
from packages.research_runtime.broker_contracts import (
    ChatRequest,
    FixtureContext,
    Message,
    digest,
)
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.engine_broker_contracts import (
    OEChatRequest,
    OEMessage,
    RDChatRequest,
    RDMessage,
    request_sha256,
)


def graph(flavor="oe", *, completed=False, large=False):
    scope = {
        "owner_id": str(uuid4()),
        "run_id": str(uuid4()),
        "attempt_id": str(uuid4()),
        "generation": 1,
    }
    protocol = {
        "oe": "mineagent.openevolve-chat.v1",
        "rd": "mineagent.rdagent-chat.v1",
        "neutral": "mineagent.neutral-model.v1",
    }[flavor]
    if flavor == "oe":
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
        ).model_dump(mode="json")
        context.update(owner_id=scope["owner_id"], run_id=scope["run_id"])
        req = OEChatRequest(
            messages=(
                OEMessage(role="system", content="s" * (150000 if large else 1)),
                OEMessage(role="user", content="u" * (150000 if large else 1)),
            )
        )
    elif flavor == "rd":
        context = {
            "schema_version": "mineagent.rdagent-context.v1",
            "spec_sha256": "a" * 64,
            "input_sha256": "b" * 64,
            "logical_step": 0,
            "task_index": 0,
        }
        req = RDChatRequest(
            messages=(RDMessage(role="user", content="hello"),),
            max_output_tokens=16,
            response_format=None,
        )
    else:
        context = FixtureContext(scenario="chat", ordinal=0).model_dump(mode="json")
        req = ChatRequest(
            messages=(Message(role="user", content="hello"),), max_output_tokens=16
        )
    raw = req.model_dump(mode="json")
    sha = digest(req) if flavor == "neutral" else request_sha256(protocol, req)
    amount = request_reservation(protocol, raw).model_dump()
    names = (
        "research_budget_controls",
        "research_budget_reservations",
        "research_budget_settlements",
        "research_operation_contexts",
        "research_context_aborts",
        "research_model_operations",
        "research_model_transports",
        "research_model_outcomes",
        "research_objects",
    )
    tables = {name: [] for name in names}
    cid, oid, rid, tid, request_id = [str(uuid4()) for _ in range(5)]
    tables["research_budget_controls"] = [
        {
            "id": str(uuid4()),
            "owner_id": scope["owner_id"],
            "run_id": scope["run_id"],
            "limits": amount,
            "used": amount if completed else BudgetVector().model_dump(),
            "reserved": BudgetVector().model_dump() if completed else amount,
        }
    ]
    tables["research_budget_reservations"] = [
        dict(
            id=rid,
            **scope,
            purpose="model_transport",
            logical_key="physical:1",
            amounts=amount,
            state="settled" if completed else "reserved",
        )
    ]
    tables["research_operation_contexts"] = [
        dict(id=cid, **scope, context=context, context_sha256=digest(context))
    ]
    tables["research_model_operations"] = [
        dict(
            id=oid,
            **scope,
            context_id=cid,
            protocol=protocol,
            request=raw,
            request_sha256=sha,
            purpose="chat",
            ordinal=0,
            state="completed" if completed else "open",
            request_object_id=request_id,
            request_object_kind="model_request",
        )
    ]
    tables["research_model_transports"] = [
        dict(
            id=tid,
            **scope,
            operation_id=oid,
            reservation_id=rid,
            ordinal=1,
            state="completed" if completed else "reserved",
        )
    ]
    tables["research_objects"] = [
        {
            "id": request_id,
            "owner_id": scope["owner_id"],
            "object_kind": "model_request",
            "sha256": sha,
        }
    ]
    if completed:
        response_id = str(uuid4())
        tables["research_budget_settlements"] = [
            dict(
                id=str(uuid4()),
                **scope,
                reservation_id=rid,
                charge=amount,
                outcome="measured",
            )
        ]
        tables["research_model_outcomes"] = [
            dict(
                id=str(uuid4()),
                **scope,
                transport_id=tid,
                usage=amount,
                outcome="completed",
                response_object_id=response_id,
                response_object_kind="model_response",
                response_sha256="d" * 64,
            )
        ]
        tables["research_objects"].append(
            {
                "id": response_id,
                "owner_id": scope["owner_id"],
                "object_kind": "model_response",
                "sha256": "d" * 64,
            }
        )
    return tables


def validate(tables, **flags):
    validate_accounting_closure(
        tables,
        {name: {row["id"]: row for row in rows} for name, rows in tables.items()},
        **flags,
    )


@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("flavor", ["neutral", "rd", "oe"])
def test_v8_preserves_prior_protocols_and_accepts_exact_oe(completed, flavor):
    validate(graph(flavor, completed=completed), rd_source=True, openevolve=True)


@pytest.mark.parametrize("flags", [{}, {"rd_source": True}])
def test_historical_versions_reject_oe_context(flags):
    with pytest.raises(ValueError):
        validate(graph(), **flags)


@pytest.mark.parametrize("flags", [{}, {"rd_source": True}])
def test_oe_request_cannot_sneak_through_historical_fixture_context(flags):
    tables = graph()
    context = tables["research_operation_contexts"][0]
    context["context"] = FixtureContext(scenario="chat", ordinal=0).model_dump(
        mode="json"
    )
    context["context_sha256"] = digest(context["context"])
    with pytest.raises(ValueError):
        validate(tables, **flags)


def test_rd_transport_uses_rd_reservation_not_neutral_parser():
    validate(graph("rd"), rd_source=True)
    with pytest.raises(ValueError):
        validate(graph("rd"))


def test_valid_large_oe_wire_uses_engine_hash_limit_not_neutral_limit():
    tables = graph(large=True)
    with pytest.raises(ValueError, match="byte bound"):
        digest(tables["research_model_operations"][0]["request"])
    validate(tables, rd_source=True, openevolve=True)


@pytest.mark.parametrize(
    "change", ["cost", "tokens", "request_hash", "response_hash", "scope"]
)
def test_oe_dispatch_preserves_exact_accounting_and_object_checks(change):
    tables = graph(completed=True)
    if change in {"cost", "tokens"}:
        field = "cost_microunits" if change == "cost" else "tokens"
        for target in (
            tables["research_budget_reservations"][0]["amounts"],
            tables["research_budget_controls"][0]["limits"],
            tables["research_budget_controls"][0]["used"],
            tables["research_budget_settlements"][0]["charge"],
            tables["research_model_outcomes"][0]["usage"],
        ):
            target[field] += 1
    elif change == "request_hash":
        tables["research_model_operations"][0]["request_sha256"] = "f" * 64
    elif change == "response_hash":
        tables["research_model_outcomes"][0]["response_sha256"] = "f" * 64
    else:
        tables["research_model_transports"][0]["attempt_id"] = str(uuid4())
    with pytest.raises(ValueError):
        validate(tables, rd_source=True, openevolve=True)


@pytest.mark.parametrize(
    "flags",
    [{"openevolve": True}, {"openevolve": 1, "rd_source": True}, {"rd_source": "yes"}],
)
def test_version_flags_are_exact_and_successor_only(flags):
    with pytest.raises(ValueError):
        validate(graph("neutral"), **flags)


def test_versioned_closure_requires_explicit_protocol_but_v1_has_no_column():
    tables = graph("neutral")
    del tables["research_model_operations"][0]["protocol"]
    validate(tables)
    with pytest.raises(ValueError):
        validate(tables, rd_source=True)
    with pytest.raises(ValueError):
        validate(tables, rd_source=True, openevolve=True)

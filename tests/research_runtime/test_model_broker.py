"""Pure contracts and accounting closure; real PG tests are separately required."""

import copy
import pickle
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.app.research_broker_services import (
    request_reservation,
    validate_accounting_closure,
)
from packages.research_runtime.broker_contracts import (
    ChatRequest,
    EmbeddingRequest,
    FixtureContext,
    Message,
    canonical_bytes,
    digest,
    parse_request,
)
from packages.research_runtime.budget_contracts import BudgetVector, validate_ledger
from packages.research_runtime.control import BrokerAbort
from workers.research.fixture_transport import submit_fixture


def request():
    return ChatRequest(
        messages=(Message(role="user", content="input"),), max_output_tokens=16
    )


@pytest.mark.parametrize("value", [True, -1, 1.2, "2", None, 2**53])
def test_budget_units_reject_coercion_negative_and_unbounded(value):
    with pytest.raises(ValidationError):
        BudgetVector(calls=value)


def test_budget_vector_enforces_conservation_and_exact_keys():
    one = BudgetVector(calls=1)
    assert one.add(one).calls == 2
    assert one.subtract(one) == BudgetVector()
    with pytest.raises(ValidationError):
        BudgetVector(credit=1)
    with pytest.raises(ValueError):
        validate_ledger(one.model_dump(), one.model_dump(), one.model_dump())
    with pytest.raises(ValidationError):
        BudgetVector().subtract(one)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(provider="external"),
        lambda r: r.update(model="unknown"),
        lambda r: r.update(max_output_tokens=True),
        lambda r: r.update(max_output_tokens=0),
        lambda r: r.update(messages=[{"role": "tool", "content": "x"}]),
        lambda r: r.update(messages=[{"role": "user", "content": ""}]),
        lambda r: r.update(messages=[{"role": "user", "content": "x", "secret": "no"}]),
    ],
)
def test_chat_request_is_closed_and_bounded(mutate):
    payload = request().model_dump(mode="json")
    mutate(payload)
    with pytest.raises((ValidationError, ValueError)):
        parse_request(payload)


def test_chat_and_embedding_are_distinct_deterministic_envelopes():
    chat = request()
    embedding = EmbeddingRequest(inputs=("alpha", "beta"), dimensions=3)
    assert digest(chat) != digest(embedding)
    response = submit_fixture(
        FixtureContext(scenario="embedding", ordinal=1), embedding, 1
    )
    assert len(response.response.vectors) == 2
    assert all(len(row) == 3 for row in response.response.vectors)
    assert response == submit_fixture(
        FixtureContext(scenario="embedding", ordinal=1), embedding, 1
    )
    assert request_reservation(embedding).covers(response.usage)
    with pytest.raises(ValueError):
        submit_fixture(FixtureContext(scenario="chat", ordinal=1), embedding, 1)


def test_each_identical_failed_physical_attempt_is_observable():
    req = request()
    context = FixtureContext(scenario="retry", ordinal=1)
    first, second, completed = (submit_fixture(context, req, i) for i in (1, 2, 3))
    assert first.response is second.response is None
    assert first.usage.calls == second.usage.calls == completed.usage.calls == 1
    assert completed.response is not None
    assert request_reservation(req).covers(completed.usage)


def test_terminal_abort_bypasses_exception_fallback_and_roundtrips_pickle():
    caught = False
    with pytest.raises(BrokerAbort, match="budget_exhausted"):
        try:
            raise pickle.loads(pickle.dumps(BrokerAbort("budget_exhausted")))
        except Exception:  # noqa: BLE001 -- prove upstream fallback cannot swallow abort
            caught = True
    assert not caught


def _closure():
    owner, run, attempt, reservation, control = [str(uuid4()) for _ in range(5)]
    bound = {"owner_id": owner, "run_id": run, "attempt_id": attempt, "generation": 1}
    amount = BudgetVector(calls=1).model_dump()
    tables = {
        name: []
        for name in (
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
    }
    tables["research_budget_controls"] = [
        {
            "id": control,
            "owner_id": owner,
            "run_id": run,
            "limits": amount,
            "used": BudgetVector().model_dump(),
            "reserved": amount,
        }
    ]
    tables["research_budget_reservations"] = [
        dict(
            id=reservation,
            **bound,
            purpose="model_transport",
            logical_key="test",
            amounts=amount,
            state="uncertain",
        )
    ]
    return tables


def _validate(tables):
    validate_accounting_closure(
        tables, {name: {r["id"]: r for r in rows} for name, rows in tables.items()}
    )


def test_closure_preserves_unknown_reservation_instead_of_refunding():
    tables = _closure()
    _validate(tables)
    tables["research_budget_controls"][0]["reserved"] = BudgetVector().model_dump()
    with pytest.raises(ValueError, match="conserve"):
        _validate(tables)


@pytest.mark.parametrize(
    "change",
    ["over_cap", "negative", "extra", "settled_without_receipt", "missing_budget"],
)
def test_closure_rejects_inconsistent_accounting(change):
    tables = _closure()
    if change == "over_cap":
        tables["research_budget_controls"][0]["limits"] = BudgetVector().model_dump()
    elif change == "negative":
        tables["research_budget_reservations"][0]["amounts"]["calls"] = -1
    elif change == "extra":
        tables["research_budget_reservations"][0]["amounts"]["credit"] = 1
    elif change == "settled_without_receipt":
        tables["research_budget_reservations"][0]["state"] = "settled"
    else:
        tables["research_budget_controls"] = []
    with pytest.raises(ValueError):
        _validate(tables)


def test_closure_rejects_fabricated_process_refund():
    tables = _closure()
    reservation = tables["research_budget_reservations"][0]
    reservation.update(state="settled", purpose="process_unit")
    tables["research_budget_controls"][0]["reserved"] = BudgetVector().model_dump()
    tables["research_budget_settlements"] = [
        dict(
            id=str(uuid4()),
            **{
                k: reservation[k]
                for k in ("owner_id", "run_id", "attempt_id", "generation")
            },
            reservation_id=reservation["id"],
            charge=BudgetVector().model_dump(),
            outcome="not_submitted",
        )
    ]
    with pytest.raises(ValueError, match="refund"):
        _validate(tables)


def test_canonical_evidence_rejects_nonfinite_json_and_is_byte_stable():
    assert canonical_bytes(request()) == canonical_bytes(
        copy.deepcopy(request().model_dump(mode="json"))
    )
    with pytest.raises(ValueError):
        canonical_bytes({"value": float("nan")})


@pytest.mark.parametrize("invalid", [False, "duplicate", "reason", "wrong_attempt"])
def test_closure_preserves_empty_context_abort_and_rejects_forgery(invalid):
    tables = _closure()
    source = tables["research_budget_reservations"][0]
    bound = {k: source[k] for k in ("owner_id", "run_id", "attempt_id", "generation")}
    context = FixtureContext(scenario="cancellation", ordinal=0)
    identifier = str(uuid4())
    tables["research_operation_contexts"].append(
        {
            "id": identifier,
            **bound,
            "context": context.model_dump(mode="json"),
            "context_sha256": digest(context),
        }
    )
    abort = {
        "id": str(uuid4()),
        **bound,
        "context_id": identifier,
        "reason": "cancelled",
    }
    tables["research_context_aborts"].append(abort)
    if invalid == "duplicate":
        tables["research_context_aborts"].append({**abort, "id": str(uuid4())})
    elif invalid == "reason":
        abort["reason"] = "refund_now"
    elif invalid == "wrong_attempt":
        abort["attempt_id"] = str(uuid4())
    if invalid:
        with pytest.raises(ValueError):
            _validate(tables)
    else:
        _validate(tables)

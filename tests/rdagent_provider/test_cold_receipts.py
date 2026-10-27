"""Component uses retained receipt bytes; synthetic records are not PG acceptance."""

from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from test_accounting_receipts import chain, pair

from backend.app.rdagent_provider_backup import validate_receipts
from packages.rdagent_provider.accounting import request_reservation
from packages.rdagent_provider.codec import request_sha256
from packages.rdagent_provider.object_identity import object_identity
from packages.rdagent_provider.process_receipts import OBJECT_KIND, receipt_bytes


@pytest.fixture
def graph():
    binding, request = pair()
    docs = tuple(
        type(doc).model_validate(
            {
                **doc.model_dump(),
                "request_sha256": request_sha256(request.schema_version, request),
                "model_binding_sha256": binding.sha256,
            }
        )
        for doc in chain()
    )
    scope = {
        key: str(getattr(docs[0], key)) if key != "generation" else 1
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    }
    tid, oid, rid = str(docs[0].transport_id), str(uuid4()), str(uuid4())
    held = request_reservation(request, binding).model_dump()
    values = {
        "transports": {
            tid: {
                "id": tid,
                **scope,
                "operation_id": oid,
                "reservation_id": rid,
                "state": "completed",
            }
        },
        "operations": {
            oid: {
                "id": oid,
                **scope,
                "protocol": request.schema_version,
                "request": request.model_dump(mode="json"),
                "request_sha256": docs[0].request_sha256,
            }
        },
        "objects": {},
        "configs": {
            scope["run_id"]: SimpleNamespace(
                model_profile="configured.rdagent.v1", model_binding=binding
            )
        },
        "reservations": {
            rid: {
                "id": rid,
                **scope,
                "purpose": "model_transport",
                "state": "settled",
                "amounts": held,
            }
        },
        "settlements": {
            "settlement": {
                **scope,
                "reservation_id": rid,
                "outcome": "conservative_unknown",
                "charge": held,
            }
        },
        "outcomes": {
            "outcome": {
                **scope,
                "transport_id": tid,
                "outcome": "completed",
                "usage": held,
            }
        },
    }
    rows, files = [], {}
    for doc in docs:
        raw = receipt_bytes(doc)
        digest = sha256(raw).hexdigest()
        key = object_identity(UUID(scope["owner_id"]), OBJECT_KIND, digest).storage_key
        object_id = str(uuid4())
        values["objects"][object_id] = {
            "id": object_id,
            "owner_id": scope["owner_id"],
            "object_kind": OBJECT_KIND,
            "sha256": digest,
            "size_bytes": len(raw),
            "storage_key": key,
        }
        rows.append(
            {
                "id": str(uuid4()),
                **scope,
                "transport_id": tid,
                "kind": doc.kind,
                "receipt_object_id": object_id,
                "receipt_object_kind": OBJECT_KIND,
                "receipt_sha256": digest,
                "size_bytes": len(raw),
            }
        )
        files[key] = raw

    def read(key, expected, cap):
        assert cap == 16384
        return files[key]

    values["read_bytes"] = read
    return rows, values, files


def test_positive_stopped_sdk_history_keeps_full_charge(graph):
    rows, values, _ = graph
    validate_receipts(rows, **values)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_started",
        "missing_stopped",
        "no_receipts",
        "tamper",
        "refund",
        "measured",
        "held",
        "wrong_scope",
        "request_bytes",
        "duplicate",
    ],
)
def test_partial_unknown_refunded_or_substituted_sdk_blocks_cold(graph, mutation):
    rows, values, files = graph
    if mutation.startswith("missing_"):
        rows[:] = [
            row for row in rows if row["kind"] != mutation.removeprefix("missing_")
        ]
    elif mutation == "no_receipts":
        rows.clear()
    elif mutation == "tamper":
        files[next(iter(files))] += b" "
    elif mutation == "refund":
        values["settlements"]["settlement"]["charge"] = {
            **values["settlements"]["settlement"]["charge"],
            "tokens": 0,
        }
    elif mutation == "measured":
        values["settlements"]["settlement"]["outcome"] = "measured"
    elif mutation == "held":
        next(iter(values["reservations"].values()))["state"] = "reserved"
    elif mutation == "request_bytes":
        next(iter(values["operations"].values()))["request"]["messages"][0][
            "content"
        ] = "substituted request"
    elif mutation == "wrong_scope":
        rows[0]["run_id"] = str(uuid4())
    else:
        rows.append(deepcopy(rows[0]))
    with pytest.raises(ValueError):
        validate_receipts(rows, **values)


def accounting_graph(graph):
    from packages.factors.contracts import digest
    from packages.research_runtime.budget_contracts import BudgetVector

    _, value, _ = graph
    operation = next(iter(value["operations"].values()))
    transport = next(iter(value["transports"].values()))
    held = next(iter(value["reservations"].values()))
    scope = {
        key: operation[key]
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    }
    context_id, request_id, response_id = (str(uuid4()) for _ in range(3))
    context = {
        "schema_version": "mineagent.rdagent-context.v1",
        "spec_sha256": "a" * 64,
        "input_sha256": "b" * 64,
        "logical_step": 0,
        "task_index": 0,
    }
    operation.update(
        context_id=context_id,
        purpose="chat",
        ordinal=0,
        state="completed",
        request_object_id=request_id,
        request_object_kind="model_request",
    )
    transport["ordinal"] = 1
    held["logical_key"] = "provider:" + transport["id"]
    charge = value["settlements"]["settlement"]
    charge["id"] = str(uuid4())
    outcome = value["outcomes"]["outcome"]
    outcome.update(
        id=str(uuid4()),
        response_object_id=response_id,
        response_object_kind="model_response",
        response_sha256="e" * 64,
    )
    tables = {
        "research_budget_controls": [
            {
                "id": str(uuid4()),
                "owner_id": scope["owner_id"],
                "run_id": scope["run_id"],
                "limits": held["amounts"],
                "used": held["amounts"],
                "reserved": BudgetVector().model_dump(),
            }
        ],
        "research_budget_reservations": [held],
        "research_budget_settlements": [charge],
        "research_operation_contexts": [
            {
                "id": context_id,
                **scope,
                "context": context,
                "context_sha256": digest(context),
            }
        ],
        "research_context_aborts": [],
        "research_model_operations": [operation],
        "research_model_transports": [transport],
        "research_model_outcomes": [outcome],
        "research_objects": [
            {
                "id": request_id,
                "owner_id": scope["owner_id"],
                "object_kind": "model_request",
                "sha256": operation["request_sha256"],
            },
            {
                "id": response_id,
                "owner_id": scope["owner_id"],
                "object_kind": "model_response",
                "sha256": "e" * 64,
            },
        ],
    }
    indexes = {name: {row["id"]: row for row in rows} for name, rows in tables.items()}
    bindings = {scope["run_id"]: value["configs"][scope["run_id"]].model_binding}
    return tables, indexes, bindings


def test_explicit_provider_accounting_preserves_full_charge_without_measured_bill(
    graph,
):
    from backend.app.research_broker_services import validate_accounting_closure

    tables, indexes, bindings = accounting_graph(graph)
    validate_accounting_closure(
        tables,
        indexes,
        rd_source=True,
        openevolve=True,
        rd_provider=True,
        model_bindings=bindings,
    )
    with pytest.raises(ValueError):
        validate_accounting_closure(tables, indexes, rd_source=True, openevolve=True)


@pytest.mark.parametrize("mutation", ["measured", "missing_binding", "wrong_request"])
def test_provider_accounting_does_not_widen_historical_or_detached_bindings(
    graph, mutation
):
    from backend.app.research_broker_services import validate_accounting_closure

    tables, indexes, bindings = accounting_graph(graph)
    if mutation == "measured":
        tables["research_budget_settlements"][0]["outcome"] = "measured"
    elif mutation == "missing_binding":
        bindings.clear()
    else:
        tables["research_model_operations"][0]["request"]["model"] = "another-model"
    with pytest.raises(ValueError):
        validate_accounting_closure(
            tables,
            indexes,
            rd_source=True,
            openevolve=True,
            rd_provider=True,
            model_bindings=bindings,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "missing",
        "duplicate",
        "foreign",
        "legacy_alias",
        "no_transport",
        "retry_transport",
    ],
)
def test_every_provider_operation_has_exactly_one_scoped_call(graph, mutation):
    from backend.app.rdagent_provider_backup import validate_operation_membership

    _, values, _ = graph
    operation = next(iter(values["operations"].values()))
    member = {
        key: operation[key]
        for key in ("owner_id", "run_id", "attempt_id", "generation", "protocol")
    }
    member["operation_id"] = operation["id"]
    members = [member]
    if mutation == "missing":
        members.clear()
    elif mutation == "duplicate":
        members.append(deepcopy(member))
    elif mutation == "foreign":
        member["owner_id"] = str(uuid4())
    elif mutation == "legacy_alias":
        member["protocol"] = "mineagent.rdagent-chat.v1"
    if mutation == "no_transport":
        values["transports"].clear()
    elif mutation == "retry_transport":
        values["transports"][str(uuid4())] = deepcopy(
            next(iter(values["transports"].values()))
        )
    if mutation is None:
        validate_operation_membership(
            values["operations"], members, values["transports"]
        )
    else:
        with pytest.raises(ValueError):
            validate_operation_membership(
                values["operations"], members, values["transports"]
            )


@pytest.mark.parametrize("count", [32, 33])
def test_provider_profile_has_a_fixed_operation_ceiling_not_user_budget(graph, count):
    from backend.app.rdagent_provider_backup import validate_operation_membership

    _, values, _ = graph
    original = next(iter(values["operations"].values()))
    operation_rows, transports, members = {}, {}, []
    for ordinal in range(count):
        key = str(uuid4())
        operation = {**original, "id": key}
        operation_rows[key] = operation
        members.append({**operation, "operation_id": key})
        transports[str(uuid4())] = {**operation, "operation_id": key}
    if count == 32:
        validate_operation_membership(operation_rows, members, transports)
    else:
        with pytest.raises(ValueError, match="at most 32"):
            validate_operation_membership(operation_rows, members, transports)

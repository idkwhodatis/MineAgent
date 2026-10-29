"""Complete synthetic retained graph, not real process or financial evidence.

The inert broker supplies native-shaped rows. Production reconstruction, closed
operator, accounting codec, result projection and cold validator all run unmocked.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest

from backend.app import structured_factor_backup as backup
from backend.app.structured_factor_results import result_graph
from packages.factors.contracts import canonical_bytes, digest
from packages.research_runtime.process_declarations_v2 import ProcessDeclarationV2
from packages.structured_factor_strategy.operator import ClosedExchange
from packages.structured_factor_strategy.wire import (
    StructuredResult,
    accounting_only,
    read_result,
    write_input,
)
from runtimes.portable_engine.export import export_result
from runtimes.portable_engine.runtime import Controller, load_inputs, to_ns
from runtimes.structured_factor_engine.journal import (
    StructuredInputBinding,
    StructuredIntent,
)
from tests.portable.test_engine import FakeAQ, FakeBroker, fake_result
from tests.structured.test_backup_exposure_binding import material

pytest_plugins = ["tests.structured.test_reconstruction"]


def completed(retained):
    args, files, inputs = retained
    closure, kwargs, _ = material(retained)
    # Reconstruct the exact market, not a replacement unrelated accounting input.
    from backend.app.structured_factor_reconstruction import reconstruct_input

    _, market, _, _ = reconstruct_input(**args)

    def fixture_calendar(manifest, sessions):
        assert manifest.calendar == "XNYS"
        assert [str(s.session_date) for s in sessions] == [
            "2024-03-12",
            "2024-03-13",
            "2024-03-14",
        ]

    native = load_inputs(
        canonical_bytes(inputs.engine), market, calendar_verifier=fixture_calendar
    )
    exchange = ClosedExchange(inputs)
    controller = Controller(native, exchange)
    broker = FakeBroker(native.sessions[0])
    fills = []
    for session in native.sessions:
        broker.session = session
        for order in broker.orders.values():
            if order.status == "Accepted":
                order.status = "Filled"
                order.filled_quantity = order.quantity
                order.updated_at = to_ns(session.close_time)
                order.commission = 1.0
                broker.positions[order.symbol] = broker.available[order.symbol] = (
                    order.quantity
                )
                fills.append(
                    NS(
                        id="fill:" + order.id,
                        order_id=order.id,
                        symbol=order.symbol,
                        side=order.side,
                        quantity=order.quantity,
                        price=100.0,
                        commission=1.0,
                        timestamp=order.updated_at,
                    )
                )
        controller.on_session(session, broker, FakeAQ)
    result = StructuredResult(
        execution_id=inputs.execution_id,
        structured_strategy_version_id=inputs.structured_strategy_version_id,
        snapshot_id=inputs.snapshot.snapshot_id,
        strategy_input_sha256=hashlib.sha256(write_input(inputs)).hexdigest(),
        market_sha256=inputs.market_sha256,
        decisions=tuple(exchange.records),
        accounting_tables=accounting_only(
            export_result(fake_result(controller, broker, fills), controller)
        ),
    )
    raw = canonical_bytes(result)
    assert read_result(raw, inputs) == result
    execution = closure["tables"]["factor_strategy_executions"][0]
    scope = {
        k: execution[k] for k in ("owner_id", "run_id", "attempt_id", "generation")
    }
    binding = StructuredInputBinding.model_validate_json(
        canonical_bytes(
            {
                **{
                    k: execution[k]
                    for k in (
                        "structured_strategy_version_id",
                        "snapshot_id",
                        "strategy_input_sha256",
                        "strategy_input_size_bytes",
                        "market_sha256",
                        "market_size_bytes",
                    )
                },
                "execution_id": execution["id"],
            }
        )
    )
    admission = ProcessDeclarationV2.model_validate_json(
        canonical_bytes(
            dict(
                schema_version="mineagent.process-admission.v2",
                id=str(uuid4()),
                **scope,
                role="native_evaluator",
                logical_operation_id=execution["id"],
                launch_ordinal=1,
                profile="mineagent.factor-strategy-engine.v1",
                input_sha256=digest(binding),
                reservation_id=str(uuid4()),
                image_id=args["spec"]["config"]["engine_image_id"],
                ipc={"ipc_policy": "none", "ticket_sha256": None},
            )
        )
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    intent = StructuredIntent(
        id=admission.id,
        admission=admission,
        inputs=binding,
        issued_at=now,
        not_after=now + timedelta(seconds=60),
    )
    research = kwargs["research_closure"]["tables"]
    ad = admission.model_dump(mode="json")
    ad.pop("ipc")
    ad.update(ipc_policy="none", ticket_sha256=None)
    research["research_process_admissions"] = [ad]
    research["research_process_states"] = [
        dict(admission_id=ad["id"], **scope, status="stopped")
    ]
    amounts = {"wall_milliseconds": 90000}
    research["research_budget_reservations"] = [
        dict(id=ad["reservation_id"], **scope, state="settled", amounts=amounts)
    ]
    research["research_budget_settlements"] = [
        dict(
            reservation_id=ad["reservation_id"],
            **scope,
            outcome="conservative_unknown",
            charge=amounts,
        )
    ]
    receipts = []
    for kind in ("launch_intent", "launch_identity", "stop", "extraction"):
        payload = (
            intent.model_dump(mode="json")
            if kind == "launch_intent"
            else {"sha256": digest(result), "size_bytes": len(raw)}
            if kind == "extraction"
            else {"synthetic": kind}
        )
        receipts.append(
            dict(
                id=str(uuid4()),
                admission_id=ad["id"],
                **scope,
                kind=kind,
                payload=payload,
                payload_sha256=digest(payload),
            )
        )
    research["research_process_receipts"] = receipts
    outcome = dict(
        execution_id=execution["id"],
        **scope,
        admission_id=ad["id"],
        result=result.model_dump(mode="json"),
        result_canonical=raw.decode(),
        result_sha256=digest(result),
        result_size_bytes=len(raw),
        decision_count=len(result.decisions),
        created_at=now.isoformat(),
    )
    for row in receipts[1:]:
        outcome.update(
            {
                row["kind"] + "_receipt_id": row["id"],
                row["kind"] + "_kind": row["kind"],
                row["kind"] + "_sha256": row["payload_sha256"],
            }
        )
    closure["tables"]["factor_strategy_execution_outcomes"] = [outcome]
    files[f"research-processes/{scope['owner_id']}/{ad['id']}/output/result.json"] = raw
    fence = NS(**{k: UUID(v) if k != "generation" else v for k, v in scope.items()})
    for table, rows in zip(
        (
            "factor_strategy_decisions",
            "factor_strategy_orders",
            "factor_strategy_fills",
        ),
        result_graph(fence, result),
        strict=True,
    ):
        closure["tables"][table] = [
            {k: str(v) if isinstance(v, UUID) else v for k, v in row.items()}
            for row in rows
        ]
        assert rows
    closure["external_bindings"]["research_runs"][0]["status"] = "succeeded"
    kwargs["research_closure"]["external_bindings"]["runs"][0]["status"] = "succeeded"
    return closure, kwargs


def test_complete_result_graph_reconstructs_all_three_record_tables(
    retained_declaration,
):
    closure, kwargs = completed(retained_declaration)
    result = backup.validate_closure(closure, **kwargs)
    assert all(
        result[k]
        for k in (
            "factor_strategy_decisions",
            "factor_strategy_orders",
            "factor_strategy_fills",
        )
    )


@pytest.mark.parametrize(
    "fault", ["decision", "order", "fill", "charge", "result_bytes", "orphan"]
)
def test_completed_graph_rejects_mutated_or_unsettled_history(
    retained_declaration, fault
):
    closure, kwargs = completed(retained_declaration)
    if fault in ("decision", "order", "fill"):
        table = {
            "decision": "factor_strategy_decisions",
            "order": "factor_strategy_orders",
            "fill": "factor_strategy_fills",
        }[fault]
        closure["tables"][table][0]["record_sha256"] = "f" * 64
    elif fault == "charge":
        kwargs["research_closure"]["tables"]["research_budget_settlements"][0][
            "charge"
        ] = {}
    elif fault == "result_bytes":
        key = next(
            k for k in retained_declaration[1] if k.endswith("/output/result.json")
        )
        retained_declaration[1][key] += b" "
    else:
        closure["tables"]["factor_strategy_fills"][0]["execution_id"] = str(uuid4())
    with pytest.raises((ValueError, KeyError)):
        backup.validate_closure(closure, **kwargs)


@pytest.mark.parametrize("table_index", [0, 1, 2])
@pytest.mark.parametrize("fault", ["identity_string", "identity_object", "nested_uuid"])
def test_projection_normalizes_only_declared_typed_identity_columns(
    retained_declaration, monkeypatch, table_index, fault
):
    closure, kwargs = completed(retained_declaration)
    original = backup.result_graph

    def corrupted(fence, result):
        projected = original(fence, result)
        row = projected[table_index][0]
        if fault == "identity_string":
            row["execution_id"] = str(row["execution_id"])
        elif fault == "identity_object":
            row["execution_id"] = object()
        else:
            row["record"]["undeclared_uuid"] = uuid4()
        return projected

    monkeypatch.setattr(backup, "result_graph", corrupted)
    with pytest.raises((TypeError, ValueError)):
        backup.validate_closure(closure, **kwargs)


def test_completed_reverse_inventory_rejects_extra_orphan_outcome(retained_declaration):
    from copy import deepcopy

    closure, kwargs = completed(retained_declaration)
    extra = deepcopy(closure["tables"]["factor_strategy_execution_outcomes"][0])
    extra["execution_id"] = str(uuid4())
    closure["tables"]["factor_strategy_execution_outcomes"].append(extra)
    with pytest.raises(ValueError, match="Orphan structured outcome"):
        backup.validate_closure(closure, **kwargs)

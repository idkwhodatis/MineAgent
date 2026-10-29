"""Actual immutable SDK files plus bounded DB doubles, no PID/Docker claim."""

import json
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_provider_containment as service
from packages.openevolve_structured.object_identity import identity_for_content
from tests.openevolve.test_structured_provider_evidence import fixture


@pytest.fixture
def case(monkeypatch, tmp_path):
    from backend.app import oe_structured_provider_evidence as live

    c = fixture(monkeypatch)
    c.config.search = NS(maximum_mutation_slots=1)

    def row(value):
        return json.loads(json.dumps(vars(value), default=str))

    tables = {}
    for table, values in {
        "research_model_operations": [c.operation],
        "research_model_transports": [c.transport],
        "research_model_outcomes": [c.outcome],
        "research_budget_reservations": [c.reservation],
        "research_budget_settlements": [c.settlement],
        "oe_structured_provider_transports": [c.membership],
        "oe_structured_provider_receipts": c.receipts,
    }.items():
        tables[table] = [row(v) for v in values]
    specs = [
        (
            c.exchange.request_object_id,
            c.exchange.request_kind,
            c.exchange.request_sha256,
        ),
        (
            c.exchange.response_object_id,
            c.exchange.response_kind,
            c.exchange.response_sha256,
        ),
    ]
    specs += [
        (r.receipt_object_id, r.receipt_object_kind, r.receipt_sha256)
        for r in c.receipts
    ]
    tables["research_objects"] = []
    for identity, kind, sha in specs:
        raw = live.read_research_object(None, None, identity, kind, sha, None)
        key = identity_for_content(c.fence.owner_id, kind, raw).storage_key
        path = tmp_path / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        tables["research_objects"].append(
            {
                "id": str(identity),
                "owner_id": str(c.fence.owner_id),
                "object_kind": kind,
                "sha256": sha,
                "size_bytes": len(raw),
                "storage_key": key,
            }
        )

    def rows(session, model, **filters):
        return [
            dict(row)
            for row in tables[model.__tablename__]
            if all(str(row[k]) == str(v) for k, v in filters.items())
        ]

    monkeypatch.setattr(service, "_rows", rows)
    monkeypatch.setattr(service, "require_run_identity", lambda run: c.config)
    run = NS(
        id=c.fence.run_id, owner_id=c.fence.owner_id, generation=c.fence.generation
    )
    attempt = NS(
        id=c.fence.attempt_id,
        run_id=run.id,
        owner_id=run.owner_id,
        generation=run.generation,
        status="cancelled",
    )
    return NS(
        c=c, tables=tables, run=run, attempt=attempt, settings=NS(storage_root=tmp_path)
    )


@pytest.mark.parametrize(
    "change",
    [
        None,
        "missing_stop",
        "held",
        "charge",
        "bytes",
        "orphan_transport",
        "orphan_receipt",
        "wrong_attempt",
        "usage",
        "submitted",
        "missing_outcome",
        "empty",
    ],
)
def test_actual_post_revoke_sdk_barrier(case, change):
    c = case
    if change == "missing_stop":
        c.tables["oe_structured_provider_receipts"] = [
            r
            for r in c.tables["oe_structured_provider_receipts"]
            if r["kind"] != "stopped"
        ]
    elif change == "held":
        c.tables["research_budget_reservations"][0]["state"] = "uncertain"
    elif change == "charge":
        c.tables["research_budget_settlements"][0]["charge"]["calls"] = 0
    elif change == "bytes":
        (
            c.settings.storage_root / c.tables["research_objects"][-1]["storage_key"]
        ).write_bytes(b"{}")
    elif change == "orphan_transport":
        c.tables["research_model_transports"].append(
            {**c.tables["research_model_transports"][0], "id": str(uuid4())}
        )
    elif change == "orphan_receipt":
        c.tables["oe_structured_provider_receipts"][0]["membership_id"] = str(uuid4())
    elif change == "wrong_attempt":
        c.attempt.id = uuid4()
    elif change == "usage":
        c.tables["research_model_outcomes"][0]["usage"]["calls"] = 0
    elif change == "submitted":
        c.tables["research_model_transports"][0]["state"] = "submitted"
    elif change == "missing_outcome":
        c.tables["research_model_outcomes"].clear()
    elif change == "empty":
        for values in c.tables.values():
            values.clear()
    assert service.provider_attempt_stopped(None, c.run, c.attempt, c.settings) is (
        change in {None, "empty"}
    )


@pytest.mark.parametrize("state", ["failed", "unknown"])
def test_stopped_unsuccessful_transport_is_not_completed_response(case, state):
    c = case
    c.tables["research_model_operations"][0]["state"] = "unknown"
    c.tables["research_model_transports"][0]["state"] = state
    outcome = c.tables["research_model_outcomes"][0]
    outcome["outcome"] = state
    for key in ("response_object_id", "response_object_kind", "response_sha256"):
        outcome[key] = None
    assert service.provider_attempt_stopped(None, c.run, c.attempt, c.settings)
    c.tables["research_budget_reservations"][0]["state"] = "uncertain"
    assert not service.provider_attempt_stopped(None, c.run, c.attempt, c.settings)


def test_query_bound_is_enforced_before_validating_any_rows():
    from backend.app.research_broker_models import ResearchModelOperation

    session = NS(execute=lambda query: NS(mappings=lambda: NS(all=lambda: [{}] * 33)))
    with pytest.raises(ValueError, match="fixed bound"):
        service._rows(session, ResearchModelOperation, run_id=uuid4())


def test_invalid_run_binding_never_reads_objects(case, monkeypatch):
    def reject(run):
        raise ValueError("Invalid immutable run declaration")

    monkeypatch.setattr(service, "require_run_identity", reject)
    assert not service.provider_attempt_stopped(
        None, case.run, case.attempt, case.settings
    )


def test_unverifiable_physical_receipt_remains_nonterminal(case, monkeypatch):
    from packages.research_runtime.process_contracts import ProcessError

    def reject(*args):
        raise ProcessError("unknown_physical_receipt")

    monkeypatch.setattr(service, "parse_receipt", reject)
    assert not service.provider_attempt_stopped(
        None, case.run, case.attempt, case.settings
    )

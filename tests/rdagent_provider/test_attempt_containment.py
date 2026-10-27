"""Real retained SDK bytes and production validators; database rows are doubles."""

from types import SimpleNamespace
from uuid import UUID

import pytest
from test_cold_receipts import graph  # noqa: F401 -- shared actual-byte fixture

from backend.app import rdagent_provider_containment as barrier


@pytest.fixture
def attempt_graph(graph, monkeypatch, tmp_path):  # noqa: F811
    rows, values, files = graph
    operation = next(iter(values["operations"].values()))
    scope = {k: operation[k] for k in barrier_scope()}
    run = SimpleNamespace(
        id=UUID(scope["run_id"]), owner_id=UUID(scope["owner_id"]), generation=1
    )
    attempt = SimpleNamespace(id=UUID(scope["attempt_id"]), run_id=run.id, generation=1)
    config = values["configs"][str(run.id)]
    monkeypatch.setattr(barrier, "require_source_identity", lambda _: config)
    for family in ("settlements", "outcomes"):
        for identifier, row in values[family].items():
            row["id"] = identifier
    inventory = {
        "ResearchModelOperation": list(values["operations"].values()),
        "ResearchModelTransport": list(values["transports"].values()),
        "ResearchBudgetReservation": list(values["reservations"].values()),
        "ResearchBudgetSettlement": list(values["settlements"].values()),
        "ResearchModelOutcome": list(values["outcomes"].values()),
        "RDProviderTransportReceipt": rows,
        "ResearchObject": list(values["objects"].values()),
        "FactorGenerationModelOperation": [
            {
                **scope,
                "protocol": operation["protocol"],
                "operation_id": operation["id"],
            }
        ],
    }

    def load(session, model, **filters):
        result = inventory[model.__name__]
        if model.__name__ == "ResearchObject":
            return [row for row in result if row["id"] == str(filters["id"])]
        return result

    monkeypatch.setattr(barrier, "_rows", load)
    for key, raw in files.items():
        path = tmp_path / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return run, attempt, SimpleNamespace(storage_root=tmp_path), inventory, files


def barrier_scope():
    return ("owner_id", "run_id", "attempt_id", "generation")


def test_completed_sdk_full_stop_and_settlement_allow_terminal(attempt_graph):
    run, attempt, settings, _, _ = attempt_graph
    assert barrier.provider_attempt_stopped(None, run, attempt, settings)


@pytest.mark.parametrize(
    "missing",
    ["intent", "started", "stopped", "all", "charge", "member", "bytes", "submitted"],
)
def test_stopped_docker_cannot_substitute_for_orphan_sdk(attempt_graph, missing):
    run, attempt, settings, inventory, files = attempt_graph
    # Docker-only reconciliation may already report confirmed. This additional
    # barrier must still refuse terminal/sandbox_stopped; it never checks /proc.
    if missing in {"intent", "started", "stopped"}:
        inventory["RDProviderTransportReceipt"] = [
            r for r in inventory["RDProviderTransportReceipt"] if r["kind"] != missing
        ]
    elif missing == "all":
        inventory["RDProviderTransportReceipt"].clear()
    elif missing == "charge":
        inventory["ResearchBudgetSettlement"].clear()
    elif missing == "member":
        inventory["FactorGenerationModelOperation"].clear()
    elif missing == "submitted":
        inventory["ResearchModelTransport"][0]["state"] = "submitted"
    else:
        (settings.storage_root / next(iter(files))).unlink()
    assert not barrier.provider_attempt_stopped(None, run, attempt, settings)


def test_historical_fixture_has_no_new_sdk_requirement(monkeypatch):
    monkeypatch.setattr(
        barrier,
        "require_source_identity",
        lambda _: SimpleNamespace(model_profile="fixture.rdagent.v1"),
    )
    monkeypatch.setattr(
        barrier,
        "_rows",
        lambda *a, **kw: pytest.fail("Historical path queried new SDK inventory"),
    )
    assert barrier.provider_attempt_stopped(None, object(), object(), object())

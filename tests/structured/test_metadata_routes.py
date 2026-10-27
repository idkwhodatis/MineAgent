"""Portable route guards; real HTTP/Chromium uses the existing ten-unit gate."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import structured_result_routes as routes
from backend.app.services import DomainError


def test_capability_never_enables_creation(monkeypatch):
    from backend.app import structured_process_services as service
    from packages.research_runtime.process_contracts import ProcessError

    monkeypatch.setattr(service, "require_structured_capability", lambda _: None)
    assert routes.read_capability(NS(id=uuid4()), object()).model_dump() == {
        "can_read": True,
        "can_create": False,
        "reason_code": "retained_history_only_no_public_execution",
    }

    def unavailable(_):
        raise ProcessError("unsupported")

    monkeypatch.setattr(service, "require_structured_capability", unavailable)
    value = routes.read_capability(NS(id=uuid4()), object())
    assert not value.can_read and not value.can_create
    with pytest.raises(DomainError) as caught:
        routes.require_read_capability(object())
    assert caught.value.code == "structured_history_schema_unavailable"
    assert caught.value.status_code == 409


def test_foreign_factor_version_rejected_before_projection(monkeypatch):
    from backend.app import factor_services

    user = NS(id=uuid4())
    factor_id, version_id = uuid4(), uuid4()
    monkeypatch.setattr(
        factor_services,
        "owned",
        lambda session, model, owner, key: (
            NS(id=factor_id) if key == factor_id else NS(factor_id=uuid4())
        ),
    )
    with pytest.raises(DomainError) as caught:
        routes.factor_execution_records(factor_id, version_id, user, object(), 0)
    assert caught.value.status_code == 404


def test_missing_owned_run_rejected_before_capability():
    with pytest.raises(DomainError) as caught:
        routes.run_execution_records(uuid4(), NS(id=uuid4()), NS(scalar=lambda _: None))
    assert caught.value.status_code == 404


@pytest.mark.parametrize("mismatch", [False, True])
def test_identity_projection_never_contains_derived_results(monkeypatch, mismatch):
    from backend.app import structured_result_release as release
    from backend.app.execution_snapshot_models import ExecutionSnapshotPublication
    from backend.app.factor_models import Factor, FactorVersion

    owner, factor, version, snapshot, run, execution_id = (uuid4() for _ in range(6))
    execution = NS(
        id=execution_id,
        run_id=run,
        factor_version_id=version,
        snapshot_id=snapshot,
        structured_strategy_version_id=uuid4(),
    )
    objects = {
        Factor: NS(id=factor, owner_id=owner),
        FactorVersion: NS(
            id=version,
            owner_id=owner,
            factor_id=factor,
            ordinal=3,
            parent_version_id=None,
        ),
        ExecutionSnapshotPublication: NS(
            owner_id=owner, factor_version_id=uuid4() if mismatch else version
        ),
    }
    events = []
    monkeypatch.setattr(
        release, "owned_execution", lambda *args: events.append("owned")
    )
    session = NS(
        get=lambda model, key: objects[model],
        execute=lambda *args: events.append("graph"),
    )
    if mismatch:
        with pytest.raises(DomainError):
            routes.record_projection(session, owner, execution)
        return
    result = routes.record_projection(session, owner, execution).model_dump()
    assert events == ["owned", "graph"]
    assert (
        result["factor_version_id"] == version
        and result["execution_snapshot_id"] == snapshot
    )
    assert set(result) == {
        "execution_id",
        "run_id",
        "factor_id",
        "factor_version_id",
        "factor_version_ordinal",
        "parent_factor_version_id",
        "execution_snapshot_id",
        "structured_strategy_version_id",
        "qualification",
    }

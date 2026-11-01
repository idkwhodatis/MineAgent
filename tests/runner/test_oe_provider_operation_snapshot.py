"""Fresh ORM materializations, actual broker ordering; DB/SDK remain doubles."""

from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from backend.app import oe_structured_provider_broker as broker
from backend.app.oe_structured_operational_models import OEStructuredIterationIntent
from backend.app.oe_structured_provider_operation import (
    ProviderIntentSnapshot,
    ResolvedProviderOperation,
    snapshot_intent,
)
from packages.research_runtime.process_contracts import ProcessError
from tests.openevolve.test_structured_provider_broker import arranged


def values():
    return {
        field.name: uuid4()
        if field.type is UUID
        else 1
        if field.type is int
        else datetime(2026, 1, 1, tzinfo=UTC)
        if field.type is datetime
        else "fixture-only"
        for field in fields(ProviderIntentSnapshot)
    }


def changed(value):
    if type(value) is UUID:
        return uuid4()
    if type(value) is int:
        return value + 1
    if type(value) is datetime:
        return value + timedelta(seconds=1)
    return value + "-changed"


def test_snapshot_covers_every_persisted_column_and_is_frozen():
    assert {f.name for f in fields(ProviderIntentSnapshot)} == {
        col.key for col in OEStructuredIterationIntent.__table__.columns
    }
    data = values()
    first, second = (OEStructuredIterationIntent(**data) for _ in range(2))
    assert first != second  # The old dataclass retained this identity comparison.
    saved = snapshot_intent(first)
    assert saved == snapshot_intent(second) and saved.id == first.id
    first.context_sha256 = "later-mutation"
    assert saved == snapshot_intent(second)
    with pytest.raises(FrozenInstanceError):
        saved.id = uuid4()
    with pytest.raises(ProcessError):
        snapshot_intent(object())


@pytest.mark.parametrize("field", [f.name for f in fields(ProviderIntentSnapshot)])
def test_every_persisted_value_drift_rejected_before_sdk(monkeypatch, field):
    test = arranged(monkeypatch)
    original = broker.require_provider_operation
    data, calls = values(), []

    def fresh(*args, **kwargs):
        bound = original(*args, **kwargs)
        row = dict(data)
        if calls:
            row[field] = changed(row[field])
        calls.append(1)
        return ResolvedProviderOperation(
            snapshot_intent(OEStructuredIterationIntent(**row)),
            bound.config,
            bound.request,
            None,
        )

    monkeypatch.setattr(broker, "require_provider_operation", fresh)
    with pytest.raises(ProcessError) as caught:
        test.call()
    assert caught.value.code == "oe_structured_controller_context"
    assert "rollback" in test.events
    assert "submitted" not in test.events and "sdk" not in test.events


@pytest.mark.parametrize("drift_at", [None, 3, 4])
def test_fresh_session_snapshots_preserve_callback_order_and_failure(
    monkeypatch, drift_at
):
    test = arranged(monkeypatch)
    original = broker.require_provider_operation
    data, calls = values(), []

    def fresh(*args, **kwargs):
        bound = original(*args, **kwargs)
        row = dict(data)
        calls.append(1)
        if len(calls) == drift_at:
            row["worker_identity_sha256"] += "-changed"
        return ResolvedProviderOperation(
            snapshot_intent(OEStructuredIterationIntent(**row)),
            bound.config,
            bound.request,
            None,
        )

    monkeypatch.setattr(broker, "require_provider_operation", fresh)
    if drift_at is None:
        assert test.call()[0] == test.exchange_id
        assert len(calls) == 4
        assert test.events.index("submitted") < test.events.index("intent")
        assert test.events.index("started") < test.events.index("private_pipe")
        assert test.events.index("stopped") < test.events.index("settle_success")
    else:
        with pytest.raises(ProcessError):
            test.call()
        assert "rollback" in test.events and "private_pipe" not in test.events
        assert "settle_success" not in test.events


@pytest.mark.parametrize("field", ["config", "request", "context"])
def test_other_resolved_identity_fields_remain_equality_guards(field):
    bound = ResolvedProviderOperation(
        snapshot_intent(OEStructuredIterationIntent(**values())),
        object(),
        object(),
        object(),
    )
    assert replace(bound, **{field: object()}) != bound

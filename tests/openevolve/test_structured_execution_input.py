"""Actual wire codec + explicit storage/DB doubles; no native unit or PG claim."""

import hashlib
import json
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_execution_input as service
from backend.app.oe_structured_models import OEStructuredExecution
from backend.app.services import DomainError
from packages.structured_factor_strategy.contracts import StructuredEngineInput
from packages.structured_factor_strategy.wire import write_input
from tests.structured.test_operator import declared_input


def fixture(monkeypatch):
    market = b'{"fixture":"grammar-only"}'
    sha = hashlib.sha256(market).hexdigest()
    raw = declared_input().model_dump(mode="json")
    raw["market_sha256"] = sha
    raw["engine"]["input_identity"]["market_data_sha256"] = sha
    value = StructuredEngineInput.model_validate_json(json.dumps(raw))
    strategy = write_input(value)
    scope = {
        key: getattr(value, key)
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    }
    fence = NS(**scope)
    row = OEStructuredExecution(
        **scope,
        id=value.execution_id,
        candidate_id=uuid4(),
        program_version_id=value.structured_strategy_version_id,
        snapshot_id=value.snapshot.snapshot_id,
        strategy_input=value.model_dump(mode="json"),
        strategy_input_canonical=strategy.decode(),
        strategy_input_sha256=hashlib.sha256(strategy).hexdigest(),
        strategy_input_size_bytes=len(strategy),
        market_sha256=sha,
        market_size_bytes=len(market),
    )
    checks = []

    def guard(*args, **kwargs):
        checks.append(kwargs["resource_scope"])
        return None, None

    monkeypatch.setattr(service, "require_neutral_transaction", guard)

    def reconstruct(*args, **kwargs):
        assert kwargs["candidate_id"] == row.candidate_id
        assert kwargs["execution_id"] == row.id
        return strategy, market

    monkeypatch.setattr(service, "reconstruct_candidate_input", reconstruct)
    return NS(get=lambda kind, key: row), fence, row, strategy, market, checks


def test_persisted_execution_relation_and_post_read_fence_are_checked(monkeypatch):
    session, fence, row, strategy, market, checks = fixture(monkeypatch)
    assert service.resolve_execution_input(session, fence, row.id, NS()) == (
        strategy,
        market,
    )
    assert checks == ["factor_numeric", "factor_numeric"]


@pytest.mark.parametrize(
    "field",
    [
        "program_version_id",
        "snapshot_id",
        "strategy_input_sha256",
        "market_sha256",
        "strategy_input_size_bytes",
    ],
)
def test_detached_or_changed_execution_metadata_rejected(monkeypatch, field):
    session, fence, row, *_ = fixture(monkeypatch)
    setattr(
        row,
        field,
        uuid4()
        if field.endswith("_id")
        else (1 if field.endswith("bytes") else "f" * 64),
    )
    with pytest.raises(DomainError):
        service.resolve_execution_input(session, fence, row.id, NS())


def test_other_attempt_rejected_before_physical_read(monkeypatch):
    session, fence, row, *_ = fixture(monkeypatch)
    row.attempt_id = uuid4()
    monkeypatch.setattr(
        service,
        "reconstruct_candidate_input",
        lambda *a, **k: pytest.fail("cross-attempt physical read"),
    )
    with pytest.raises(DomainError):
        service.resolve_execution_input(session, fence, row.id, NS())

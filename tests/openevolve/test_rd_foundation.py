"""Foundation declaration tests, not actual PG/Docker execution acceptance."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

INTEGRATION = Path(__file__).parents[1] / "integration"
sys.path.insert(0, str(INTEGRATION))
spec = importlib.util.spec_from_file_location(
    "oe_rd_foundation", INTEGRATION / "oe_structured_rd_foundation.py"
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
try:
    spec.loader.exec_module(module)
finally:
    sys.path.remove(str(INTEGRATION))


def demo():
    from packages.structured_factor_strategy.demo import rd_preset

    return {
        "dataset": {
            "id": str(uuid4()),
            "name": "Synthetic demo",
            "market": "US",
            "start_date": "2024-03-11",
            "end_date": "2024-03-15",
            "source": "synthetic",
            "is_synthetic": True,
            "content_sha256": "a" * 64,
            "manifest": {},
            "created_at": "2024-03-20T00:00:00Z",
        },
        "qualification": "synthetic_by_construction_not_live_or_pit_evidence",
        "rd_preset": rd_preset(),
        "research_submitted": False,
    }


def test_demo_preset_remains_one_session_and_explicitly_acknowledged(monkeypatch):
    from backend.app.engine_run_preflight import RECOMMENDED_BUDGET

    monkeypatch.setattr(
        module, "provider_budget_floor", lambda binding: RECOMMENDED_BUDGET
    )
    owner = uuid4()
    operator = SimpleNamespace(
        connection=SimpleNamespace(id="local-demo"), public_binding=lambda oid: oid
    )
    value = demo()
    request = module.request_from_demo(value, operator, owner)
    assert str(request.dataset_version_id) == value["dataset"]["id"]
    assert request.window.model_dump(mode="json") == value["rd_preset"]["window"]
    assert request.task.model_dump(mode="json") == value["rd_preset"]["task"] | {
        "explanation_origin": "task_declaration"
    }
    assert request.input_fields == ("close", "volume")
    assert request.source_policy.acknowledged_unverified is True
    assert request.selected_steer_version_ids == ()
    assert request.lookback_sessions == 2 and request.max_iterations == 1


@pytest.mark.parametrize(
    "field,value", [("research_submitted", True), ("qualification", "verified_market")]
)
def test_foreign_demo_receipt_is_rejected(field, value):
    receipt = demo()
    receipt[field] = value
    with pytest.raises(ValueError):
        module.request_from_demo(receipt, None, uuid4())


def test_generated_source_computes_declared_return_not_precomputed_values():
    compile(module.DEMO_RD_SOURCE, "<actual model response>", "exec")
    assert 'history[0]["values"][field]' in module.DEMO_RD_SOURCE
    assert "Exactly two prior sessions plus current required" in module.DEMO_RD_SOURCE
    assert "0.04" not in module.DEMO_RD_SOURCE
    assert json.loads(json.dumps(module.DEMO_RD_SOURCE)) == module.DEMO_RD_SOURCE
    from dataclasses import fields

    values = {field.name: uuid4() for field in fields(module.DemoRDFoundation)}
    snapshot_id, analysis_id = uuid4(), uuid4()
    values.update(
        snapshot_release_receipt_ids=(snapshot_id,),
        analysis_release_receipt_ids=(analysis_id,),
    )
    receipt = module.DemoRDFoundation(**values)
    assert receipt.release_receipt_ids == (snapshot_id,)
    assert analysis_id not in receipt.release_receipt_ids

"""Explicit old structured/new normal partition, never legacy execution aliases."""

from uuid import uuid4

import pytest

from backend.app import structured_factor_backup as module


def empty():
    closure = {
        "schema_version": module.SCHEMA,
        "tables": {k: [] for k in module.FIELDS},
        "external_bindings": {"research_runs": []},
    }
    research = {
        "schema_version": "mineagent.research-restore-closure.v13",
        "external_bindings": {"runs": []},
        "tables": {
            k: []
            for k in (
                "research_process_admissions",
                "research_process_receipts",
                "research_budget_reservations",
                "research_budget_settlements",
                "research_process_states",
            )
        },
    }
    kwargs = {
        "research_closure": research,
        "execution_closure": {
            "tables": {
                "factor_execution_snapshot_publications": [],
                "factor_execution_snapshot_outcomes": [],
            }
        },
        "direct_analysis_closure": {
            "tables": {"dataset_versions": [], "data_usage_exposures": []}
        },
        "read_bytes": lambda *a: pytest.fail(
            "empty predecessor must not read new engine bytes"
        ),
    }
    return closure, kwargs


def test_v11_requires_mandatory_separate_final_domain_validator():
    closure, kw = empty()
    with pytest.raises(ValueError, match="final links"):
        module.validate_closure(closure, **kw)
    module.validate_closure(closure, **kw, oe_structured_links_pending=True)
    kw["research_closure"]["schema_version"] = "mineagent.research-restore-closure.v10"
    with pytest.raises(ValueError, match="final links"):
        module.validate_closure(closure, **kw, oe_structured_links_pending=True)
    module.validate_closure(closure, **kw)


@pytest.mark.parametrize(
    "schema", ["mineagent.oe-structured-provider-run.v1", "unknown", None]
)
def test_native_unit_only_partitions_to_exact_new_domain(schema):
    closure, kw = empty()
    owner, run, admission = (str(uuid4()) for _ in range(3))
    research = kw["research_closure"]
    research["external_bindings"]["runs"] = [
        {
            "id": run,
            "owner_id": owner,
            "purpose": "strategy_search",
            "engine": "openevolve",
            "config_schema_version": schema,
        }
    ]
    research["tables"]["research_process_admissions"] = [
        {
            "id": admission,
            "owner_id": owner,
            "run_id": run,
            "logical_operation_id": str(uuid4()),
            "profile": "mineagent.factor-strategy-engine.v1",
        }
    ]
    if schema == "mineagent.oe-structured-provider-run.v1":
        module.validate_closure(closure, **kw, oe_structured_links_pending=True)
        research["tables"]["research_process_admissions"][0]["owner_id"] = str(uuid4())
        with pytest.raises(ValueError, match="owner differs"):
            module.validate_closure(closure, **kw, oe_structured_links_pending=True)
    else:
        with pytest.raises(ValueError, match="Orphan structured process"):
            module.validate_closure(closure, **kw, oe_structured_links_pending=True)

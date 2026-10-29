"""Exact aggregate call ordering/reader forwarding; not physical restore proof."""

from types import SimpleNamespace as NS

import pytest

from backend.app import oe_structured_operations as module


def capabilities(**changes):
    return NS(
        **(
            {
                "oe_structured_closure_schema": module.SCHEMA,
                "research_closure_schema": module.RESEARCH,
                "rd_source_closure_schema": module.RD,
            }
            | changes
        )
    )


def snapshot():
    return {
        "oe_structured_closure": {"schema_version": module.SCHEMA},
        "research_closure": {"schema_version": module.RESEARCH},
        "rd_source_closure": {"schema_version": module.RD},
        "native_closure": {},
        "execution_closure": {},
        "direct_analysis_closure": {},
        "structured_strategy_closure": {},
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"oe_structured_closure_schema": "future"},
        {"oe_structured_closure_schema": None},
        {"research_closure_schema": "mineagent.research-restore-closure.v10"},
        {"rd_source_closure_schema": "mineagent.rd-source-restore-closure.v5"},
    ],
)
def test_mixed_or_unknown_capability_never_dispatches(changes):
    with pytest.raises(ValueError):
        module.closure_for(snapshot(), capabilities(**changes))


def test_historical_no_field_remains_closed():
    cap = NS(
        research_closure_schema="mineagent.research-restore-closure.v10",
        rd_source_closure_schema="mineagent.rd-source-restore-closure.v5",
    )
    assert module.closure_for({}, cap) is None
    with pytest.raises(ValueError):
        module.closure_for(snapshot(), cap)


def test_both_final_validators_receive_original_readers_and_proofs(monkeypatch):
    from backend.app import oe_structured_backup, rdagent_numeric_failure_backup

    value = snapshot()
    reader = object()
    inventory = []
    records = {"legacy_oe": {}, "structured": {}, "rejections": {}}
    calls = []

    def failure(*args, **kw):
        assert args == (
            value["rd_source_closure"],
            value["research_closure"],
            value["native_closure"],
            value["execution_closure"],
            records["rejections"],
            reader,
        )
        assert kw == {"max_rows": 23}
        calls.append("failure")

    def normal(closure, **kw):
        assert calls == ["failure"] and closure is value["oe_structured_closure"]
        assert kw["inventory"] is inventory and kw["read_bytes"] is reader
        assert (
            kw["maximum_rows"] == 23
            and kw["structured_closure"] is value["structured_strategy_closure"]
        )
        calls.append("normal")

    monkeypatch.setattr(
        rdagent_numeric_failure_backup, "validate_failure_links", failure
    )
    monkeypatch.setattr(oe_structured_backup, "validate_closure", normal)
    module.validate_snapshot(
        value, capabilities(), reader, 23, inventory=inventory, process_records=records
    )
    assert calls == ["failure", "normal"]


@pytest.mark.parametrize(
    "records",
    [
        None,
        {},
        {"rejections": {}},
        {"legacy_oe": {}, "structured": {}, "rejections": {}, "extra": {}},
    ],
)
def test_missing_actual_process_proofs_rejected(records):
    with pytest.raises(ValueError, match="physical process"):
        module.validate_snapshot(
            snapshot(), capabilities(), None, 23, inventory=[], process_records=records
        )


def test_empty_successor_shared_and_final_graph_validators_all_run():
    from backend.app import oe_structured_backup, rdagent_backup, research_backup
    from tests.operations.test_openevolve_full_wiring import (
        EmptyConnection,
        composed_snapshot,
    )

    value = composed_snapshot("026_rd_seeded_submissions")
    value["research_closure"] = {
        "schema_version": module.RESEARCH,
        "tables": {k: [] for k in research_backup.closure_fields(module.RESEARCH)},
        "external_bindings": {"runs": [], "attempts": []},
    }
    value["rd_source_closure"] = {
        "schema_version": module.RD,
        "tables": {k: [] for k in rdagent_backup.closure_fields(module.RD)},
        "external_bindings": {"runs": [], "datasets": []},
        "consumer_claims": [],
    }
    value["oe_structured_closure"] = oe_structured_backup.read_closure(
        EmptyConnection(), 100
    )

    def no_read(*a):
        pytest.fail("empty graph cannot read any object")

    records = research_backup.validate_closure(
        value["research_closure"],
        no_read,
        no_read,
        100,
        inventory=[],
        read_bytes=no_read,
        rd_source_closure=value["rd_source_closure"],
        openevolve_closure=value["openevolve_closure"],
        structured_links_pending=True,
        seed_context={},
        oe_structured_closure=value["oe_structured_closure"],
        successor_links_pending=True,
    )
    assert records == {"legacy_oe": {}, "structured": {}, "rejections": {}}
    module.validate_snapshot(
        value, capabilities(), no_read, 100, inventory=[], process_records=records
    )


def test_aggregate_backup_and_retention_empty_v11_with_installed_capability(
    tmp_path, monkeypatch
):
    import time

    from backend.app import (
        oe_structured_backup,
        operations_backup,
        operations_retention,
        rdagent_backup,
        research_backup,
    )
    from backend.app.schema_capabilities import capabilities_for
    from tests.operations.test_openevolve_full_wiring import (
        EmptyConnection,
        composed_snapshot,
    )

    revision = "028_oe_structured_search"
    value = composed_snapshot("026_rd_seeded_submissions")
    value["schema_revision"] = revision
    value["research_closure"] = {
        "schema_version": module.RESEARCH,
        "tables": {k: [] for k in research_backup.closure_fields(module.RESEARCH)},
        "external_bindings": {"runs": [], "attempts": []},
    }
    value["rd_source_closure"] = {
        "schema_version": module.RD,
        "tables": {k: [] for k in rdagent_backup.closure_fields(module.RD)},
        "external_bindings": {"runs": [], "datasets": []},
        "consumer_claims": [],
    }
    value["oe_structured_closure"] = oe_structured_backup.read_closure(
        EmptyConnection(), 100
    )
    limits = operations_backup.BackupLimits()
    operations_backup._validate_references(tmp_path, [], value, limits)
    assert operations_retention._snapshot_manifest_keys(value, limits) == set()
    _, trees = operations_retention._protected(value, {}, {}, time.monotonic() + 10)
    assert "factors" in trees
    assert capabilities_for(revision).oe_structured_closure_schema == module.SCHEMA

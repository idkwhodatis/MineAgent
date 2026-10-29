"""Exact legacy/normal family partition, not PostgreSQL or whole restore proof."""

from copy import deepcopy
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import openevolve_backup_partition as p
from backend.app import openevolve_backup_shape as shape
from backend.app.openevolve_backup_spec import validate_specs
from backend.app.openevolve_operations import read_openevolve_closure
from tests.openevolve.test_backup_shape import Connection, empty
from tests.openevolve.test_backup_spec import case


def mixed():
    closure, research = case()
    research["schema_version"] = p.V11
    research["external_bindings"]["runs"][0]["config_schema_version"] = p.LEGACY
    research["external_bindings"]["runs"].append(
        {
            "id": str(uuid4()),
            "owner_id": str(uuid4()),
            "engine": "openevolve",
            "config_schema_version": p.NORMAL,
        }
    )
    return closure, research


def test_mixed_v11_keeps_actual_legacy_spec_and_omits_only_normal_family():
    closure, research = mixed()
    before = deepcopy(research)
    result = validate_specs(closure, research)
    assert set(result) == {closure["external_bindings"]["runs"][0]["id"]}
    assert research == before  # Never create a fake older research envelope.


@pytest.mark.parametrize("schema", [None, "", "future", 11, {}, p.LEGACY + ".future"])
def test_unknown_missing_oe_projection_rejected(schema):
    closure, research = mixed()
    research["external_bindings"]["runs"][1]["config_schema_version"] = schema
    with pytest.raises(ValueError, match="config schema"):
        validate_specs(closure, research)


def test_omitted_projected_field_rejected():
    closure, research = mixed()
    del research["external_bindings"]["runs"][1]["config_schema_version"]
    with pytest.raises(ValueError):
        validate_specs(closure, research)


@pytest.mark.parametrize("schema", [None, p.NORMAL, "future"])
def test_legacy_full_config_cannot_disagree_with_fresh_shared_projection(schema):
    closure, research = mixed()
    closure["external_bindings"]["runs"][0]["config"]["schema_version"] = schema
    with pytest.raises(ValueError, match="full config"):
        validate_specs(closure, research)


def test_normal_run_cannot_be_smuggled_into_legacy_full_inventory():
    closure, research = mixed()
    normal = deepcopy(closure["external_bindings"]["runs"][0])
    normal["id"] = research["external_bindings"]["runs"][1]["id"]
    normal["config"]["schema_version"] = p.NORMAL
    closure["external_bindings"]["runs"].append(normal)
    with pytest.raises(ValueError, match="partition"):
        validate_specs(closure, research)


def test_shape_reference_layer_also_rejects_omitted_legacy_run():
    closure, research = empty()
    research["schema_version"] = p.V11
    research["external_bindings"]["runs"] = [
        {"id": str(uuid4()), "engine": "openevolve", "config_schema_version": p.LEGACY}
    ]
    with pytest.raises(ValueError, match="partition"):
        shape.validate_references(closure, research, maximum_rows=10)


def test_normal_only_shared_runs_allow_empty_legacy_closure_not_normal_acceptance():
    closure, research = empty()
    research["schema_version"] = p.V11
    research["external_bindings"]["runs"] = [
        {"id": str(uuid4()), "engine": "openevolve", "config_schema_version": p.NORMAL}
    ]
    shape.validate_references(closure, research, maximum_rows=10)


@pytest.mark.parametrize("version", range(1, 11))
def test_old_research_versions_never_gain_normal_exclusion(version):
    closure, research = mixed()
    research["schema_version"] = f"mineagent.research-restore-closure.v{version}"
    with pytest.raises(ValueError, match="inventory"):
        validate_specs(closure, research)


class ObservedConnection(Connection):
    def __init__(self, rows):
        super().__init__([[] for _ in range(32)] + [rows, [], []])
        self.statements = []

    def execute(self, statement, values):
        self.statements.append(str(statement))
        return super().execute(statement, values)


def test_reader_filters_only_explicit_normal_schema_with_null_safe_sql():
    connection = ObservedConnection([])
    shape.read_closure(connection, 10, research_schema=p.V11)
    sql = connection.statements[32]
    assert "WHERE engine='openevolve'" in sql
    assert f"config->>'schema_version' IS DISTINCT FROM '{p.NORMAL}'" in sql
    assert all(result.closed for result in connection.results)


@pytest.mark.parametrize("schema", [None, "future", p.NORMAL])
def test_reader_rejects_unknown_or_wrongly_returned_full_run(schema):
    connection = ObservedConnection([{"config": {"schema_version": schema}}])
    with pytest.raises(ValueError, match="legacy reader"):
        shape.read_closure(connection, 10, research_schema=p.V11)
    assert all(result.closed for result in connection.results)


def test_operations_passes_partition_only_for_actual_v11_capability(monkeypatch):
    calls = []
    monkeypatch.setattr(
        shape, "read_closure", lambda *args, **kw: calls.append(kw) or empty()[0]
    )
    caps = NS(openevolve_closure_schema=shape.SCHEMA, research_closure_schema=p.V11)
    read_openevolve_closure(None, caps, NS(max_files=100, max_manifest_bytes=100000))
    assert calls == [{"maximum_bytes": 100000, "research_schema": p.V11}]
    caps.research_closure_schema = "mineagent.research-restore-closure.v10"
    read_openevolve_closure(None, caps, NS(max_files=100, max_manifest_bytes=100000))
    assert calls[-1] == {"maximum_bytes": 100000}

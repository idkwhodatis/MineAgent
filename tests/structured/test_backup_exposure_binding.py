"""Nonempty v1 retained-byte closure; frozen structured mode has no experiment."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app import structured_factor_backup as backup
from backend.app.experiment_services import dataset_identities
from packages.factors.contracts import digest
from packages.structured_factor_strategy.contracts import StructuredProgram
from packages.structured_factor_strategy.run import StructuredRunConfig

pytest_plugins = ["tests.structured.test_reconstruction"]


def material(retained):
    args, _, _ = retained
    run = {**args["run"], "status": "interrupted"}
    tables = {key: [] for key in backup.FIELDS}
    for name, source in (
        ("structured_strategy_versions", "version"),
        ("structured_strategy_specs", "spec"),
        ("factor_strategy_executions", "execution"),
    ):
        row = {key: None for key in backup.FIELDS[name].split()}
        row.update(args[source], created_at="2026-01-01T00:00:00+00:00")
        tables[name] = [row]
    closure = {
        "schema_version": backup.SCHEMA,
        "tables": tables,
        "external_bindings": {"research_runs": [run]},
    }
    source_run = str(uuid4())
    publication = {**args["publication"], "run_id": source_run}
    config = StructuredRunConfig.model_validate(args["spec"]["config"])
    program = StructuredProgram.model_validate(args["version"]["program"])
    dataset = args["dataset"]
    exposure = {
        "id": str(uuid4()),
        "owner_id": run["owner_id"],
        "run_id": run["id"],
        "source_key": "structured-factor-source:" + run["id"],
        "dataset_version_id": dataset["id"],
        "start_date": dataset["start_date"],
        "end_date": dataset["end_date"],
        "purpose": "structured_factor_execution_source",
        "experiment_id": None,
        "split_version_id": None,
        "identities": dataset_identities(
            SimpleNamespace(manifest=dataset["manifest"], market=dataset["market"])
        ),
        "provenance": {
            "schema_version": "mineagent.structured-source-exposure.v1",
            "config_sha256": digest(config),
            "program_sha256": digest(program),
            "snapshot_ids": [str(x) for x in config.snapshots],
            "dataset_manifest_sha256": config.dataset_sha256,
            "extent": "whole_retained_dataset",
            "interpretation": "research_input_admission_not_order_or_fill_evidence",
        },
    }
    shared = {
        key: run[key]
        for key in ["id", "owner_id", "generation", "purpose", "engine", "status"]
    }
    research = {
        "schema_version": backup.RESEARCH_SCHEMA,
        "external_bindings": {
            "runs": [shared, {**shared, "id": source_run, "engine": "rdagent"}]
        },
        "tables": {
            key: []
            for key in (
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
                "factor_execution_snapshot_publications": [publication],
                "factor_execution_snapshot_outcomes": [
                    {"publication_id": publication["id"]}
                ],
            }
        },
        "direct_analysis_closure": {
            "tables": {
                "dataset_versions": [dataset],
                "data_usage_exposures": [exposure],
            }
        },
        "read_bytes": args["read_bytes"],
    }
    return closure, kwargs, exposure


def test_nonempty_v1_reconstructs_retained_bytes_without_unprojected_fields(
    retained_declaration,
):
    closure, kwargs, _ = material(retained_declaration)
    assert "experiment_id" not in backup.RUN_FIELDS.split()
    assert "split_version_id" not in backup.RUN_FIELDS.split()
    result = backup.validate_closure(closure, **kwargs)
    assert len(result["factor_strategy_executions"]) == 1


@pytest.mark.parametrize("field", ["experiment_id", "split_version_id"])
def test_nonempty_v1_rejects_exposure_binding_forbidden_by_structured_mode(
    retained_declaration, field
):
    closure, kwargs, exposure = material(retained_declaration)
    exposure[field] = str(uuid4())
    with pytest.raises(ValueError, match="Structured source exposure differs"):
        backup.validate_closure(closure, **kwargs)


def test_all_literal_run_reads_are_present_in_exact_v1_projection():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(backup.validate_closure))
    reads = {
        node.slice.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "run"
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    }
    assert reads <= set(backup.RUN_FIELDS.split())


def test_frozen_structured_mode_independently_forbids_experiment_and_split():
    from backend.app.openevolve_revision_ddl import historical
    from backend.app.structured_revision_ddl import _check

    for revision in ("025", "026", "027", "027a"):
        sql = historical(revision, "UPGRADE_SQL")
        if "ck_research_run_legacy_config" in sql:
            rule = _check(sql, "ck_research_run_legacy_config")
            assert "experiment_id IS NULL AND split_version_id IS NULL" in rule

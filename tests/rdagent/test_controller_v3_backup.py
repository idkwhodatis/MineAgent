"""Exact cold-version gates; unit evidence, not a full cold restore."""

from dataclasses import asdict
from uuid import uuid4

import pytest

from backend.app import rdagent_backup as rd
from backend.app import research_backup as research
from backend.app.rdagent_profile_backup import (
    PROFILE,
    RESEARCH,
    SOURCE,
    require_admission_revision,
    require_config_revision,
)
from backend.app.schema_capabilities import capabilities_for


def test_new_capabilities_preserve_every_other_domain():
    old = asdict(capabilities_for("026_rd_seeded_submissions"))
    new = asdict(capabilities_for("027_rd_controller_v3"))
    old.update(research_closure_schema=RESEARCH, rd_source_closure_schema=SOURCE)
    assert new == old
    assert rd.closure_fields(SOURCE) == rd.closure_fields(rd.SCHEMA_V5)
    assert research.closure_fields(RESEARCH) == research.closure_fields(
        research.SCHEMA_V10
    )


@pytest.mark.parametrize(
    "r,s",
    [
        (RESEARCH, SOURCE),
        (research.SCHEMA_V10, rd.SCHEMA_V5),
        (RESEARCH, rd.SCHEMA_V5),
        (research.SCHEMA_V10, SOURCE),
    ],
)
def test_missing_profile_remains_uninterpreted(r, s):
    require_config_revision({}, research_schema=r, source_schema=s)


@pytest.mark.parametrize(
    "value", [None, "", False, {}, [], "mineagent.rdagent-orchestrator.v2", "unknown"]
)
def test_explicit_unknown_or_null_never_authorized(value):
    with pytest.raises(ValueError):
        require_config_revision(
            {"controller_profile": value},
            research_schema=RESEARCH,
            source_schema=SOURCE,
        )


@pytest.mark.parametrize(
    "r,s",
    [
        (research.SCHEMA_V10, rd.SCHEMA_V5),
        (RESEARCH, rd.SCHEMA_V5),
        (research.SCHEMA_V10, SOURCE),
        (None, None),
    ],
)
def test_new_profile_requires_exact_supplied_versions(r, s):
    with pytest.raises(ValueError):
        require_config_revision(
            {"controller_profile": PROFILE}, research_schema=r, source_schema=s
        )


def test_new_profile_exact_versions():
    require_config_revision(
        {"controller_profile": PROFILE}, research_schema=RESEARCH, source_schema=SOURCE
    )
    require_admission_revision(PROFILE, RESEARCH)


@pytest.mark.parametrize(
    "schema", [research.SCHEMA, research.SCHEMA_V5, research.SCHEMA_V10]
)
def test_old_research_rejects_new_admission_even_without_journal(schema):
    with pytest.raises(ValueError):
        require_admission_revision(PROFILE, schema)


@pytest.mark.parametrize("name", ["runs", "factor_research_specs"])
@pytest.mark.parametrize("schema,allowed", [(rd.SCHEMA_V5, False), (SOURCE, True)])
def test_actual_source_index_gates_raw_profile_before_semantic_reconstruction(
    name, schema, allowed
):
    closure = {
        "schema_version": schema,
        "tables": {k: [] for k in rd.closure_fields(schema)},
        "external_bindings": {k: [] for k in rd.EXTERNAL_FIELDS},
        "consumer_claims": [],
    }
    fields = (rd.EXTERNAL_FIELDS if name == "runs" else rd.closure_fields(schema))[name]
    row = {key: None for key in fields.split()}
    row.update(
        id=str(uuid4()), owner_id=str(uuid4()), config={"controller_profile": PROFILE}
    )
    if "created_at" in row:
        row["created_at"] = "2026-10-28T00:00:00Z"
    if "generation" in row:
        row["generation"] = 1
    target = closure["external_bindings"] if name == "runs" else closure["tables"]
    target[name].append(row)
    if allowed:
        assert rd._indexes(closure, 100)[name][row["id"]] == row
    else:
        with pytest.raises(ValueError, match="Controller profile"):
            rd._indexes(closure, 100)


def research_with_controller(schema, tmp_path):
    from test_rdagent_controller_process import records

    from packages.research_runtime.process_persistence_codec import encode_row

    launch, *_ = records(tmp_path)
    valid = {
        k: str(v) if hasattr(v, "hex") else v
        for k, v in encode_row(launch.intent.admission).items()
    }
    owner, run, attempt, admission = (str(uuid4()) for _ in range(4))
    closure = {
        "schema_version": schema,
        "tables": {k: [] for k in research.FIELDS},
        "external_bindings": {
            "runs": [
                {
                    "id": run,
                    "owner_id": owner,
                    "generation": 1,
                    "purpose": "factor_research",
                    "engine": "rdagent",
                    "status": "failed",
                }
            ],
            "attempts": [
                {
                    "id": attempt,
                    "owner_id": owner,
                    "run_id": run,
                    "generation": 1,
                    "status": "failed",
                    "sandbox_stopped": True,
                    "finished_at": "2026-10-28T00:00:00Z",
                }
            ],
        },
    }
    row = dict.fromkeys(
        research.closure_fields(schema)["research_process_admissions"].split()
    )
    row.update(valid)
    row.update(
        id=admission,
        owner_id=owner,
        run_id=run,
        attempt_id=attempt,
        generation=1,
        profile=PROFILE,
    )
    closure["tables"]["research_process_admissions"].append(row)
    return closure, row


def test_real_manifest_dispatch_new_controller_paths_only_new_version(tmp_path):
    closure, row = research_with_controller(RESEARCH, tmp_path)
    from backend.app.rdagent_controller_backup import manifest_keys

    assert research.manifest_keys(closure) == manifest_keys(row)
    closure["schema_version"] = research.SCHEMA_V10
    with pytest.raises(ValueError, match="Controller v3 admission"):
        research.manifest_keys(closure)


@pytest.mark.parametrize(
    "rv,sv,allowed", [(10, 5, True), (11, 6, True), (10, 6, False), (11, 5, False)]
)
def test_execution_dependency_matrix_uses_exact_research_source_pair(rv, sv, allowed):
    from backend.app import execution_snapshot_backup as execution
    from backend.app import factor_native_backup as native

    closure = {
        "schema_version": execution.SCHEMA_V2,
        "tables": {k: [] for k in execution.FIELDS},
    }
    kwargs = {
        "inventory_rows": [],
        "research_closure": {
            "schema_version": f"mineagent.research-restore-closure.v{rv}",
            "tables": {k: [] for k in research.FIELDS},
        },
        "rd_source_closure": {
            "schema_version": f"mineagent.rd-source-restore-closure.v{sv}",
            "tables": {k: [] for k in rd.FIELDS_V5},
        },
        "native_closure": {
            "schema_version": native.SCHEMA_V3,
            "tables": {k: [] for k in native.FIELDS},
        },
        "direct_analysis_closure": {
            "schema_version": "mineagent.direct-analysis-restore-closure.v1",
            "tables": {},
        },
    }
    if allowed:
        execution.validate_closure(closure, None, None, 100, **kwargs)
    else:
        with pytest.raises(ValueError, match="dependency versions"):
            execution.validate_closure(closure, None, None, 100, **kwargs)


@pytest.mark.parametrize("rv", [6, 7, 8, 9, 10])
def test_native_cannot_attach_new_source_to_old_research_revision(rv):
    from backend.app import factor_native_backup as native

    closure = {
        "schema_version": native.SCHEMA_V3,
        "tables": {k: [] for k in native.FIELDS},
    }
    with pytest.raises(ValueError):
        native.validate_closure(
            closure,
            None,
            None,
            100,
            inventory=[],
            research_closure={
                "schema_version": f"mineagent.research-restore-closure.v{rv}",
                "tables": {},
            },
            rd_source_closure={"schema_version": SOURCE, "tables": {}},
        )

"""Exact new cold tuple; no live configuration can reinterpret retained history."""

import copy
import time
from typing import get_args

import pytest

from backend.app.operations_backup import REVISIONS, BackupLimits, _validate_references
from backend.app.operations_retention import _protected, _snapshot_manifest_keys
from backend.app.operations_schemas import BackupReceipt
from backend.app.rdagent_profile_backup import (
    require_admission_revision,
    require_config_revision,
)
from backend.app.schema_capabilities import capabilities_for
from tests.operations.test_openevolve_full_wiring import composed_snapshot

NEW = "027c_seeded_controller_v4"
R12 = "mineagent.research-restore-closure.v12"
S7 = "mineagent.rd-source-restore-closure.v7"
R11 = "mineagent.research-restore-closure.v11"
S6 = "mineagent.rd-source-restore-closure.v6"
V4 = "mineagent.rdagent-orchestrator.v4"
V3 = "mineagent.rdagent-orchestrator.v3"


def test_explicit_new_capability_changes_only_versioned_closure_pair():
    old, new = capabilities_for("027b_provider_material"), capabilities_for(NEW)
    assert {
        k: (getattr(old, k), getattr(new, k))
        for k in vars(old)
        if getattr(old, k) != getattr(new, k)
    } == {"research_closure_schema": (R11, R12), "rd_source_closure_schema": (S6, S7)}
    assert NEW in REVISIONS
    assert (
        set(get_args(BackupReceipt.model_fields["schema_revision"].annotation))
        == REVISIONS
    )


def test_new_empty_full_backup_and_retention_dispatch(tmp_path):
    snapshot = composed_snapshot(NEW)
    _validate_references(tmp_path, [], snapshot, BackupLimits())
    assert _snapshot_manifest_keys(snapshot, BackupLimits()) == set()
    _, trees = _protected(snapshot, {}, {}, time.monotonic() + 10)
    assert "factors" in trees


@pytest.mark.parametrize("profile", [V3, V4])
@pytest.mark.parametrize(
    "research,source",
    [
        (R11, S6),
        (R12, S7),
        (R11, S7),
        (R12, S6),
        (None, None),
        ("unknown", S7),
        (R12, "unknown"),
    ],
)
def test_cold_profile_exact_pair(profile, research, source):
    allowed = (research, source) == (R12, S7) or (
        profile == V3 and (research, source) == (R11, S6)
    )
    if allowed:
        require_config_revision(
            {"controller_profile": profile},
            research_schema=research,
            source_schema=source,
        )
    else:
        with pytest.raises(ValueError):
            require_config_revision(
                {"controller_profile": profile},
                research_schema=research,
                source_schema=source,
            )


@pytest.mark.parametrize(
    "profile", [None, {}, [], False, "unknown", "mineagent.rdagent-orchestrator.v2"]
)
def test_explicit_null_unknown_and_historical_profile_not_reinterpreted(profile):
    with pytest.raises(ValueError):
        require_config_revision(
            {"controller_profile": profile}, research_schema=R12, source_schema=S7
        )


def test_missing_legacy_field_preserved():
    require_config_revision({}, research_schema=R12, source_schema=S7)
    require_admission_revision(V3, R11)
    require_admission_revision(V3, R12)
    require_admission_revision(V4, R12)
    with pytest.raises(ValueError):
        require_admission_revision(V4, R11)


@pytest.mark.parametrize(
    "field,bad", [("research_closure", R11), ("rd_source_closure", S6)]
)
def test_new_revision_cannot_claim_old_pair(tmp_path, field, bad):
    snapshot = copy.deepcopy(composed_snapshot(NEW))
    snapshot[field]["schema_version"] = bad
    with pytest.raises(ValueError):
        _validate_references(tmp_path, [], snapshot, BackupLimits())


def test_v4_orphan_run_without_any_launch_cannot_skip_seed_closure():
    from uuid import uuid4

    from backend.app.rdagent_seed_backup import validate_closure

    snapshot = composed_snapshot(NEW)
    snapshot["rd_source_closure"]["external_bindings"]["runs"].append(
        {
            "id": str(uuid4()),
            "owner_id": str(uuid4()),
            "config": {"controller_profile": V4},
        }
    )
    with pytest.raises(ValueError, match="lacks retained parent"):
        validate_closure(
            snapshot["rd_seed_closure"],
            rd_source_closure=snapshot["rd_source_closure"],
            research_closure=snapshot["research_closure"],
            factor_closure=snapshot["factor_closure"],
            direct_analysis_closure=snapshot["direct_analysis_closure"],
            rd_submission_closure=snapshot["rd_submission_closure"],
            read_bytes=lambda *a: pytest.fail("orphan read"),
            maximum_rows=100,
        )


@pytest.mark.parametrize(
    "constructor", ["seeded_bootstrap", "build_expected_bootstrap"]
)
def test_v4_empty_seed_context_does_not_become_fresh(constructor):
    from types import SimpleNamespace
    from uuid import uuid4

    from backend.app import rdagent_seed_backup

    value = SimpleNamespace(
        run_id=uuid4(), config=SimpleNamespace(controller_profile=V4)
    )
    with pytest.raises(ValueError, match="lacks retained parent"):
        if constructor == "seeded_bootstrap":
            rdagent_seed_backup.seeded_bootstrap(value, {})
        else:
            rdagent_seed_backup.build_expected_bootstrap(seed_context={}, **vars(value))


@pytest.mark.parametrize("table", ["runs", "factor_research_specs"])
@pytest.mark.parametrize("schema,allowed", [(S6, False), (S7, True)])
def test_raw_v4_config_is_version_gated_without_admission(table, schema, allowed):
    from uuid import uuid4

    from backend.app import rdagent_backup as rd

    closure = {
        "schema_version": schema,
        "tables": {k: [] for k in rd.closure_fields(schema)},
        "external_bindings": {k: [] for k in rd.EXTERNAL_FIELDS},
        "consumer_claims": [],
    }
    fields = (rd.EXTERNAL_FIELDS if table == "runs" else rd.closure_fields(schema))[
        table
    ]
    row = dict.fromkeys(fields.split())
    row.update(
        id=str(uuid4()), owner_id=str(uuid4()), config={"controller_profile": V4}
    )
    if "created_at" in row:
        row["created_at"] = "2026-10-29T00:00:00Z"
    if "generation" in row:
        row["generation"] = 1
    target = closure["external_bindings"] if table == "runs" else closure["tables"]
    target[table].append(row)
    if allowed:
        assert rd._indexes(closure, 100)[table][row["id"]] == row
    else:
        with pytest.raises(ValueError, match="Controller profile"):
            rd._indexes(closure, 100)

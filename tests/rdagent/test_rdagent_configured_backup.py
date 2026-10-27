"""Cold reconstruction over real retained fixture files, no PG restore claim."""

import json
from uuid import UUID

import pytest
from sqlalchemy import inspect
from test_rdagent_configured_inputs import case, persist  # noqa: F401
from test_rdagent_configured_source import retained  # noqa: F401
from test_rdagent_configured_source_service import setup_source  # noqa: F401

from backend.app.rdagent_configured_backup import validate_configured_input_closure


def row(value):
    return json.loads(
        json.dumps(
            {
                key.key: getattr(value, key.key)
                for key in inspect(type(value)).column_attrs
            },
            default=lambda x: str(x) if isinstance(x, UUID) else x.isoformat(),
        )
    )


@pytest.fixture
def cold(case):  # noqa: F811
    s = case
    witness, _ = persist(s)
    scope = {
        key: str(getattr(s.fence, key)) if key != "generation" else s.fence.generation
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    }
    key = f"factors/{s.fence.owner_id}/objects/{s.code.code_sha256}.py"
    path = s.settings.storage_root / key
    path.parent.mkdir(parents=True)
    path.write_bytes(s.code.code_utf8)
    tables = {
        "factor_cutoff_executions": [row(s.execution)],
        "factor_evaluation_starts": [row(s.evaluation)],
        "factor_generation_sources": [row(s.source_row)],
        "factor_source_read_witnesses": [
            dict(
                id=str(s.code.read_witness_id),
                **scope,
                source_id=str(s.source_row.id),
                source_object_id=str(s.code.code_object_id),
                witness_sha256=s.code.read_witness_sha256,
            )
        ],
        "factor_analysis_plans": [row(s.plan)],
        "factor_research_specs": [
            {
                "id": str(s.plan.spec_id),
                "owner_id": scope["owner_id"],
                "run_id": scope["run_id"],
                "config": s.config.model_dump(mode="json"),
            }
        ],
        "factor_objects": [
            {
                "id": str(s.code.code_object_id),
                "owner_id": scope["owner_id"],
                "object_kind": "code",
                "sha256": s.code.code_sha256,
                "size_bytes": len(s.code.code_utf8),
                "storage_key": key,
            }
        ],
        "research_process_admissions": [row(s.admission)],
    }

    def read(key, maximum):
        raw = (s.settings.storage_root / key).read_bytes()
        assert len(raw) <= maximum
        return raw

    return [row(witness)], {
        "tables": tables,
        "datasets": [row(s.dataset)],
        "read_bytes": read,
    }


def test_rebuilds_real_prefix_and_full_admitted_input(cold):
    witnesses, kwargs = cold
    validate_configured_input_closure(witnesses, **kwargs)


@pytest.mark.parametrize(
    "field",
    [
        "input_sha256",
        "projection_sha256",
        "calendar_sha256",
        "availability_revision_sha256",
        "dataset_manifest_sha256",
        "witness_sha256",
    ],
)
def test_witness_drift_rejected(cold, field):
    witnesses, kwargs = cold
    witnesses[0][field] = "f" * 64
    with pytest.raises(ValueError):
        validate_configured_input_closure(witnesses, **kwargs)


def test_no_missing_configured_cutoff_witness(cold):
    _, kwargs = cold
    with pytest.raises(ValueError, match="incomplete"):
        validate_configured_input_closure([], **kwargs)


def test_no_duplicate_witness(cold):
    witnesses, kwargs = cold
    with pytest.raises(ValueError, match="duplicate"):
        validate_configured_input_closure(witnesses * 2, **kwargs)


def test_rereads_actual_marketdata_not_only_manifest(cold):
    witnesses, kwargs = cold
    read = kwargs["read_bytes"]
    kwargs["read_bytes"] = lambda key, maximum: (
        b"[]" if key.endswith("preview.json") else read(key, maximum)
    )
    with pytest.raises(ValueError):
        validate_configured_input_closure(witnesses, **kwargs)


@pytest.mark.parametrize(
    "table",
    [
        "factor_cutoff_executions",
        "factor_evaluation_starts",
        "factor_generation_sources",
        "factor_source_read_witnesses",
        "research_process_admissions",
    ],
)
def test_scoped_dependencies(cold, table):
    witnesses, kwargs = cold
    kwargs["tables"][table][0]["generation"] += 1
    with pytest.raises(ValueError, match="scope"):
        validate_configured_input_closure(witnesses, **kwargs)

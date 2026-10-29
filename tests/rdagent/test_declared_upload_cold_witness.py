"""Actual basket source -> candidate bytes -> frozen cold witness, no execution."""

# ruff: noqa: F811
import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_declared_upload_backup import retained  # noqa: F401
from test_declared_upload_source import OWNER, TARGET
from test_rdagent_contracts import research_dict

from backend.app.declared_upload_backup import retained_artifacts
from backend.app.rdagent_configured_backup import validate_configured_input_closure
from backend.app.rdagent_configured_inputs import build_candidate_projection
from packages.factors.contracts import ExposureHistory, digest
from packages.rdagent_adapter.configured_source import (
    ConfiguredSourcePolicy,
    parse_retained_source,
)
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.host_evidence import research_snapshot_input


@pytest.fixture
def witness(retained):
    backup, blobs, read = retained
    ds = backup["declared_upload_closure"]["tables"]["datasets"][-1]
    policy = ConfiguredSourcePolicy(acknowledged_unverified=True)
    market = parse_retained_source(
        dataset_id=TARGET,
        manifest_sha256=ds["content_sha256"],
        manifest_bytes=blobs[ds["storage_key"] + "/manifest.json"],
        artifacts=retained_artifacts(
            ds["manifest"], ds["storage_key"], read, declared_upload=True
        ),
        policy=policy,
        input_names=("close",),
        declared_upload=True,
        owner_id=OWNER,
    )
    raw = research_dict()
    raw["input"].update(
        dataset=market.dataset.model_dump(mode="json"),
        calculation_universe=market.universe.model_dump(mode="json"),
        calendar_sha256=market.calendar.sha256,
        market_timezone=market.calendar.timezone,
        input_fields=[x.model_dump(mode="json") for x in market.input_fields],
        window={"start": "2024-01-03", "end": "2024-01-04"},
        lookback_sessions=1,
        lag_sessions=0,
    )
    config = FactorResearchConfig.model_validate(raw)
    code = b"value = 1\n"
    sha = hashlib.sha256(code).hexdigest()
    code_obj = SimpleNamespace(code_utf8=code, code_sha256=sha, code_object_id=uuid4())
    version = uuid4()
    snap = research_snapshot_input(
        config,
        factor_version_id=version,
        code_sha256=sha,
        exposure_history=ExposureHistory(),
    )
    prefix, _, binding = build_candidate_projection(config, snap, market, code_obj, 0)
    scope = {
        "owner_id": str(OWNER),
        "run_id": str(uuid4()),
        "attempt_id": str(uuid4()),
        "generation": 1,
    }
    ids = {
        k: str(uuid4())
        for k in ("cutoff", "evaluation", "source", "read", "plan", "spec", "admission")
    }
    source_read = dict(
        id=ids["read"],
        **scope,
        source_object_id=str(code_obj.code_object_id),
        source_id=ids["source"],
        witness_sha256="c" * 64,
    )
    document = dict(
        schema_version="mineagent.configured-factor-input-witness.v1",
        **scope,
        evaluation_id=ids["evaluation"],
        source_id=ids["source"],
        plan_id=ids["plan"],
        source_read_witness_id=ids["read"],
        source_read_witness_sha256=source_read["witness_sha256"],
        dataset_id=str(TARGET),
        dataset_manifest_sha256=market.dataset.content_sha256,
        availability_revision_sha256=market.dataset.availability_revision_sha256,
        calendar_sha256=market.calendar.sha256,
        input_sha256=digest(binding.model_dump(mode="json")),
        projection_sha256=prefix.sha256,
        source_artifacts=dict(market.artifact_sha256),
        files=[x.model_dump(mode="json") for x in binding.files],
        execution_id=ids["cutoff"],
        admission_id=ids["admission"],
    )
    row = {**document, "document": document, "witness_sha256": digest(document)}
    key = f"factors/{OWNER}/objects/{sha}.py"
    blobs[key] = code
    tables = {
        "factor_cutoff_executions": [
            dict(
                id=ids["cutoff"],
                **scope,
                evaluation_id=ids["evaluation"],
                evaluated_source_id=ids["source"],
                cutoff_ordinal=0,
                candidate_admission_id=ids["admission"],
                projection=prefix.model_dump(mode="json"),
            )
        ],
        "factor_evaluation_starts": [
            dict(
                id=ids["evaluation"],
                **scope,
                snapshot_input=snap.model_dump(mode="json"),
                input_sha256=snap.sha256,
            )
        ],
        "factor_generation_sources": [
            dict(
                id=ids["source"],
                **scope,
                factor_version_id=str(version),
                source_sha256=sha,
            )
        ],
        "factor_source_read_witnesses": [source_read],
        "factor_research_specs": [
            {
                "id": ids["spec"],
                "owner_id": scope["owner_id"],
                "run_id": scope["run_id"],
                "config": config.model_dump(mode="json"),
            }
        ],
        "factor_analysis_plans": [
            {
                "id": ids["plan"],
                "owner_id": scope["owner_id"],
                "run_id": scope["run_id"],
                "spec_id": ids["spec"],
                "source_policy": policy.model_dump(mode="json"),
                "source_policy_sha256": digest(policy),
                "config_sha256": config.sha256,
                "input_sha256": config.input.sha256,
                "label_source_dataset_id": str(TARGET),
                "label_source_manifest_sha256": ds["content_sha256"],
            }
        ],
        "factor_objects": [
            {
                "id": str(code_obj.code_object_id),
                "owner_id": str(OWNER),
                "object_kind": "code",
                "sha256": sha,
                "size_bytes": len(code),
                "storage_key": key,
            }
        ],
        "research_process_admissions": [
            dict(
                id=ids["admission"],
                **scope,
                logical_operation_id=ids["cutoff"],
                input_sha256=document["input_sha256"],
                role="factor_cutoff_candidate",
                profile=prefix.candidate_profile,
                image_id=prefix.candidate_image,
                ipc_policy="none",
                ticket_sha256=None,
                schema_version="mineagent.process-admission.v2",
                launch_ordinal=1,
            )
        ],
    }
    return (
        [row],
        {
            "tables": tables,
            "datasets": [ds],
            "read_bytes": read,
            "declared_upload": True,
        },
        blobs,
        ds,
    )


def test_actual_input_witness_rebuilds_from_original_uploads(witness):
    rows, kwargs, _, _ = witness
    validate_configured_input_closure(rows, **kwargs)


def test_old_cold_gate_cannot_read_new_basket(witness):
    rows, kwargs, _, _ = witness
    kwargs["declared_upload"] = False
    with pytest.raises(ValueError):
        validate_configured_input_closure(rows, **kwargs)


def test_original_csv_mutation_breaks_actual_input_reconstruction(witness):
    rows, kwargs, blobs, ds = witness
    key = ds["storage_key"] + "/sources/0/source.csv"
    blobs[key] += b" "
    with pytest.raises(ValueError):
        validate_configured_input_closure(rows, **kwargs)


@pytest.mark.parametrize(
    "revision", ["029_research_workflow", "030_declared_upload_basket"]
)
def test_snapshot_history_reconstructs_nonempty_declared_witness(
    witness, tmp_path, monkeypatch, revision
):
    """Real history function + parser/bytes; DB and unrelated release read doubles."""
    from uuid import UUID

    from backend.app import execution_snapshot_history as history
    from backend.app import factor_analysis_history
    from backend.app.factor_native_io import native_storage_guard

    rows, kwargs, blobs, ds = witness
    tables = dict(kwargs["tables"], factor_configured_input_witnesses=rows)
    plan_raw = tables["factor_analysis_plans"][0]
    plan = SimpleNamespace(label_source_dataset_id=UUID(ds["id"]), _raw=plan_raw)
    dataset = SimpleNamespace(_raw=ds)
    for key, raw in blobs.items():
        path = tmp_path / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    (tmp_path / "factors").mkdir(exist_ok=True)

    class Values(list):
        def all(self):
            return self

    class Session:
        def scalar(self, statement):
            return plan

        def get(self, model, identity):
            assert str(identity) == ds["id"]
            return dataset

        def scalars(self, statement):
            if "alembic_version" in str(statement):
                return Values([revision])
            entity = statement.column_descriptions[0]["entity"]
            return Values(
                SimpleNamespace(_raw=row) for row in tables[entity.__tablename__]
            )

    monkeypatch.setattr(factor_analysis_history, "row_document", lambda row: row._raw)
    monkeypatch.setattr(history, "check_exposure_history", lambda *args: None)
    publication = SimpleNamespace(
        owner_id=UUID(plan_raw["owner_id"]), run_id=UUID(plan_raw["run_id"])
    )
    with native_storage_guard(tmp_path):
        if revision == "029_research_workflow":
            with pytest.raises(ValueError):
                history.check_configured_history(
                    Session(), publication, SimpleNamespace(storage_root=tmp_path)
                )
        else:
            history.check_configured_history(
                Session(), publication, SimpleNamespace(storage_root=tmp_path)
            )
            raw_csv = tmp_path / ds["storage_key"] / "sources/0/source.csv"
            raw_csv.write_bytes(raw_csv.read_bytes() + b" ")
            with pytest.raises(ValueError):
                history.check_configured_history(
                    Session(), publication, SimpleNamespace(storage_root=tmp_path)
                )

"""Nonempty retained analysis validator; synthetic dependency/process rows, not E2E."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.encoders import jsonable_encoder

from backend.app import factor_analysis_backup as backup
from backend.app.experiment_services import dataset_identities
from backend.app.factor_services import public
from backend.app.rdagent_configured_exposure import PURPOSE, exposure_provenance
from tests.rdagent.test_rdagent_configured_source import retained  # noqa:F401
from tests.rdagent.test_rdagent_configured_source_service import setup_source  # noqa:F401


@pytest.fixture(params=[False, True])
def graph(setup_source, request):  # noqa: F811
    s = setup_source
    if request.param:
        from packages.rdagent_adapter.contracts import FactorResearchConfig

        raw = s.config.model_dump(mode="json")
        raw["controller_profile"] = "mineagent.rdagent-orchestrator.v3"
        raw["max_iterations"] = 1
        raw["budget"]["wall_milliseconds"] += 60000
        s.config = FactorResearchConfig.model_validate(raw)
        s.plan.config_sha256 = s.config.sha256
    s.plan.qlib_image_id = s.config.runtime.analysis_image
    s.plan.qlib_installed_parent_image_id = "sha256:" + "b" * 64
    s.plan.analysis_dependency_lock_sha256 = next(
        x.sha256 for x in s.config.runtime.dependency_locks if x.role == "analysis"
    )
    s.plan.created_at = datetime.now(UTC)
    tables = {k: [] for k in backup.FIELDS}
    for name, model in [
        ("factor_analysis_plans", s.plan),
        ("dataset_versions", s.dataset),
    ]:
        values = jsonable_encoder(public(model))
        tables[name] = [{k: values[k] for k in backup.FIELDS[name].split()}]
    scope = {"owner_id": str(s.fence.owner_id), "run_id": str(s.fence.run_id)}
    tables["data_usage_exposures"] = [
        dict(
            id=str(uuid4()),
            **scope,
            experiment_id=None,
            split_version_id=None,
            dataset_version_id=str(s.dataset.id),
            source_key="rd-configured-source:" + str(s.plan.id),
            identities=dataset_identities(s.dataset),
            start_date=str(s.dataset.start_date),
            end_date=str(s.dataset.end_date),
            purpose=PURPOSE,
            provenance=exposure_provenance(s.plan),
            created_at=datetime.now(UTC).isoformat(),
        )
    ]
    deps = {
        name: []
        for name in (
            "factor_research_specs",
            "factor_execution_snapshot_publications",
            "factor_execution_snapshot_outcomes",
            "research_process_admissions",
            "research_process_receipts",
            "research_process_states",
            "research_budget_reservations",
            "research_budget_settlements",
            "factor_evaluation_starts",
            "factor_cutoff_executions",
            "factor_generation_sources",
            "factor_source_read_witnesses",
            "factor_objects",
        )
    }
    deps["factor_research_specs"] = [
        dict(id=str(s.plan.spec_id), **scope, config=s.config.model_dump(mode="json"))
    ]

    def read(key, maximum):
        raw = (s.settings.storage_root / key).read_bytes()
        assert len(raw) <= maximum
        return raw

    kwargs = {
        "research_closure": {
            "schema_version": "mineagent.research-restore-closure.v7",
            "tables": deps,
        },
        "rd_source_closure": {
            "schema_version": "mineagent.rd-source-restore-closure.v4",
            "tables": {},
            "external_bindings": {
                "runs": [
                    {
                        "id": scope["run_id"],
                        "owner_id": scope["owner_id"],
                        "engine": "rdagent",
                        "purpose": "factor_research",
                    }
                ]
            },
        },
        "execution_closure": {
            "schema_version": "mineagent.execution-snapshot-restore-closure.v2",
            "tables": {},
        },
        "factor_closure": {"tables": {}},
        "read_bytes": read,
        "maximum_rows": 1000,
    }
    if request.param:
        kwargs["research_closure"]["schema_version"] = (
            "mineagent.research-restore-closure.v11"
        )
        kwargs["rd_source_closure"]["schema_version"] = (
            "mineagent.rd-source-restore-closure.v6"
        )
    return s, {"schema_version": backup.SCHEMA, "tables": tables}, kwargs


def test_nonempty_declared_analysis(graph):
    _, closure, kwargs = graph
    backup.validate_closure(closure, **kwargs)


@pytest.fixture
def completed(graph):
    import hashlib
    from uuid import UUID

    from backend.app.factor_analysis_canonical import read_objective
    from backend.app.rdagent_configured_source import derive_configured_labels
    from packages.factors.contracts import (
        ExposureHistory,
        FeatureRow,
        canonical_bytes,
        digest,
        rows_digest,
    )
    from packages.factors.execution_snapshot import ExecutionSnapshotManifest
    from packages.qlib_adapter.contracts import DirectDay, DirectResult
    from packages.qlib_adapter.direct_wire import DirectInput, write_input
    from packages.qlib_adapter.execution_binding import bind_completed_snapshot
    from packages.qlib_adapter.objective import measure_direct_objective
    from packages.rdagent_adapter.configured_source import parse_retained_source
    from packages.rdagent_adapter.host_evidence import research_snapshot_input
    from packages.research_runtime.analysis_process_journal import AnalysisInputBinding
    from tests.structured.test_operator import declared_input

    s, closure, kw = graph
    own = closure["tables"]
    deps = kw["research_closure"]["tables"]
    plan = own["factor_analysis_plans"][0]
    scope = {
        "owner_id": plan["owner_id"],
        "run_id": plan["run_id"],
        "attempt_id": str(uuid4()),
        "generation": 1,
    }
    exposure = own["data_usage_exposures"][0]
    exposure["created_at"] = "2026-10-29T01:44:20.123450+00:00"
    from packages.factors.contracts import DateWindow, ExposureClaim

    history = ExposureHistory(
        claims=(
            ExposureClaim(
                origin_namespace="mineagent.local.exposure",
                evidence_id=exposure["id"],
                evidence_sha256=digest(exposure),
                window=DateWindow(
                    start=exposure["start_date"], end=exposure["end_date"]
                ),
                purpose=exposure["purpose"],
                identity_mapping="unresolved",
                mapping_sha256=None,
            ),
        ),
        completeness="partial",
        unresolved_reasons=("source_history_incomplete",),
    )
    snap = research_snapshot_input(
        s.config,
        factor_version_id=uuid4(),
        code_sha256="a" * 64,
        exposure_history=history,
    )
    deps["factor_evaluation_starts"] = [
        dict(id=str(uuid4()), **scope, snapshot_input=snap.model_dump(mode="json"))
    ]
    source = parse_retained_source(**{**s.args, "input_names": ("close",)})
    objective = read_objective(plan)
    labels = derive_configured_labels(source, objective)
    day = source.calendar.sessions[1]
    rows = tuple(
        FeatureRow(
            event_time=day.close_time,
            session_date=day.session_date,
            listing_id=l.listing_id,
            factor_version_id=snap.version.factor_version_id,
            available_at=day.close_time,
            value=float(i),
            value_state="valid",
            input_window_start=s.config.input.window.start,
            input_window_end=s.config.input.window.end,
            input_evidence_digest="a" * 64,
            required_input_available_at=day.close_time,
            membership_available_at=day.close_time,
            lag_available_at=day.close_time,
        )
        for i, l in enumerate(labels.rows)
    )
    raw_rows = canonical_bytes(
        {
            "schema_version": "mineagent.execution-feature-rows.v1",
            "input_sha256": snap.sha256,
            "rows": [r.model_dump(mode="json") for r in rows],
        }
    )
    sha = hashlib.sha256(raw_rows).hexdigest()
    template = declared_input().snapshot.model_dump(mode="json")
    template.update(
        **scope,
        input=snap.model_dump(mode="json"),
        input_sha256=snap.sha256,
        row_count=len(rows),
        valid_count=len(rows),
        output_rows_sha256=rows_digest(rows),
        expected_cutoffs=1,
    )
    template["partitions"] = template["partitions"][:1]
    part = template["partitions"][0]
    part.update(ordinal=0, row_count=len(rows), valid_count=len(rows))
    part["values"].update(
        owner_id=scope["owner_id"],
        storage_key=f"factors/{scope['owner_id']}/objects/{part['values']['sha256']}.parquet",
    )
    template["assembled_rows"].update(
        owner_id=scope["owner_id"],
        sha256=sha,
        size_bytes=len(raw_rows),
        storage_key=f"factors/{scope['owner_id']}/objects/{sha}.json",
    )
    manifest = ExecutionSnapshotManifest.model_validate(template)
    path = s.settings.storage_root / manifest.assembled_rows.storage_key
    path.parent.mkdir(parents=True)
    path.write_bytes(raw_rows)
    publication = dict(
        id=str(manifest.snapshot_id),
        **scope,
        manifest=manifest.model_dump(mode="json"),
        manifest_sha256=digest(manifest),
    )
    deps["factor_execution_snapshot_publications"] = [publication]
    deps["factor_execution_snapshot_outcomes"] = [
        dict(publication_id=publication["id"], **scope)
    ]
    projection = bind_completed_snapshot(
        snapshot_id=manifest.snapshot_id,
        snapshot_input=snap,
        output_sha256=rows_digest(rows),
        rows=rows,
        label_manifest=labels.manifest,
        labels=labels.rows,
        calendar_sessions=tuple(x.session_date for x in source.calendar.sessions),
    )
    value = DirectInput(
        protocol=projection.protocol,
        factors=projection.factors,
        labels=projection.labels,
        calendar_sessions=projection.calendar_sessions,
    )
    raw = write_input(value)
    aid = str(uuid4())
    admission_id = str(uuid4())
    reservation_id = str(uuid4())
    intent = dict(
        id=aid,
        **scope,
        plan_id=plan["id"],
        snapshot_id=publication["id"],
        label_source_id=plan["label_source_dataset_id"],
        label_manifest=labels.manifest.model_dump(mode="json"),
        label_manifest_sha256=labels.manifest.sha256,
        label_recipe_sha256=labels.label_recipe_sha256,
        protocol=value.protocol.model_dump(mode="json"),
        protocol_sha256=value.protocol.sha256,
        analysis_input_sha256=hashlib.sha256(raw).hexdigest(),
        analysis_input_size_bytes=len(raw),
        created_at=datetime.now(UTC).isoformat(),
    )
    own["factor_direct_analysis_intents"] = [intent]
    binding = AnalysisInputBinding(
        analysis_id=UUID(aid),
        snapshot_id=manifest.snapshot_id,
        label_source_id=UUID(intent["label_source_id"]),
        protocol_sha256=intent["protocol_sha256"],
        analysis_input_sha256=intent["analysis_input_sha256"],
        analysis_input_size_bytes=len(raw),
    )
    deps["research_process_admissions"] = [
        dict(
            id=admission_id,
            **scope,
            logical_operation_id=aid,
            role="native_evaluator",
            profile="mineagent.qlib-direct-analysis.v1",
            image_id=plan["qlib_image_id"],
            input_sha256=digest(binding),
            reservation_id=reservation_id,
        )
    ]
    deps["research_process_states"] = [
        dict(admission_id=admission_id, **scope, status="stopped")
    ]
    deps["research_budget_reservations"] = [
        dict(
            id=reservation_id,
            **scope,
            state="settled",
            amounts={"evaluator_executions": 1},
        )
    ]
    deps["research_budget_settlements"] = [
        dict(
            reservation_id=reservation_id,
            **scope,
            charge={"evaluator_executions": 1},
            outcome="conservative_unknown",
        )
    ]
    outcome = dict(
        analysis_id=aid,
        **scope,
        admission_id=admission_id,
        created_at=datetime.now(UTC).isoformat(),
    )
    for kind in ("launch_intent", "launch_identity", "stop", "extraction"):
        payload = (
            {"inputs": binding.model_dump(mode="json")}
            if kind == "launch_intent"
            else {"synthetic_receipt": kind}
        )
        receipt = dict(
            id=str(uuid4()),
            **scope,
            admission_id=admission_id,
            kind=kind,
            payload=payload,
            payload_sha256=digest(payload),
        )
        deps["research_process_receipts"].append(receipt)
        if kind != "launch_intent":
            outcome.update(
                {
                    kind + "_receipt_id": receipt["id"],
                    kind + "_receipt_sha256": receipt["payload_sha256"],
                    kind + "_kind": kind,
                }
            )
    result = DirectResult(
        protocol_sha256=value.protocol.sha256,
        days=(
            DirectDay(
                session=day.session_date,
                pair_count=len(rows),
                ic=None,
                rank_ic=None,
                reason="constant_cross_section",
            ),
        ),
        ic=None,
        rank_ic=None,
        defined_days=0,
        total_pairs=len(rows),
    )
    measurement = measure_direct_objective(objective, projection, result)
    raw = canonical_bytes(result)
    path = (
        s.settings.storage_root
        / f"research-processes/{scope['owner_id']}/{admission_id}/output/analysis.json"
    )
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    outcome.update(
        result=result.model_dump(mode="json"),
        result_canonical=raw.decode(),
        result_sha256=hashlib.sha256(raw).hexdigest(),
        result_size_bytes=len(raw),
        measurement=measurement.model_dump(mode="json"),
        measurement_canonical=canonical_bytes(measurement).decode(),
        measurement_sha256=digest(measurement),
    )
    own["factor_direct_analysis_outcomes"] = [outcome]
    return closure, kw


def test_completed_nonempty_direct_analysis(completed):
    closure, kw = completed
    backup.validate_closure(closure, **kw)


def test_completed_postgres_timestamp_projection(completed):
    closure, kw = completed
    closure["tables"]["data_usage_exposures"][0]["created_at"] = (
        "2026-10-29T01:44:20.12345+00:00"
    )
    backup.validate_closure(closure, **kw)


@pytest.mark.parametrize(
    "fault",
    [
        "owner",
        "result_bytes",
        "result_hash",
        "measurement",
        "charge",
        "stop",
        "receipt",
        "exposure_hash",
        "exposure_instant",
    ],
)
def test_completed_analysis_rejects_changed_authority(completed, fault):
    closure, kw = completed
    own = closure["tables"]
    deps = kw["research_closure"]["tables"]
    outcome = own["factor_direct_analysis_outcomes"][0]
    if fault == "owner":
        outcome["owner_id"] = str(uuid4())
    elif fault == "result_bytes":
        original = kw["read_bytes"]
        kw["read_bytes"] = lambda key, maximum: (
            original(key, maximum) + (b" " if key.endswith("analysis.json") else b"")
        )
    elif fault == "result_hash":
        outcome["result_sha256"] = "f" * 64
    elif fault == "measurement":
        outcome["measurement"]["status"] = "passed"
    elif fault == "charge":
        deps["research_budget_settlements"][0]["charge"] = {}
    elif fault == "stop":
        deps["research_process_states"][0]["status"] = "running"
    elif fault == "receipt":
        deps["research_process_receipts"][-1]["payload_sha256"] = "f" * 64
    elif fault == "exposure_hash":
        own["data_usage_exposures"][0]["provenance"]["objective_sha256"] = "f" * 64
    else:
        own["data_usage_exposures"][0]["created_at"] = (
            "2026-10-29T01:44:20.123451+00:00"
        )
    with pytest.raises(ValueError):
        backup.validate_closure(closure, **kw)


@pytest.mark.parametrize(
    "stamp",
    [
        None,
        1,
        "2026-10-29T01:44:20.12345Z",
        "2026-10-29T01:44:20.12345-04:00",
        "2026-10-29T01:44:20.1234500+00:00",
        "2026-99-29T01:44:20+00:00",
    ],
)
def test_exposure_timestamp_never_uses_generic_string_conversion(stamp):
    from backend.app.rdagent_configured_exposure_backup import _retained_exposure_digest

    with pytest.raises(ValueError):
        _retained_exposure_digest({"created_at": stamp})


@pytest.mark.parametrize("micros", [0, 1, 10, 100, 1000, 10000, 100000, 123450, 123456])
def test_postgres_fractional_projection_preserves_exact_live_hash(micros):
    from backend.app.rdagent_configured_exposure_backup import _retained_exposure_digest
    from packages.factors.contracts import digest

    live = {
        "created_at": datetime(2026, 10, 29, 1, 44, 20, micros, tzinfo=UTC).isoformat(),
        "unchanged": {"value": -0.0},
    }
    pg = dict(live)
    if micros:
        pg["created_at"] = (
            live["created_at"].removesuffix("+00:00").rstrip("0") + "+00:00"
        )
    assert _retained_exposure_digest(pg) == digest(live)
    assert pg["created_at"] != live["created_at"] or micros in (0, 1, 123456)


def test_analysis_failure_diagnostic_identifies_fixed_module_not_payload():
    import json

    from backend.app.rdagent_configured_exposure_backup import _retained_exposure_digest
    from tests.integration.cold_failure_diagnostic import failure_projection

    try:
        _retained_exposure_digest({"created_at": "SECRET timestamp"})
    except ValueError as error:
        projection = failure_projection(error)
    assert "SECRET" not in json.dumps(projection)
    assert [(f["module"], f["function"]) for f in projection["chain"][0]["frames"]] == [
        ("backend.app.rdagent_configured_exposure_backup", "_retained_exposure_digest")
    ]

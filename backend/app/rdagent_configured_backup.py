"""022 configured witness cold reconstruction from retained source bytes.

The enclosing restore validator validates old RD/process/object graphs first and
supplies an ownership-safe bounded byte reader. No native parser is imported.
"""

import hashlib
from types import SimpleNamespace
from uuid import UUID

from backend.app.declared_upload_backup import retained_artifacts
from backend.app.rdagent_configured_inputs import build_candidate_projection
from backend.app.rdagent_snapshot_config import parse_snapshot_config
from packages.factors.contracts import SnapshotInput, canonical_bytes, digest
from packages.rdagent_adapter.configured_source import (
    ConfiguredSourcePolicy,
    parse_retained_source,
)
from packages.rdagent_adapter.host_evidence import research_snapshot_input
from packages.strategy_sdk import JsonLimits, parse_json


def validate_configured_input_closure(
    witnesses,
    *,
    tables,
    datasets,
    read_bytes,
    max_rows=100000,
    provider_config=False,
    workflow_config=False,
    composed_controller=False,
    declared_upload=False,
    seeded_controller=False,
):
    """read_bytes(storage_key, maximum) must reject unsafe paths/oversized files.

    Call only after independently validating the supplied source/analysis/process
    table inventories. This check does not substitute for those closures.
    """
    if type(witnesses) is not list or len(witnesses) > max_rows:
        raise ValueError("configured witness inventory bound")
    names = (
        "factor_cutoff_executions",
        "factor_evaluation_starts",
        "factor_generation_sources",
        "factor_source_read_witnesses",
        "factor_research_specs",
        "factor_analysis_plans",
        "factor_objects",
        "research_process_admissions",
    )
    indexes = {}
    for name in names:
        rows = tables[name]
        if type(rows) is not list or len(rows) > max_rows:
            raise ValueError("configured dependency inventory bound")
        index = {str(row["id"]): row for row in rows}
        if len(index) != len(rows):
            raise ValueError("configured duplicate dependency")
        indexes[name] = index
    dataset_index = {str(row["id"]): row for row in datasets}
    if len(dataset_index) != len(datasets):
        raise ValueError("configured duplicate dataset")
    seen = set()
    for witness in witnesses:
        execution_id = str(witness["execution_id"])
        if execution_id in seen:
            raise ValueError("configured duplicate execution")
        seen.add(execution_id)
        c = indexes["factor_cutoff_executions"][execution_id]
        e = indexes["factor_evaluation_starts"][str(c["evaluation_id"])]
        src = indexes["factor_generation_sources"][str(c["evaluated_source_id"])]
        r = indexes["factor_source_read_witnesses"][
            str(witness["source_read_witness_id"])
        ]
        p = indexes["factor_analysis_plans"][str(witness["plan_id"])]
        spec = indexes["factor_research_specs"][str(p["spec_id"])]
        a = indexes["research_process_admissions"][str(witness["admission_id"])]
        d = dataset_index[str(witness["dataset_id"])]
        scope = {
            key: witness[key]
            for key in ("owner_id", "run_id", "attempt_id", "generation")
        }
        if any(
            any(row[key] != value for key, value in scope.items())
            for row in (c, e, src, r, a)
        ):
            raise ValueError("configured witness scope")
        if any(row["owner_id"] != scope["owner_id"] for row in (p, spec, d)) or any(
            row["run_id"] != scope["run_id"] for row in (p, spec)
        ):
            raise ValueError("configured owner/run scope")
        config = parse_snapshot_config(
            spec["config"],
            owner_id=spec["owner_id"],
            provider_config=provider_config,
            workflow_config=workflow_config,
            composed_controller=composed_controller,
            run_id=spec["run_id"],
            seeded_controller=seeded_controller,
            json_mode=True,
        )
        policy = ConfiguredSourcePolicy.model_validate_json(
            canonical_bytes(p["source_policy"])
        )
        if (
            p["config_sha256"],
            p["input_sha256"],
            p["source_policy_sha256"],
            p["label_source_dataset_id"],
            p["label_source_manifest_sha256"],
        ) != (
            config.sha256,
            config.input.sha256,
            digest(policy),
            str(d["id"]),
            d["content_sha256"],
        ):
            raise ValueError("configured plan binding")
        key = f"datasets/{scope['owner_id']}/{d['id']}"
        if d["storage_key"] != key:
            raise ValueError("configured dataset path")
        manifest = read_bytes(key + "/manifest.json", 2 * 1024**2)
        parsed = parse_json(
            manifest,
            limits=JsonLimits(
                max_bytes=2 * 1024**2, max_nodes=100000, max_items=100000
            ),
        )
        if parsed != d["manifest"]:
            raise ValueError("configured dataset manifest")
        market = parse_retained_source(
            dataset_id=UUID(str(d["id"])),
            manifest_sha256=d["content_sha256"],
            manifest_bytes=manifest,
            artifacts=retained_artifacts(
                parsed, key, read_bytes, declared_upload=declared_upload
            ),
            declared_upload=declared_upload,
            owner_id=UUID(str(scope["owner_id"])),
            policy=policy,
            input_names=tuple(field.name for field in config.input.input_fields),
        )
        if (
            market.dataset,
            market.universe,
            market.calendar.sha256,
            market.calendar.timezone,
            market.input_fields,
        ) != (
            config.input.dataset,
            config.input.calculation_universe,
            config.input.calendar_sha256,
            config.input.market_timezone,
            config.input.input_fields,
        ):
            raise ValueError("configured frozen source drift")
        obj = indexes["factor_objects"][str(r["source_object_id"])]
        raw = read_bytes(obj["storage_key"], 131072)
        if (
            obj["owner_id"],
            obj["object_kind"],
            obj["sha256"],
            obj["size_bytes"],
            r["source_id"],
        ) != (
            scope["owner_id"],
            "code",
            hashlib.sha256(raw).hexdigest(),
            len(raw),
            src["id"],
        ) or src["source_sha256"] != obj["sha256"]:
            raise ValueError("configured source object drift")
        code = SimpleNamespace(
            code_utf8=raw,
            code_sha256=obj["sha256"],
            code_object_id=UUID(str(obj["id"])),
        )
        snapshot = SnapshotInput.model_validate_json(
            canonical_bytes(e["snapshot_input"])
        )
        expected = research_snapshot_input(
            config,
            factor_version_id=UUID(str(src["factor_version_id"])),
            code_sha256=code.code_sha256,
            exposure_history=snapshot.exposure_history,
        )
        if snapshot != expected or e["input_sha256"] != snapshot.sha256:
            raise ValueError("configured snapshot input drift")
        prefix, _, binding = build_candidate_projection(
            config, snapshot, market, code, c["cutoff_ordinal"]
        )
        document = {
            "schema_version": "mineagent.configured-factor-input-witness.v1",
            **scope,
            "evaluation_id": str(e["id"]),
            "source_id": str(src["id"]),
            "plan_id": str(p["id"]),
            "source_read_witness_id": str(r["id"]),
            "source_read_witness_sha256": r["witness_sha256"],
            "dataset_id": str(d["id"]),
            "dataset_manifest_sha256": market.dataset.content_sha256,
            "availability_revision_sha256": market.dataset.availability_revision_sha256,
            "calendar_sha256": market.calendar.sha256,
            "input_sha256": digest(binding.model_dump(mode="json")),
            "projection_sha256": prefix.sha256,
            "source_artifacts": dict(market.artifact_sha256),
            "files": [item.model_dump(mode="json") for item in binding.files],
            "execution_id": str(c["id"]),
            "admission_id": str(a["id"]),
        }
        if (
            witness["document"] != document
            or witness["witness_sha256"] != digest(document)
            or len(canonical_bytes(document)) > 4096
        ):
            raise ValueError("configured witness bytes drift")
        if any(
            witness[key] != document[key]
            for key in (
                "dataset_manifest_sha256",
                "availability_revision_sha256",
                "calendar_sha256",
                "input_sha256",
                "projection_sha256",
            )
        ):
            raise ValueError("configured witness columns drift")
        if (
            (
                a["logical_operation_id"],
                a["input_sha256"],
                a["role"],
                a["profile"],
                a["image_id"],
                a["ipc_policy"],
                a["ticket_sha256"],
                a["schema_version"],
                a["launch_ordinal"],
            )
            != (
                c["id"],
                document["input_sha256"],
                "factor_cutoff_candidate",
                prefix.candidate_profile,
                prefix.candidate_image,
                "none",
                None,
                "mineagent.process-admission.v2",
                1,
            )
            or c["projection"] != prefix.model_dump(mode="json")
            or c["candidate_admission_id"] != a["id"]
        ):
            raise ValueError("configured admitted input drift")
    configured_runs = {
        (row["owner_id"], row["run_id"])
        for row in indexes["factor_analysis_plans"].values()
    }
    required = {
        str(row["id"])
        for row in indexes["factor_cutoff_executions"].values()
        if (row["owner_id"], row["run_id"]) in configured_runs
    }
    if seen != required:
        raise ValueError("configured witness incomplete")

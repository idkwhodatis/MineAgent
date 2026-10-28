"""Leased factor-package admission on the existing trusted research worker.

No model/code execution. Original archives and source evidence remain immutable;
local snapshots are explicitly derived identities, not renamed originals.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from sqlalchemy import select

from backend.app.core.atomic_storage import _prepare_directory, publish_bytes
from backend.app.core.db import create_db_engine, session_factory
from backend.app.factor_import_models import FactorImportAdmission, FactorImportEnvelope
from backend.app.factor_models import (
    Factor,
    FactorEvaluation,
    FactorObject,
    FactorVersion,
    FeatureSnapshot,
    FeatureSnapshotObject,
)
from backend.app.factor_services import (
    OBJECT_TYPES,
    check_deadline,
    owned,
    publication,
    publish_object,
)
from backend.app.models import DatasetVersion, utcnow
from backend.app.services import DomainError
from backend.app.storage import read_verified
from packages.factors.contracts import (
    ExposureHistory,
    FactorDefinition,
    FeatureSnapshotManifest,
    ObjectRef,
    SnapshotInput,
    canonical_bytes,
    digest,
    imported_verification,
)
from packages.factors.package import decode_package
from runtimes.factor_parser.supervisor import (
    DockerParser,
    ParserIsolationError,
    assert_quiescent,
    factor_fence,
    prepare_operation,
)

MAX_SNAPSHOTS = 2
MAX_IMPORT_ROWS = 5000


@dataclass(frozen=True)
class Claim:
    id: UUID
    owner_id: UUID
    generation: int
    token: UUID
    archive_object_id: UUID
    archive_sha256: str
    image_id: str


def operation_path(settings, claim, index):
    identity = uuid5(claim.id, f"generation:{claim.generation}:snapshot:{index}")
    return (
        Path(settings.storage_root)
        / "factors"
        / str(claim.owner_id)
        / "staging"
        / str(identity)
    )


def claim_one(session, admission_id=None):
    query = (
        select(FactorImportAdmission)
        .where(FactorImportAdmission.status == "queued")
        .order_by(FactorImportAdmission.created_at, FactorImportAdmission.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if admission_id is not None:
        query = query.where(FactorImportAdmission.id == UUID(str(admission_id)))
    row = session.scalar(query)
    if row is None:
        return None
    row.status = "processing"
    row.generation += 1
    row.lease_token = uuid4()
    row.lease_expires_at = utcnow() + timedelta(seconds=60)
    row.updated_at = utcnow()
    row.error_code = None
    claim = Claim(
        row.id,
        row.owner_id,
        row.generation,
        row.lease_token,
        row.archive_object_id,
        row.archive_sha256,
        row.parser_image_id,
    )
    session.commit()
    return claim


def fenced_admission(session, claim):
    row = session.scalar(
        select(FactorImportAdmission)
        .where(
            FactorImportAdmission.id == claim.id,
            FactorImportAdmission.owner_id == claim.owner_id,
        )
        .with_for_update()
    )
    if (
        row is None
        or row.status != "processing"
        or row.generation != claim.generation
        or row.lease_token != claim.token
        or row.lease_expires_at <= utcnow()
    ):
        raise DomainError(
            "factor_import_fence_lost", "Factor import lease is no longer current", 409
        )
    return row


def validate_payload(package):
    """Bound semantic metadata without touching native tabular bytes."""
    payload = package.manifest
    required = {
        "origin_namespace",
        "factor_id",
        "version_id",
        "parent_version_id",
        "ordinal",
        "definition",
        "definition_sha256",
        "code_sha256",
        "source_provenance",
        "snapshots",
        "source_datasets",
        "evaluations",
        "include_values",
        "history_completeness",
        "execution_binding_status",
        "exposure_claims",
    }
    if set(payload) != required:
        raise DomainError(
            "factor_package_schema", "Unsupported factor package payload", 422
        )
    if (
        type(payload["include_values"]) is not bool
        or not isinstance(payload["exposure_claims"], list)
        or len(payload["exposure_claims"]) > 256
    ):
        raise DomainError(
            "factor_package_schema", "Invalid package exposure or value mode", 422
        )
    definition = FactorDefinition.model_validate(payload["definition"])
    if (
        definition.sha256 != payload["definition_sha256"]
        or definition.code_sha256 != payload["code_sha256"]
    ):
        raise DomainError(
            "factor_package_integrity", "Factor definition hash differs", 422
        )
    source_version = UUID(payload["version_id"])
    UUID(payload["factor_id"])
    if (
        not isinstance(payload["origin_namespace"], str)
        or not 1 <= len(payload["origin_namespace"]) <= 180
    ):
        raise DomainError("factor_package_schema", "Invalid origin namespace", 422)
    raw_code = package.objects.get(f"objects/{definition.code_sha256}.py")
    if (
        raw_code is None
        or len(raw_code) > 131072
        or hashlib.sha256(raw_code).hexdigest() != definition.code_sha256
    ):
        raise DomainError(
            "factor_package_integrity", "Exact factor source is missing", 422
        )
    code = raw_code.decode("utf-8")
    definition.validate_source(code)
    if (
        not isinstance(payload["snapshots"], list)
        or len(payload["snapshots"]) > MAX_SNAPSHOTS
    ):
        raise DomainError(
            "factor_import_profile_limit", "Import supports at most two snapshots", 422
        )
    if (
        not isinstance(payload["source_datasets"], list)
        or len(payload["source_datasets"]) > MAX_SNAPSHOTS
    ):
        raise DomainError(
            "factor_import_profile_limit", "Source dataset profile exceeded", 422
        )
    manifests = []
    for value in payload["snapshots"]:
        manifest = FeatureSnapshotManifest.model_validate(value)
        if (
            manifest.input.version.factor_version_id != source_version
            or manifest.input.definition.sha256 != definition.sha256
        ):
            raise DomainError(
                "factor_package_integrity", "Snapshot version binding differs", 422
            )
        partition = [obj for obj in manifest.objects if obj.role == "values_partition"]
        if manifest.row_count > MAX_IMPORT_ROWS or len(partition) != 1:
            raise DomainError(
                "factor_import_profile_limit",
                "Import supports one partition and at most5000rows per snapshot",
                422,
            )
        raw_manifest = package.objects.get(f"objects/{manifest.sha256}.json")
        if raw_manifest != canonical_bytes(manifest):
            raise DomainError(
                "factor_package_integrity", "Original snapshot manifest differs", 422
            )
        code_refs = [obj for obj in manifest.objects if obj.role == "code"]
        if any(
            obj.sha256 != definition.code_sha256 or obj.size_bytes != len(raw_code)
            for obj in code_refs
        ):
            raise DomainError(
                "factor_package_integrity",
                "Snapshot code reference differs from exact version source",
                422,
            )
        if partition[0].rows != manifest.row_count:
            raise DomainError(
                "factor_package_integrity", "Partition row inventory differs", 422
            )
        if payload["include_values"]:
            if len(code_refs) != 1:
                raise DomainError(
                    "factor_package_integrity",
                    "Value snapshot must retain its exact code object",
                    422,
                )
            for reference in manifest.objects:
                extension = OBJECT_TYPES[reference.role][0]
                content = package.objects.get(f"objects/{reference.sha256}.{extension}")
                if (
                    content is None
                    or len(content) != reference.size_bytes
                    or hashlib.sha256(content).hexdigest() != reference.sha256
                ):
                    raise DomainError(
                        "factor_package_integrity",
                        "Original snapshot object inventory is incomplete or inconsistent",
                        422,
                    )
        manifests.append(manifest)
    if len({m.snapshot_id for m in manifests}) != len(manifests):
        raise DomainError("factor_package_integrity", "Duplicate source snapshot", 422)
    return definition, code, manifests


def verify_dataset_integrity(dataset, settings, deadline):
    """Bounded full immutable inventory verification, with no native data parser."""
    manifest = dataset.manifest
    if manifest.get("schema_version") == "mineagent.factor-fixture-dataset.v1":
        encoded = canonical_bytes(manifest)
        if hashlib.sha256(encoded).hexdigest() != dataset.content_sha256:
            raise DomainError(
                "factor_dataset_integrity", "Fixture dataset identity differs", 409
            )
    elif manifest.get("content_hash") == dataset.content_sha256:
        from packages.marketdata.normalize import canonical_json

        expected = dict(manifest)
        expected.pop("content_hash")
        if (
            hashlib.sha256(canonical_json(expected)).hexdigest()
            != dataset.content_sha256
        ):
            raise DomainError(
                "factor_dataset_integrity", "Dataset content identity differs", 409
            )
        encoded = canonical_json(manifest)
    else:
        raise DomainError(
            "factor_dataset_unresolved", "Dataset manifest profile is unsupported", 422
        )
    actual = read_verified(
        settings.storage_root,
        f"{dataset.storage_key}/manifest.json",
        hashlib.sha256(encoded).hexdigest(),
        1_048_576,
    )
    if actual != encoded:
        raise DomainError(
            "factor_dataset_integrity", "Dataset physical manifest differs", 409
        )
    files = manifest.get("files", {})
    artifacts = manifest.get("artifact_files", {})
    if (
        not isinstance(files, dict)
        or not files
        or not isinstance(artifacts, dict)
        or len(files) + len(artifacts) > 128
    ):
        raise DomainError(
            "factor_dataset_integrity", "Dataset file inventory exceeds bounds", 409
        )
    inventory = dict(files)
    for name, sha in artifacts.items():
        if name in inventory and inventory[name] != sha:
            raise DomainError(
                "factor_dataset_integrity", "Dataset inventory conflicts", 409
            )
        inventory[name] = sha
    remaining = 128 * 1024 * 1024
    for name, sha in inventory.items():
        check_deadline(deadline)
        if (
            not isinstance(name, str)
            or len(name) > 200
            or not isinstance(sha, str)
            or len(sha) != 64
        ):
            raise DomainError(
                "factor_dataset_integrity", "Invalid dataset reference", 409
            )
        data = read_verified(
            settings.storage_root, f"{dataset.storage_key}/{name}", sha, remaining
        )
        remaining -= len(data)
    check_deadline(deadline)


def _dataset(session, owner_id, source, package, settings, deadline, admission_id):
    """Resolve immutable content, or import the closed synthetic dataset format."""
    expected = source.input.dataset
    existing = session.scalar(
        select(DatasetVersion)
        .where(
            DatasetVersion.owner_id == owner_id,
            DatasetVersion.content_sha256 == expected.content_sha256,
        )
        .order_by(DatasetVersion.id)
        .limit(1)
    )
    if existing is not None:
        verify_dataset_integrity(existing, settings, deadline)
        return existing
    matches = [
        d
        for d in package.manifest["source_datasets"]
        if isinstance(d, dict)
        and d.get("dataset_version_id") == str(expected.dataset_version_id)
        and d.get("content_sha256") == expected.content_sha256
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise DomainError("factor_package_integrity", "Duplicate source dataset", 422)
    value = matches[0]
    manifest = value.get("manifest")
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != "mineagent.factor-fixture-dataset.v1"
        or value.get("is_synthetic") is not True
        or value.get("source") != "synthetic_fixture"
        or manifest.get("is_synthetic") is not True
        or set(manifest.get("files", {})) != {"observations.json"}
    ):
        raise DomainError(
            "factor_source_dataset_unresolved",
            "Only explicit synthetic fixture source transfer is supported",
            422,
        )
    raw = package.objects.get(value.get("manifest_object_path"))
    if (
        raw != canonical_bytes(manifest)
        or hashlib.sha256(raw).hexdigest() != expected.content_sha256
    ):
        raise DomainError("factor_package_integrity", "Dataset manifest differs", 422)
    files = value.get("files")
    if (
        not isinstance(files, list)
        or len(files) != 1
        or files[0].get("name") != "observations.json"
    ):
        raise DomainError("factor_package_integrity", "Dataset inventory differs", 422)
    ref = files[0]
    content = package.objects.get(ref.get("object_path"))
    if (
        content is None
        or len(content) > 1_048_576
        or len(content) != ref.get("size_bytes")
        or hashlib.sha256(content).hexdigest() != ref.get("sha256")
        or manifest["files"]["observations.json"] != ref["sha256"]
    ):
        raise DomainError("factor_package_integrity", "Dataset input differs", 422)
    identity = uuid5(admission_id, "dataset:" + expected.content_sha256)
    key = f"factors/{owner_id}/datasets/{identity}"
    for name, data in (("observations.json", content), ("manifest.json", raw)):
        check_deadline(deadline)
        publish_bytes(
            Path(settings.storage_root) / key / name,
            data,
            sha256=hashlib.sha256(data).hexdigest(),
            durable_root=Path(settings.storage_root),
        )
    row = DatasetVersion(
        id=identity,
        owner_id=owner_id,
        name="Imported factor synthetic source (unverified)",
        market=value["market"],
        start_date=date.fromisoformat(value["start_date"]),
        end_date=date.fromisoformat(value["end_date"]),
        source="imported_factor_synthetic",
        is_synthetic=True,
        content_sha256=expected.content_sha256,
        manifest=manifest,
        storage_key=key,
    )
    session.add(row)
    session.flush()
    return row


def _finish(session, claim, package, settings, admission, parser_factory):
    row = fenced_admission(session, claim)
    existing = session.scalar(
        select(FactorImportEnvelope).where(
            FactorImportEnvelope.owner_id == claim.owner_id,
            FactorImportEnvelope.archive_sha256 == claim.archive_sha256,
        )
    )
    if existing:
        row.status = "succeeded"
        row.result_envelope_id = existing.id
        row.lease_token = None
        row.lease_expires_at = None
        row.updated_at = utcnow()
        return existing.id
    definition, code, sources = validate_payload(package)
    # Reusing a declared origin UUID with different source content is a conflict,
    # not permission to overwrite the old local binding.
    previous = session.scalars(
        select(FactorImportEnvelope)
        .where(
            FactorImportEnvelope.owner_id == claim.owner_id,
            FactorImportEnvelope.origin_namespace
            == package.manifest["origin_namespace"],
        )
        .order_by(FactorImportEnvelope.id)
        .limit(65)
    ).all()
    if len(previous) > 64:
        raise DomainError(
            "factor_import_profile_limit",
            "Source origin history exceeds the initial bounded import profile",
            422,
        )
    history_bytes = 0
    for envelope in previous:
        check_deadline(admission)
        history_bytes += len(canonical_bytes(envelope.origin_payload)) + len(
            canonical_bytes(envelope.local_bindings)
        )
        if history_bytes > 8 * 1024 * 1024:
            raise DomainError(
                "factor_import_profile_limit",
                "Source identity history exceeds byte budget",
                422,
            )
        if (
            envelope.origin_payload.get("version_id") == package.manifest["version_id"]
            and envelope.origin_payload.get("definition_sha256") != definition.sha256
        ):
            raise DomainError(
                "factor_origin_conflict",
                "Source version identity has conflicting content",
                409,
            )
    history = ExposureHistory(
        completeness="unknown", unresolved_reasons=("imported_history_unverified",)
    )
    same_origin = next(
        (
            e
            for e in previous
            if e.origin_payload.get("version_id") == package.manifest["version_id"]
            and e.origin_payload.get("definition_sha256") == definition.sha256
            and e.origin_payload.get("code_sha256") == definition.code_sha256
        ),
        None,
    )
    if same_origin is not None:
        version = owned(
            session,
            FactorVersion,
            claim.owner_id,
            UUID(same_origin.local_bindings["version_id"]),
        )
        if (
            version.definition_sha256 != definition.sha256
            or version.code_sha256 != definition.code_sha256
        ):
            raise DomainError(
                "factor_origin_conflict", "Stored source binding differs", 409
            )
        factor_id = version.factor_id
        version_id = version.id
        code_obj = owned(session, FactorObject, claim.owner_id, version.code_object_id)
    else:
        factor_id = uuid5(claim.id, "factor")
        version_id = uuid5(claim.id, "version")
        factor = Factor(
            id=factor_id,
            owner_id=claim.owner_id,
            name="Imported factor",
            category="imported",
        )
        code_obj = publish_object(
            session, claim.owner_id, "code", code.encode(), settings, admission
        )
        version = FactorVersion(
            id=version_id,
            owner_id=claim.owner_id,
            factor_id=factor_id,
            ordinal=1,
            parent_version_id=None,
            definition=definition.model_dump(mode="json"),
            definition_sha256=definition.sha256,
            code=code,
            code_sha256=definition.code_sha256,
            code_object_id=code_obj.id,
            source_kind="imported",
            source_event_key=f"archive:{claim.archive_sha256}",
            source_provenance={
                "history_completeness": "unknown",
                "origin": "imported",
                "source_archive_sha256": claim.archive_sha256,
                "source_version_id": package.manifest["version_id"],
                "source_claims": package.manifest["source_provenance"],
                "exposure_history": history.model_dump(mode="json"),
                "import_envelope_id": str(uuid5(claim.id, "envelope")),
            },
        )
        session.add(factor)
        session.flush()
        session.add(version)
        session.flush()
    retained = []
    for path, content in package.objects.items():
        if path.endswith(".py"):
            kind = "code"
        elif path.endswith(".parquet"):
            kind = "values_partition"
        else:
            kind = "native_evidence"
        # Existing immutable registrations may already use a narrower JSON kind.
        sha = hashlib.sha256(content).hexdigest()
        ext = OBJECT_TYPES[kind][0]
        obj = session.scalar(
            select(FactorObject).where(
                FactorObject.owner_id == claim.owner_id,
                FactorObject.storage_key
                == f"factors/{claim.owner_id}/objects/{sha}.{ext}",
            )
        )
        if obj:
            read_verified(settings.storage_root, obj.storage_key, sha, len(content))
        else:
            obj = publish_object(
                session, claim.owner_id, kind, content, settings, admission
            )
        retained.append({"source_path": path, "object_id": str(obj.id), "sha256": sha})
    bindings = {
        "factor_id": str(factor_id),
        "version_id": str(version_id),
        "source_version_id": package.manifest["version_id"],
        "objects": retained,
        "snapshots": [],
        "snapshot_ids": [],
        "origin_map": [],
        "execution_binding_status": "unresolved",
    }
    bindings["origin_map"].append(
        {
            "local_kind": "factor_version",
            "local_id": str(version_id),
            "local_sha256": definition.sha256,
            "source_id": package.manifest["version_id"],
            "source_sha256": definition.sha256,
        }
    )
    parser = None
    for index, source in enumerate(sources):
        check_deadline(admission)
        preferred = next(
            (
                m
                for e in previous
                for m in e.local_bindings.get("origin_map", [])
                if m.get("local_kind") == "dataset_version"
                and m.get("source_id") == str(source.input.dataset.dataset_version_id)
            ),
            None,
        )
        if preferred is not None:
            if preferred.get("source_sha256") != source.input.dataset.content_sha256:
                raise DomainError(
                    "factor_origin_conflict",
                    "Source dataset identity has conflicting content",
                    409,
                )
            dataset = owned(
                session, DatasetVersion, claim.owner_id, UUID(preferred["local_id"])
            )
            if dataset.content_sha256 != source.input.dataset.content_sha256:
                raise DomainError(
                    "factor_origin_conflict", "Dataset binding differs", 409
                )
            verify_dataset_integrity(dataset, settings, admission)
        else:
            dataset = _dataset(
                session, claim.owner_id, source, package, settings, admission, claim.id
            )
        if not package.manifest["include_values"] or dataset is None:
            bindings["snapshots"].append(
                {
                    "source_snapshot_id": str(source.snapshot_id),
                    "status": "unresolved",
                    "reason": "source_dataset_or_values_unresolved",
                }
            )
            continue
        dataset_mapping = {
            "local_kind": "dataset_version",
            "local_id": str(dataset.id),
            "local_sha256": dataset.content_sha256,
            "source_id": str(source.input.dataset.dataset_version_id),
            "source_sha256": source.input.dataset.content_sha256,
        }
        if dataset_mapping not in bindings["origin_map"]:
            bindings["origin_map"].append(dataset_mapping)
        old_snapshot = None
        for prior in previous:
            check_deadline(admission)
            for item in prior.local_bindings.get("snapshots", []):
                if item.get("source_snapshot_id") == str(source.snapshot_id):
                    if (
                        item.get("source_manifest_sha256", source.sha256)
                        != source.sha256
                    ):
                        raise DomainError(
                            "factor_origin_conflict",
                            "Source snapshot identity has conflicting content",
                            409,
                        )
                    if item.get("status") == "derived_validated":
                        old_snapshot = item
        if old_snapshot is not None:
            from backend.app.factor_services import verified_snapshot

            old, _old_manifest, _ = verified_snapshot(
                session, claim.owner_id, UUID(old_snapshot["snapshot_id"]), settings
            )
            if (
                old.factor_version_id != version_id
                or old.dataset_content_sha256 != source.input.dataset.content_sha256
            ):
                raise DomainError(
                    "factor_origin_conflict", "Stored snapshot binding differs", 409
                )
            bindings["snapshots"].append(dict(old_snapshot))
            bindings["snapshot_ids"].append(str(old.id))
            bindings["origin_map"].append(
                {
                    "local_kind": "feature_snapshot",
                    "local_id": str(old.id),
                    "local_sha256": old.manifest_sha256,
                    "source_id": str(source.snapshot_id),
                    "source_sha256": source.sha256,
                }
            )
            continue
        expected = source.input.model_dump(mode="json")
        expected["version"] = {
            "factor_version_id": str(version_id),
            "definition_sha256": definition.sha256,
            "code_sha256": definition.code_sha256,
        }
        expected["dataset"] = {
            **expected["dataset"],
            "dataset_version_id": str(dataset.id),
            "source_quality": "unknown",
        }
        expected["calculation_universe"]["membership_history"] = "unknown"
        expected["exposure_history"] = history.model_dump(mode="json")
        expected["materialization_profile"] = "isolated-derived-import.v1"
        expected["environment_sha256"] = digest(
            {"parser_image_id": claim.image_id, "profile": "isolated-derived-import.v1"}
        )
        expected["adapter_version"] = "mineagent-factor-import.v1"
        local_input = SnapshotInput.model_validate(expected)
        operation = operation_path(settings, claim, index)
        partition = next(
            obj for obj in source.objects if obj.role == "values_partition"
        )
        prepare_operation(
            settings.storage_root,
            operation,
            image_id=claim.image_id,
            input_sha256=partition.sha256,
            fence_fd=admission.factor_fd,
        )
        for name in ("input", "output", "control"):
            fd = _prepare_directory(operation / name, Path(settings.storage_root))
            os.close(fd)
        mapping = {
            "schema_version": "mineagent.factor-identity-map.v1",
            "source_factor_version_id": package.manifest["version_id"],
            "local_factor_version_id": str(version_id),
        }
        for name, content in (
            ("input.parquet", package.objects[f"objects/{partition.sha256}.parquet"]),
            ("identity-map.json", canonical_bytes(mapping)),
            ("snapshot-input.json", canonical_bytes(local_input)),
        ):
            publish_bytes(
                operation / "input" / name,
                content,
                sha256=hashlib.sha256(content).hexdigest(),
                durable_root=Path(settings.storage_root),
            )
        if parser is None:
            parser = parser_factory(
                image_id=claim.image_id, output_bytes=16 * 1024 * 1024
            )
        report = parser.run(
            settings.storage_root,
            operation,
            input_sha256=partition.sha256,
            deadline=float(admission),
            fence_fd=admission.factor_fd,
        )
        if (
            report["row_count"] != source.row_count
            or report["valid_count"] != source.valid_count
            or report["missing_count"] != source.missing_count
            or report["source_rows_sha256"] != source.output_digest
            or report["row_count"] > MAX_IMPORT_ROWS
            or report["output_size_bytes"] > 16 * 1024 * 1024
            or report["factor_version_ids"]
            != ([str(version_id)] if source.row_count else [])
        ):
            raise DomainError(
                "factor_package_integrity",
                "Parsed source content differs from declared snapshot",
                422,
            )
        normalized = read_verified(
            operation,
            "output/values.parquet",
            report["output_sha256"],
            128 * 1024 * 1024,
        )
        values_obj = publish_object(
            session, claim.owner_id, "values_partition", normalized, settings, admission
        )
        evidence_obj = publish_object(
            session,
            claim.owner_id,
            "native_evidence",
            canonical_bytes(report),
            settings,
            admission,
        )
        objects = [code_obj, values_obj, evidence_obj]
        refs = tuple(
            ObjectRef(
                role=obj.object_kind,
                ordinal=0,
                sha256=obj.sha256,
                size_bytes=obj.size_bytes,
                name=f"{obj.sha256}.{OBJECT_TYPES[obj.object_kind][0]}",
                rows=report["row_count"]
                if obj.object_kind == "values_partition"
                else 0,
            )
            for obj in objects
        )
        snapshot_id = uuid5(claim.id, f"snapshot:{source.snapshot_id}")
        manifest = FeatureSnapshotManifest(
            snapshot_id=snapshot_id,
            input=local_input,
            input_digest=local_input.sha256,
            output_digest=report["rows_sha256"],
            row_count=report["row_count"],
            valid_count=report["valid_count"],
            missing_count=report["missing_count"],
            objects=refs,
        )
        manifest_obj = publish_object(
            session,
            claim.owner_id,
            "snapshot_manifest",
            canonical_bytes(manifest),
            settings,
            admission,
        )
        snapshot = FeatureSnapshot(
            id=snapshot_id,
            owner_id=claim.owner_id,
            factor_version_id=version_id,
            dataset_version_id=dataset.id,
            dataset_content_sha256=dataset.content_sha256,
            definition_sha256=definition.sha256,
            input_digest=manifest.input_digest,
            output_digest=manifest.output_digest,
            manifest_object_id=manifest_obj.id,
            manifest_sha256=manifest_obj.sha256,
            manifest=manifest.model_dump(mode="json"),
            environment_digest=local_input.environment_sha256,
            row_count=manifest.row_count,
            valid_count=manifest.valid_count,
            missing_count=manifest.missing_count,
            identity_mode="local",
            local_verification=imported_verification(
                evidence_sha256=digest(report)
            ).model_dump(mode="json"),
            source_claims={
                "import_envelope_id": str(uuid5(claim.id, "envelope")),
                "parser_report": report,
                "origin_snapshot_id": str(source.snapshot_id),
                "derived_identity": True,
                "source_archive_sha256": claim.archive_sha256,
                "source_snapshot_id": str(source.snapshot_id),
                "source_manifest_sha256": source.sha256,
                "identity_mapping_sha256": report["identity_mapping_sha256"],
                "derived_output": True,
                "source_claims": source.input.model_dump(mode="json"),
            },
        )
        session.add(snapshot)
        session.flush()
        for obj in objects:
            session.add(
                FeatureSnapshotObject(
                    owner_id=claim.owner_id,
                    snapshot_id=snapshot_id,
                    object_id=obj.id,
                    role=obj.object_kind,
                    ordinal=0,
                )
            )
        protocol = {
            "schema_version": "mineagent.factor-materialization.v1",
            "profile": "isolated-derived-import.v1",
            "input_digest": local_input.sha256,
        }
        session.add(
            FactorEvaluation(
                id=uuid5(claim.id, f"evaluation:{source.snapshot_id}"),
                owner_id=claim.owner_id,
                factor_version_id=version_id,
                feature_snapshot_id=snapshot_id,
                kind="materialization",
                protocol=protocol,
                protocol_sha256=digest(protocol),
                status="succeeded",
                result_payload=report,
                result_sha256=digest(report),
                environment_digest=local_input.environment_sha256,
            )
        )
        bindings["snapshot_ids"].append(str(snapshot_id))
        bindings["origin_map"].append(
            {
                "local_kind": "feature_snapshot",
                "local_id": str(snapshot_id),
                "local_sha256": manifest.sha256,
                "source_id": str(source.snapshot_id),
                "source_sha256": source.sha256,
            }
        )
        bindings["snapshots"].append(
            {
                "source_snapshot_id": str(source.snapshot_id),
                "snapshot_id": str(snapshot_id),
                "source_manifest_sha256": source.sha256,
                "manifest_sha256": manifest.sha256,
                "source_dataset_version_id": str(
                    source.input.dataset.dataset_version_id
                ),
                "dataset_version_id": str(dataset.id),
                "input_digest": manifest.input_digest,
                "output_digest": manifest.output_digest,
                "identity_mapping_sha256": report["identity_mapping_sha256"],
                "status": "derived_validated",
            }
        )
    if bindings["snapshots"] and all(
        s["status"] == "derived_validated" for s in bindings["snapshots"]
    ):
        bindings["execution_binding_status"] = "resolved"
    from backend.app.factor_import_services import admit_import_exposures

    bindings["exposure_admissions"] = admit_import_exposures(
        session,
        claim.owner_id,
        package.manifest,
        bindings,
        claim.archive_sha256,
        settings,
        deadline=admission,
    )
    envelope = FactorImportEnvelope(
        id=uuid5(claim.id, "envelope"),
        owner_id=claim.owner_id,
        archive_object_id=claim.archive_object_id,
        archive_sha256=claim.archive_sha256,
        origin_namespace=package.manifest["origin_namespace"],
        origin_payload=package.manifest,
        origin_payload_sha256=digest(package.manifest),
        local_bindings=bindings,
        local_bindings_sha256=digest(bindings),
        exposure_history=history.model_dump(mode="json"),
    )
    session.add(envelope)
    session.flush()
    row = fenced_admission(session, claim)
    row.status = "succeeded"
    row.result_envelope_id = envelope.id
    row.lease_token = None
    row.lease_expires_at = None
    row.updated_at = utcnow()
    row.error_code = None
    session.flush()
    return envelope.id


def _outcome(factory, claim, error_code, settings, *, retry=False):
    """New connection reconciles uncertain commit before any retry/status change."""
    with factory() as session:
        row = session.scalar(
            select(FactorImportAdmission)
            .where(FactorImportAdmission.id == claim.id)
            .with_for_update()
        )
        envelope = session.scalar(
            select(FactorImportEnvelope).where(
                FactorImportEnvelope.owner_id == claim.owner_id,
                FactorImportEnvelope.archive_sha256 == claim.archive_sha256,
            )
        )
        if envelope is not None:
            if row.status != "succeeded":
                row.status = "succeeded"
                row.result_envelope_id = envelope.id
                row.lease_token = None
                row.lease_expires_at = None
                row.updated_at = utcnow()
                row.error_code = None
            session.commit()
            return "succeeded"
        if row.generation != claim.generation or row.lease_token != claim.token:
            return "superseded"
        try:
            with factor_fence(settings.storage_root):
                assert_quiescent(settings.storage_root)
        except (ParserIsolationError, OSError):
            status = "blocked"
        else:
            status = "queued" if retry and claim.generation < 3 else "failed"
        row.status = status
        row.error_code = error_code[:80]
        row.lease_token = None
        row.lease_expires_at = None
        row.updated_at = utcnow()
        session.commit()
        return status


def process_one(
    settings, admission_id=None, *, factory=None, parser_factory=DockerParser
):
    engine = None
    if factory is None:
        engine = create_db_engine(settings)
        factory = session_factory(engine)
    claim = None
    publication_entered = False
    try:
        with factory() as session:
            claim = claim_one(session, admission_id)
        if claim is None:
            return False
        with factory() as session:  # noqa: SIM117 -- keep publication scope explicit
            # Peak includes original staging, two parser outputs + atomic publication
            # copies, all source bytes and control overhead. Existing bytes remain charged.
            with publication(
                session,
                claim.owner_id,
                settings,
                peak_bytes=256 * 1024 * 1024,
                peak_files=100,
            ) as admission:
                publication_entered = True
                row = fenced_admission(session, claim)
                archive = owned(
                    session, FactorObject, claim.owner_id, row.archive_object_id
                )
                raw = read_verified(
                    settings.storage_root,
                    archive.storage_key,
                    claim.archive_sha256,
                    10_000_000,
                )
                package = decode_package(raw)
                _finish(session, claim, package, settings, admission, parser_factory)
        return True
    except Exception as exc:
        if claim is None:
            raise
        code = getattr(exc, "code", "factor_import_failed")
        # Only a known-unexecuted publication admission collision is eligible.
        # A busy error from the body or context exit may follow parser/publication
        # work and must never authorize replay. _outcome independently reconciles
        # committed envelopes, fences generation, proves quiescence and caps retry.
        retry = (
            not publication_entered
            and isinstance(exc, DomainError)
            and exc.code == "factor_busy"
        )
        _outcome(factory, claim, code, settings, retry=retry)
        return True
    finally:
        if engine is not None:
            engine.dispose()


def reconcile_imports(settings, *, factory=None, parser_factory=DockerParser):
    engine = None
    if factory is None:
        engine = create_db_engine(settings)
        factory = session_factory(engine)
    recovered = 0
    try:
        with factory() as session:
            rows = session.scalars(
                select(FactorImportAdmission)
                .where(
                    (FactorImportAdmission.status == "blocked")
                    | (
                        (FactorImportAdmission.status == "processing")
                        & (FactorImportAdmission.lease_expires_at < utcnow())
                    )
                )
                .limit(100)
            ).all()
            claims = [
                Claim(
                    r.id,
                    r.owner_id,
                    r.generation,
                    r.lease_token,
                    r.archive_object_id,
                    r.archive_sha256,
                    r.parser_image_id,
                )
                for r in rows
            ]
        for claim in claims:
            parser = None
            safe = True
            for index in range(MAX_SNAPSHOTS):
                operation = operation_path(settings, claim, index)
                if (
                    operation.exists()
                    or (operation.parent / f"prepared-{operation.name}.json").exists()
                ):
                    try:
                        if parser is None:
                            parser = parser_factory(image_id=claim.image_id)
                        safe = (
                            parser.reconcile_stop(settings.storage_root, operation)
                            and safe
                        )
                    except (ParserIsolationError, OSError):
                        safe = False
            if safe:
                _outcome(
                    factory, claim, "factor_import_interrupted", settings, retry=True
                )
                recovered += 1
        return recovered
    finally:
        if engine is not None:
            engine.dispose()

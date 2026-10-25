"""Explicit factor restore closure, independent of legacy run/artifact registrations."""

from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import text

SCHEMA = "mineagent.factor-restore-closure.v1"
IMPORT_SCHEMA = "mineagent.factor-restore-closure.v2"
IMPORT_TABLE_NAMES = ("factor_import_envelopes", "factor_import_admissions")
TABLE_NAMES = (
    "factors",
    "factor_objects",
    "factor_versions",
    "feature_snapshots",
    "feature_snapshot_objects",
    "factor_evaluations",
    "factor_bundles",
    "factor_bundle_members",
    "strategy_factor_dependencies",
    "factor_submissions",
    "factor_comparisons",
    "factor_packages",
)


def canonical(value):
    from packages.factors.contracts import canonical_bytes

    return canonical_bytes(value)


def read_closure(
    connection, maximum_rows, *, revision="012_factor_library", maximum_bytes=32_000_000
):
    """Fixed allowlisted SQL; retain every generation, failed result and physical object."""
    tables = {}
    count = size = 0

    def bounded_rows(query):
        nonlocal count, size
        result = connection.execute(
            text(query).execution_options(stream_results=True, yield_per=1),
            {"limit": maximum_rows + 1},
        ).scalars()
        rows = []
        try:
            for row in result:
                count += 1
                size += len(canonical(row))
                if count > maximum_rows or size > maximum_bytes:
                    raise ValueError("Factor closure exceeds inventory budget")
                rows.append(row)
        finally:
            close = getattr(result, "close", None)
            if close is not None:
                close()
        return rows

    for name in TABLE_NAMES + (
        IMPORT_TABLE_NAMES if revision == "013_factor_import" else ()
    ):
        rows = bounded_rows(
            f"SELECT to_jsonb(t) AS record FROM {name} AS t ORDER BY id LIMIT :limit"
        )
        tables[name] = rows
    external = {}
    for name, query in (
        (
            "datasets",
            "SELECT d.id,d.owner_id,d.content_sha256 FROM dataset_versions d WHERE EXISTS (SELECT 1 FROM feature_snapshots f WHERE f.dataset_version_id=d.id) OR EXISTS (SELECT 1 FROM data_usage_exposures e WHERE e.dataset_version_id=d.id AND e.source_key LIKE 'factor:%')",
        ),
        (
            "strategies",
            "SELECT DISTINCT s.id,s.owner_id FROM strategy_versions s JOIN strategy_factor_dependencies f ON f.strategy_version_id=s.id",
        ),
        (
            "exposures",
            "SELECT e.* FROM data_usage_exposures e WHERE e.source_key LIKE 'factor:%'",
        ),
    ):
        rows = bounded_rows(
            f"SELECT to_jsonb(t) FROM ({query}) t ORDER BY id LIMIT :limit"
        )
        external[name] = rows
    return {
        "schema_version": IMPORT_SCHEMA if revision == "013_factor_import" else SCHEMA,
        "tables": tables,
        "external_bindings": external,
    }


def validate_closure(
    closure, check_object, read_json, maximum_rows, *, read_archive=None
):
    """Validate relations and immutable byte identities again after SQL restore.

    check_object verifies an independently inventoried physical file. read_json
    opens only a registered, already hash-checked manifest with bounded no-follow IO.
    """
    if (
        not isinstance(closure, dict)
        or set(closure) != {"schema_version", "tables", "external_bindings"}
        or closure["schema_version"] not in {SCHEMA, IMPORT_SCHEMA}
    ):
        raise ValueError("Unsupported factor closure")
    expected_tables = set(TABLE_NAMES) | (
        set(IMPORT_TABLE_NAMES) if closure["schema_version"] == IMPORT_SCHEMA else set()
    )
    tables = closure["tables"]
    external = closure["external_bindings"]
    if (
        not isinstance(tables, dict)
        or set(tables) != expected_tables
        or not isinstance(external, dict)
        or set(external) != {"datasets", "strategies", "exposures"}
    ):
        raise ValueError("Incomplete factor closure")
    indexes = {}
    count = 0
    for name, rows in {**tables, **external}.items():
        if not isinstance(rows, list):
            raise TypeError("Invalid factor inventory")
        count += len(rows)
        if count > maximum_rows:
            raise ValueError("Factor closure exceeds inventory budget")
        indexed = {}
        for row in rows:
            if not isinstance(row, dict) or "id" not in row or "owner_id" not in row:
                raise ValueError("Invalid factor identity")
            for field in ("id", "owner_id"):
                if (
                    not isinstance(row[field], str)
                    or str(UUID(row[field])) != row[field]
                ):
                    raise ValueError("Invalid factor identity")
            if row["id"] in indexed:
                raise ValueError("Duplicate factor identity")
            indexed[row["id"]] = row
        indexes[name] = indexed

    def related(row, field, table, optional=False):
        key = row.get(field)
        if optional and key is None:
            return None
        other = indexes[table].get(key)
        if other is None or other["owner_id"] != row["owner_id"]:
            raise ValueError("Missing or cross-owner factor relationship")
        return other

    def hashed(row, payload, digest):
        if hashlib.sha256(canonical(row[payload])).hexdigest() != row[digest]:
            raise ValueError("Factor metadata hash mismatch")

    for row in external["exposures"]:
        if not isinstance(row.get("source_key"), str) or not row[
            "source_key"
        ].startswith("factor:"):
            raise ValueError("Invalid factor exposure source")
        if row.get("dataset_version_id") is not None:
            related(row, "dataset_version_id", "datasets")
    for row in tables["factor_objects"]:
        if not row["storage_key"].startswith(f"factors/{row['owner_id']}/"):
            raise ValueError("Factor object ownership path mismatch")
        check_object(row["storage_key"], row["sha256"], row["size_bytes"])
    ordinals = {}
    for row in tables["factor_versions"]:
        related(row, "factor_id", "factors")
        parent = related(row, "parent_version_id", "factor_versions", True)
        if parent is not None and (
            parent["factor_id"] != row["factor_id"]
            or parent["ordinal"] >= row["ordinal"]
        ):
            raise ValueError("Factor parent mismatch")
        ordinals.setdefault(row["factor_id"], []).append(row["ordinal"])
        code = related(row, "code_object_id", "factor_objects")
        if (
            code["object_kind"] != "code"
            or code["sha256"] != row["code_sha256"]
            or hashlib.sha256(row["code"].encode()).hexdigest() != row["code_sha256"]
        ):
            raise ValueError("Factor source byte identity mismatch")
        hashed(row, "definition", "definition_sha256")
    if any(
        sorted(values) != list(range(1, len(values) + 1))
        for values in ordinals.values()
    ):
        raise ValueError("Factor lineage ordinals incomplete")
    for row in tables["feature_snapshots"]:
        version = related(row, "factor_version_id", "factor_versions")
        dataset = related(row, "dataset_version_id", "datasets")
        obj = related(row, "manifest_object_id", "factor_objects")
        if (
            version["definition_sha256"] != row["definition_sha256"]
            or dataset["content_sha256"] != row["dataset_content_sha256"]
            or obj["sha256"] != row["manifest_sha256"]
            or obj["object_kind"] != "snapshot_manifest"
        ):
            raise ValueError("Factor snapshot binding mismatch")
        if read_json(obj["storage_key"]) != row["manifest"]:
            raise ValueError("Factor manifest differs from preserved bytes")
        from packages.factors.contracts import FeatureSnapshotManifest

        manifest = FeatureSnapshotManifest.model_validate(row["manifest"])
        if (
            str(manifest.snapshot_id) != row["id"]
            or str(manifest.input.version.factor_version_id) != row["factor_version_id"]
            or manifest.input.definition.sha256 != row["definition_sha256"]
            or manifest.input_digest != row["input_digest"]
            or manifest.output_digest != row["output_digest"]
            or manifest.sha256 != row["manifest_sha256"]
            or manifest.input.environment_sha256 != row["environment_digest"]
            or str(manifest.input.dataset.dataset_version_id)
            != row["dataset_version_id"]
            or manifest.input.dataset.content_sha256 != row["dataset_content_sha256"]
            or (manifest.row_count, manifest.valid_count, manifest.missing_count)
            != (row["row_count"], row["valid_count"], row["missing_count"])
        ):
            raise ValueError("Factor snapshot manifest identity mismatch")
        actual = []
        for link in tables["feature_snapshot_objects"]:
            if link["snapshot_id"] == row["id"]:
                child = related(link, "object_id", "factor_objects")
                actual.append(
                    (
                        link["role"],
                        link["ordinal"],
                        child["sha256"],
                        child["size_bytes"],
                    )
                )
        expected = [
            (o.role, o.ordinal, o.sha256, o.size_bytes) for o in manifest.objects
        ]
        if sorted(actual) != sorted(expected):
            raise ValueError("Factor snapshot physical inventory incomplete")
    for row in tables["feature_snapshot_objects"]:
        related(row, "snapshot_id", "feature_snapshots")
        obj = related(row, "object_id", "factor_objects")
        if obj["object_kind"] != row["role"]:
            raise ValueError("Factor inventory role mismatch")
    for row in tables["factor_evaluations"]:
        related(row, "factor_version_id", "factor_versions")
        snapshot = related(row, "feature_snapshot_id", "feature_snapshots", True)
        if (
            snapshot is not None
            and snapshot["factor_version_id"] != row["factor_version_id"]
        ):
            raise ValueError("Factor evaluation tuple mismatch")
        if row["status"] == "succeeded" and snapshot is None:
            raise ValueError("Successful factor evidence missing")
        hashed(row, "protocol", "protocol_sha256")
        hashed(row, "result_payload", "result_sha256")
    for row in tables["factor_bundles"]:
        hashed(row, "definition", "content_sha256")
        expected_members = row["definition"].get("members")
        if not isinstance(expected_members, list) or not expected_members:
            raise ValueError("Factor bundle members missing")
        expected = [
            (i, item["factor_version_id"], item["definition_sha256"])
            for i, item in enumerate(expected_members)
        ]
        actual = [
            (
                member["ordinal"],
                member["factor_version_id"],
                member["definition_sha256"],
            )
            for member in tables["factor_bundle_members"]
            if member["bundle_id"] == row["id"]
        ]
        if sorted(actual) != sorted(expected):
            raise ValueError("Factor bundle member closure incomplete")
    for row in tables["factor_bundle_members"]:
        related(row, "bundle_id", "factor_bundles")
        version = related(row, "factor_version_id", "factor_versions")
        if row["definition_sha256"] != version["definition_sha256"]:
            raise ValueError("Factor bundle definition mismatch")
    for row in tables["strategy_factor_dependencies"]:
        related(row, "strategy_version_id", "strategies")
        related(row, "factor_bundle_id", "factor_bundles")
        hashed(row, "definition", "dependency_sha256")
    for row in tables["factor_comparisons"]:
        related(row, "left_version_id", "factor_versions")
        related(row, "right_version_id", "factor_versions")
        hashed(row, "definition", "content_sha256")
    for row in tables["factor_packages"]:
        related(row, "factor_version_id", "factor_versions")
        obj = related(row, "archive_object_id", "factor_objects")
        if obj["sha256"] != row["archive_sha256"] or obj["object_kind"] != "package":
            raise ValueError("Factor package identity mismatch")

    for row in tables.get("factor_import_envelopes", []):
        obj = related(row, "archive_object_id", "factor_objects")
        if obj["sha256"] != row["archive_sha256"] or obj["object_kind"] != "package":
            raise ValueError("Factor import source archive mismatch")
        if read_archive is None:
            raise ValueError("Factor import archive reader is required")
        from packages.factors.package import decode_package

        package = decode_package(read_archive(obj["storage_key"]))
        if (
            package.archive_sha256 != row["archive_sha256"]
            or package.manifest != row["origin_payload"]
        ):
            raise ValueError("Factor origin payload differs from preserved archive")
        if package.manifest.get("origin_namespace") != row["origin_namespace"]:
            raise ValueError("Factor origin namespace mismatch")
        hashed(row, "origin_payload", "origin_payload_sha256")
        hashed(row, "local_bindings", "local_bindings_sha256")
        bindings = row["local_bindings"]
        if not isinstance(bindings, dict) or set(bindings) != {
            "factor_id",
            "version_id",
            "source_version_id",
            "objects",
            "snapshots",
            "snapshot_ids",
            "origin_map",
            "execution_binding_status",
            "exposure_admissions",
        }:
            raise ValueError("Unsupported local import binding schema")
        local = {"owner_id": row["owner_id"], **bindings}
        factor = related(local, "factor_id", "factors")
        version = related(local, "version_id", "factor_versions")
        if version["factor_id"] != factor["id"]:
            raise ValueError("Imported factor/version binding mismatch")
        if not isinstance(bindings["snapshot_ids"], list) or len(
            set(bindings["snapshot_ids"])
        ) != len(bindings["snapshot_ids"]):
            raise ValueError("Invalid imported snapshot inventory")
        for snapshot_id in bindings["snapshot_ids"]:
            snapshot = related(
                {"owner_id": row["owner_id"], "snapshot_id": snapshot_id},
                "snapshot_id",
                "feature_snapshots",
            )
            if snapshot["factor_version_id"] != version["id"]:
                raise ValueError("Imported snapshot/version mismatch")
        if bindings["source_version_id"] != package.manifest["version_id"]:
            raise ValueError("Imported source version mismatch")
        if not isinstance(bindings["objects"], list):
            raise TypeError("Imported original objects missing")
        original_paths = set()
        for original in bindings["objects"]:
            if set(original) != {"source_path", "object_id", "sha256"}:
                raise ValueError("Invalid original object mapping")
            source_path = original["source_path"]
            child = related(
                {"owner_id": row["owner_id"], "object_id": original["object_id"]},
                "object_id",
                "factor_objects",
            )
            if (
                source_path not in package.objects
                or source_path in original_paths
                or child["sha256"] != original["sha256"]
                or hashlib.sha256(package.objects[source_path]).hexdigest()
                != original["sha256"]
            ):
                raise ValueError("Original import object mapping differs")
            original_paths.add(source_path)
        if original_paths != set(package.objects):
            raise ValueError("Original import object closure incomplete")
        mapping_types = {
            "factor_version": ("factor_versions", "definition_sha256"),
            "feature_snapshot": ("feature_snapshots", "manifest_sha256"),
            "dataset_version": ("datasets", "content_sha256"),
        }
        source_hashes = {
            ("factor_version", package.manifest["version_id"]): package.manifest[
                "definition_sha256"
            ]
        }
        source_hashes.update(
            {
                ("feature_snapshot", item["snapshot_id"]): hashlib.sha256(
                    canonical(item)
                ).hexdigest()
                for item in package.manifest.get("snapshots", [])
            }
        )
        source_hashes.update(
            {
                ("dataset_version", item["dataset_version_id"]): item["content_sha256"]
                for item in package.manifest.get("source_datasets", [])
            }
        )
        source_hashes.update(
            {
                (
                    "dataset_version",
                    item["input"]["dataset"]["dataset_version_id"],
                ): item["input"]["dataset"]["content_sha256"]
                for item in package.manifest.get("snapshots", [])
            }
        )
        mapped = set()
        for mapping in bindings["origin_map"]:
            if (
                set(mapping)
                != {
                    "local_kind",
                    "local_id",
                    "local_sha256",
                    "source_id",
                    "source_sha256",
                }
                or mapping["local_kind"] not in mapping_types
            ):
                raise ValueError("Invalid origin/local mapping schema")
            table, hash_field = mapping_types[mapping["local_kind"]]
            child = related(
                {"owner_id": row["owner_id"], "local_id": mapping["local_id"]},
                "local_id",
                table,
            )
            identity = (mapping["local_kind"], mapping["local_id"])
            if (
                identity in mapped
                or child[hash_field] != mapping["local_sha256"]
                or source_hashes.get((mapping["local_kind"], mapping["source_id"]))
                != mapping["source_sha256"]
            ):
                raise ValueError("Origin/local mapping identity mismatch")
            mapped.add(identity)
        required_mappings = {("factor_version", bindings["version_id"])} | {
            ("feature_snapshot", key) for key in bindings["snapshot_ids"]
        }
        if not required_mappings <= mapped:
            raise ValueError("Origin/local mapping closure incomplete")
        exposure_map = bindings["exposure_admissions"]
        if (
            not isinstance(exposure_map, dict)
            or set(exposure_map) != {"admitted", "unresolved", "history_completeness"}
            or exposure_map["history_completeness"] != "unknown"
        ):
            raise ValueError("Imported exposure uncertainty was erased")
        claim_hashes = {
            hashlib.sha256(canonical(item)).hexdigest()
            for item in package.manifest.get("exposure_claims", [])
        }
        recorded_claims = set()
        for admission in exposure_map["admitted"]:
            if set(admission) != {
                "claim_sha256",
                "local_exposure_id",
                "dataset_version_id",
                "dataset_content_sha256",
                "mapping_sha256",
            }:
                raise ValueError("Invalid imported exposure admission")
            exposure = related(
                {
                    "owner_id": row["owner_id"],
                    "exposure_id": admission["local_exposure_id"],
                },
                "exposure_id",
                "exposures",
            )
            dataset = related(
                {
                    "owner_id": row["owner_id"],
                    "dataset_id": admission["dataset_version_id"],
                },
                "dataset_id",
                "datasets",
            )
            provenance = exposure["provenance"]
            if (
                exposure["dataset_version_id"] != dataset["id"]
                or dataset["content_sha256"] != admission["dataset_content_sha256"]
                or provenance.get("source_archive_sha256") != row["archive_sha256"]
                or provenance.get("claim_sha256") != admission["claim_sha256"]
                or provenance.get("mapping_sha256") != admission["mapping_sha256"]
            ):
                raise ValueError("Imported exposure physical identity mismatch")
            recorded_claims.add(admission["claim_sha256"])
        for unresolved in exposure_map["unresolved"]:
            if set(unresolved) != {"claim_sha256", "reason"} or not isinstance(
                unresolved["reason"], str
            ):
                raise ValueError("Invalid unresolved exposure evidence")
            if unresolved["claim_sha256"] is not None:
                recorded_claims.add(unresolved["claim_sha256"])
        if recorded_claims != claim_hashes or (
            not claim_hashes and not exposure_map["unresolved"]
        ):
            raise ValueError("Imported exposure claim closure incomplete")
        if not isinstance(row["exposure_history"], dict):
            raise TypeError("Missing factor import exposure history")
    for row in tables.get("factor_import_admissions", []):
        obj = related(row, "archive_object_id", "factor_objects")
        if obj["sha256"] != row["archive_sha256"]:
            raise ValueError("Factor import admission archive mismatch")
        if (
            row["status"] not in {"succeeded", "failed"}
            or row["lease_token"] is not None
            or row["lease_expires_at"] is not None
        ):
            raise ValueError("Factor import remains unresolved")
        result = related(row, "result_envelope_id", "factor_import_envelopes", True)
        if (row["status"] == "succeeded") != (result is not None):
            raise ValueError("Factor import terminal result missing")
        if result is not None and result["archive_sha256"] != row["archive_sha256"]:
            raise ValueError("Factor import result archive mismatch")

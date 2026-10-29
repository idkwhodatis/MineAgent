"""Exact030 dataset lineage, including baskets not yet used by a research run.

Retained originals are re-read and the pure assembler recomputes the basket.
This verifies provenance integrity, not the truth of user declarations or PIT.
"""

import hashlib
import re
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import text

from packages.factors.contracts import canonical_bytes, digest
from packages.strategy_sdk import JsonLimits, parse_json

SCHEMA = "mineagent.declared-upload-restore-closure.v1"
PROFILE = "mineagent.declared-upload-basket.v1"
FIELDS = {
    "owners": "id",
    "declared_upload_baskets": "id owner_id idempotency_key request_sha256 dataset_id manifest_sha256 profile created_at",
    "declared_upload_basket_members": "basket_id owner_id ordinal source_dataset_id source_manifest_sha256 created_at",
    "datasets": "id owner_id market start_date end_date source is_synthetic content_sha256 manifest storage_key",
}
LIMITS = JsonLimits(max_bytes=2 * 1024**2, max_nodes=100000, max_items=100000)


def _require(value):
    if not value:
        raise ValueError("Declared upload retained lineage differs")


def _uuid(value):
    _require(type(value) is str and str(UUID(value)) == value)
    return value


def _sha(value):
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value))
    return value


def read_closure(connection, maximum_rows, *, maximum_bytes=32_000_000):
    _require(type(maximum_rows) is int and maximum_rows > 0)
    tables, total, size = {}, 0, 0
    for name, fields in FIELDS.items():
        table, where = name, ""
        order = "id"
        if name == "owners":
            table = "users"
            where = " WHERE id IN (SELECT owner_id FROM declared_upload_baskets)"
        elif name == "datasets":
            table = "dataset_versions"
            where = " WHERE id IN (SELECT dataset_id FROM declared_upload_baskets UNION SELECT source_dataset_id FROM declared_upload_basket_members)"
        elif name == "declared_upload_basket_members":
            order = "basket_id,ordinal"
        result = connection.execute(
            text(
                f"SELECT to_jsonb(t) FROM (SELECT {','.join(fields.split())} FROM {table}{where} ORDER BY {order} LIMIT :limit) t"
            ).execution_options(stream_results=True, yield_per=1),
            {"limit": maximum_rows + 1},
        )
        rows = []
        try:
            for row in result.scalars():
                total += 1
                size += len(canonical_bytes(row)) + 1
                _require(total <= maximum_rows and size <= maximum_bytes)
                rows.append(row)
        finally:
            result.close()
        tables[name] = rows
    value = {"schema_version": SCHEMA, "tables": tables}
    _require(len(canonical_bytes(value)) <= maximum_bytes)
    return value


def closure_for(snapshot, capabilities):
    schema = getattr(capabilities, "declared_upload_schema", None)
    _require(schema in (None, SCHEMA))
    _require((schema is not None) == ("declared_upload_closure" in snapshot))
    if schema is None:
        return None
    _require(
        (capabilities.research_closure_schema, capabilities.rd_source_closure_schema)
        in {
            (
                "mineagent.research-restore-closure.v15",
                "mineagent.rd-source-restore-closure.v10",
            ),
            (
                "mineagent.research-restore-closure.v16",
                "mineagent.rd-source-restore-closure.v11",
            ),
        }
    )
    value = snapshot["declared_upload_closure"]
    _require(
        type(value) is dict
        and set(value) == {"schema_version", "tables"}
        and value["schema_version"] == SCHEMA
    )
    return value


def read_snapshot(connection, capabilities, limits):
    if getattr(capabilities, "declared_upload_schema", None) is None:
        return {}
    value = {
        "declared_upload_closure": read_closure(
            connection, limits.max_files, maximum_bytes=limits.max_manifest_bytes
        )
    }
    closure_for(value, capabilities)
    return value


def retained_artifacts(manifest, key, read_bytes, *, declared_upload=False):
    """Only the explicit new gate admits the bounded assembler's inventory."""
    if manifest.get("schema_version") != 3:
        return {
            name: read_bytes(key + "/" + name, 32 * 1024**2)
            for name in ("preview.json", "sessions.json")
        }
    _require(declared_upload is True and manifest.get("profile") == PROFILE)
    from packages.rdagent_adapter.declared_upload import (
        MAX_BASKET_FILES,
        MAX_BYTES,
        _name,
    )

    refs = manifest.get("artifact_files")
    _require(type(refs) is dict and 0 < len(refs) <= MAX_BASKET_FILES)
    total, result = 0, {}
    for name, sha in refs.items():
        _require(_name(name) and name != "manifest.json")
        _sha(sha)
        raw = read_bytes(key + "/" + name, min(32 * 1024**2, MAX_BYTES - total))
        total += len(raw)
        _require(total <= MAX_BYTES and hashlib.sha256(raw).hexdigest() == sha)
        result[name] = raw
    return result


def validate_snapshot(snapshot, capabilities, read_bytes, maximum_rows):
    closure = closure_for(snapshot, capabilities)
    # A new-profile manifest cannot hide in an old database capability.
    if closure is None:
        _require(
            not any(
                (
                    row["manifest"].get("profile") == PROFILE
                    or row["manifest"].get("schema_version") == 3
                )
                for row in snapshot["datasets"]
            )
        )
        return
    _require(type(maximum_rows) is int and maximum_rows > 0)
    tables = closure["tables"]
    _require(type(tables) is dict and set(tables) == set(FIELDS))
    _require(len(canonical_bytes(closure)) <= 32_000_000)
    total = 0
    for name, fields in FIELDS.items():
        rows = tables[name]
        _require(type(rows) is list)
        total += len(rows)
        _require(total <= maximum_rows)
        for row in rows:
            _require(type(row) is dict and set(row) == set(fields.split()))
    owners = {_uuid(row["id"]) for row in tables["owners"]}
    _require(len(owners) == len(tables["owners"]))
    _require(owners == {row["owner_id"] for row in tables["declared_upload_baskets"]})
    datasets = {}
    for row in tables["datasets"]:
        _uuid(row["id"])
        _uuid(row["owner_id"])
        _require(row["owner_id"] in owners)
        _sha(row["content_sha256"])
        _require(
            row["id"] not in datasets
            and row["market"] == "US"
            and row["is_synthetic"] is False
        )
        _require(
            date.fromisoformat(row["start_date"]) < date.fromisoformat(row["end_date"])
        )
        _require(row["storage_key"] == f"datasets/{row['owner_id']}/{row['id']}")
        datasets[row["id"]] = row
    physical = {row["path"]: row for row in snapshot["datasets"]}
    _require(len(physical) == len(snapshot["datasets"]))
    member_groups = {}
    for member in tables["declared_upload_basket_members"]:
        _uuid(member["basket_id"])
        member_groups.setdefault(member["basket_id"], []).append(member)
    baskets, used, target_ids, requests = {}, set(), set(), set()
    from packages.rdagent_adapter.declared_upload import validate_declared_basket

    for row in tables["declared_upload_baskets"]:
        for key in ("id", "owner_id", "dataset_id"):
            _uuid(row[key])
        _sha(row["manifest_sha256"])
        _sha(row["request_sha256"])
        stamp = datetime.fromisoformat(row["created_at"])
        _require(stamp.tzinfo is not None and row["profile"] == PROFILE)
        _require(row["id"] not in baskets and row["dataset_id"] not in target_ids)
        _require(
            type(row["idempotency_key"]) is str
            and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", row["idempotency_key"])
        )
        _require((row["owner_id"], row["idempotency_key"]) not in requests)
        requests.add((row["owner_id"], row["idempotency_key"]))
        target_ids.add(row["dataset_id"])
        dataset = datasets[row["dataset_id"]]
        _require(dataset["source"] == "declared_upload_basket")
        _require(
            (dataset["owner_id"], dataset["content_sha256"])
            == (row["owner_id"], row["manifest_sha256"])
        )
        key = dataset["storage_key"]
        raw = read_bytes(key + "/manifest.json", 2 * 1024**2)
        _require(hashlib.sha256(raw).hexdigest() == row["manifest_sha256"])
        manifest = parse_json(raw, limits=LIMITS)
        _require(manifest == dataset["manifest"])
        _require(
            (dataset["start_date"], dataset["end_date"])
            == (manifest["start"], manifest["end"])
        )
        artifacts = retained_artifacts(manifest, key, read_bytes, declared_upload=True)
        validate_declared_basket(
            raw,
            artifacts,
            dataset_id=UUID(dataset["id"]),
            owner_id=UUID(row["owner_id"]),
        )
        members = member_groups.get(row["id"], [])
        _require(len(members) == 2 and {m["ordinal"] for m in members} == {0, 1})
        ordered_ids = [
            m["source_dataset_id"] for m in sorted(members, key=lambda m: m["ordinal"])
        ]
        _require(ordered_ids == sorted(set(ordered_ids)) and len(set(ordered_ids)) == 2)
        _require(
            row["request_sha256"]
            == digest(
                {
                    "source_dataset_ids": ordered_ids,
                    "acknowledge_unverified": True,
                    "idempotency_key": row["idempotency_key"],
                }
            )
        )
        for member in members:
            ordinal = member["ordinal"]
            _require(type(ordinal) is int and member["owner_id"] == row["owner_id"])
            _uuid(member["source_dataset_id"])
            _sha(member["source_manifest_sha256"])
            _require(datetime.fromisoformat(member["created_at"]).tzinfo is not None)
            source = datasets[member["source_dataset_id"]]
            _require(source["source"] == "upload")
            ref = manifest["sources"][ordinal]
            _require(
                (source["owner_id"], source["content_sha256"], source["id"])
                == (
                    row["owner_id"],
                    member["source_manifest_sha256"],
                    ref["dataset_version_id"],
                )
            )
            _require(ref["manifest_sha256"] == source["content_sha256"])
            original = read_bytes(source["storage_key"] + "/manifest.json", 2 * 1024**2)
            _require(
                original == artifacts[f"sources/{ordinal}/manifest.json"]
                and hashlib.sha256(original).hexdigest() == source["content_sha256"]
            )
            _require(parse_json(original, limits=LIMITS) == source["manifest"])
            _require(
                (source["start_date"], source["end_date"])
                == (
                    source["manifest"]["request"]["start"],
                    source["manifest"]["request"]["end"],
                )
            )
            _require(
                (source["start_date"], source["end_date"])
                == (dataset["start_date"], dataset["end_date"])
            )
            for name, content in artifacts.items():
                prefix = f"sources/{ordinal}/"
                if name.startswith(prefix) and name != prefix + "manifest.json":
                    _require(
                        read_bytes(
                            source["storage_key"] + "/" + name[len(prefix) :],
                            len(content),
                        )
                        == content
                    )
            used.add(source["id"])
        used.add(dataset["id"])
        baskets[row["id"]] = row
    _require(
        all(m["basket_id"] in baskets for m in tables["declared_upload_basket_members"])
    )
    _require(used == set(datasets))
    for dataset in datasets.values():
        entry = physical[dataset["storage_key"]]
        _require(
            (entry["sha256"], entry["manifest"])
            == (dataset["content_sha256"], dataset["manifest"])
        )
    _require(
        {
            path
            for path, row in physical.items()
            if (
                row["manifest"].get("profile") == PROFILE
                or row["manifest"].get("schema_version") == 3
            )
        }
        == {datasets[i]["storage_key"] for i in target_ids}
    )

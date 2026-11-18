"""Pure config differential only: no admitted graph, SDK or native acceptance."""

import copy
import hashlib
import json
import os
import time
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from packages.factors.contracts import canonical_bytes, digest
from tests.integration.test_rd_controller_v3_predicates import configured
from tests.integration.test_row_validation_invariants import catalog
from tests.rdagent.test_rdagent_workflow_child_contracts import config
from tests.sql_probes import canonical_text_reuse_probe as probe

pytestmark = pytest.mark.postgres


def fixtures():
    return {
        probe.WORKFLOW: config().model_dump(mode="json"),
        probe.PROVIDER: configured(True),
    }


def sized_snapshot(value, name, size):
    value = copy.deepcopy(value)
    if name == probe.WORKFLOW:
        snapshot = value["workflow_inputs"]
        while len(snapshot["inputs"]) < 4:
            item = copy.deepcopy(snapshot["inputs"][0])
            item["ref"]["record_id"], item["ref"]["version_id"] = (
                str(uuid4()),
                str(uuid4()),
            )
            snapshot["inputs"].append(item)
        snapshot["selection"]["refs"] = [item["ref"] for item in snapshot["inputs"]]
        snapshot["binding"]["input_sha256"] = digest(snapshot["selection"])
        items, limit, char = snapshot["inputs"], 2048, "é"
    else:
        snapshot = value["steer_snapshot"]
        snapshot["versions"] = [
            {
                "schema_version": "mineagent.rdagent-steer-version.v1",
                "steer_id": str(uuid4()),
                "version_id": str(uuid4()),
                "ordinal": 1,
                "kind": "hypothesis",
                "text": "x",
                "text_sha256": "0" * 64,
            }
            for _ in range(8)
        ]
        items, limit, char = snapshot["versions"], 2048, "é"
    for item in items:
        item["text"] = "x"
        item["text_sha256"] = hashlib.sha256(b"x").hexdigest()
    left = size - len(canonical_bytes(snapshot))
    if left < 0:
        raise AssertionError("Boundary fixture exceeds requested size")
    for item in items:
        # Canonical ASCII escaping makes é six bytes; hashes have fixed width.
        width = 6 if char == "é" else 1
        count = min(limit - 1, left // width)
        item["text"] += char * count
        left -= count * width
        if char == "é" and count < limit - 1:
            extra = min(limit - len(item["text"]), left)
            item["text"] += "x" * extra
            left -= extra
        item["text_sha256"] = hashlib.sha256(item["text"].encode()).hexdigest()
    if left or len(canonical_bytes(snapshot)) != size:
        raise AssertionError("Exact canonical boundary fixture required")
    value[
        "workflow_inputs_sha256" if name == probe.WORKFLOW else "steer_snapshot_sha256"
    ] = digest(snapshot)
    return value


def corpus(value, name):
    values = [
        ("valid", value),
        ("json_null", None),
        ("empty", {}),
        ("array", []),
        ("scalar", 1.5),
        ("false", False),
    ]
    for field, replacement in (
        ("max_iterations", 1.5),
        ("schema_version", "unknown"),
        ("steer_snapshot", None),
        ("steer_snapshot_sha256", "f" * 64),
    ):
        changed = copy.deepcopy(value)
        changed[field] = replacement
        values.append(("invalid_" + field, changed))
    deep = "leaf"
    for _ in range(26):
        deep = {"nested": deep}
    changed = copy.deepcopy(value)
    changed["unknown"] = deep
    values.append(("early_schema_reject_deep", changed))
    changed = copy.deepcopy(value)
    changed["task"]["name"] = "漢字 café"
    values.append(("non_ascii", changed))
    for size in (32767, 32768, 32769):
        values.append(
            ("snapshot_bytes_" + str(size), sized_snapshot(value, name, size))
        )
    if name == probe.WORKFLOW:
        changed = sized_snapshot(value, name, 32769)
        changed["max_iterations"] = 2
        values.append(("early_semantic_reject_oversize", changed))
    result = [
        (label, json.dumps(item, ensure_ascii=True, allow_nan=False))
        for label, item in values
    ]
    return [("sql_null", None), *result]


def scalar_outcome(connection, sql, encoded):
    try:
        with connection.begin_nested():
            value = connection.execute(text(sql), {"value": encoded}).scalar_one()
        return ("value", value)
    except DBAPIError as error:
        state = getattr(error.orig, "sqlstate", None)
        if state not in {"22023", "22003", "22P02", "54001", "57014"}:
            raise AssertionError("Unexpected pure predicate SQL error") from None
        return ("error", state)


def outcome(connection, name, encoded):
    return scalar_outcome(connection, f"SELECT {name}(CAST(:value AS jsonb))", encoded)


def rows(connection):
    tables = (
        connection.exec_driver_sql("""SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname=current_schema() AND c.relkind='r' ORDER BY c.relname LIMIT 257""")
        .scalars()
        .all()
    )
    if not 0 < len(tables) <= 256:
        raise AssertionError("Pure proof table bound exceeded")
    result = []
    for table in tables:
        if not probe.re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table):
            raise AssertionError("Pure proof table identity invalid")
        count, checksum = (
            connection.exec_driver_sql(f"""SELECT count(*),encode(sha256(convert_to(coalesce(jsonb_agg(value ORDER BY value::text)::text,'[]'),'UTF8')),'hex')
          FROM (SELECT to_jsonb(t) AS value FROM "{table}" t LIMIT 16385) q""").one()
        )
        if count > 16384:
            raise AssertionError("Pure proof row bound exceeded")
        result.append((table, count, checksum))
    return result


def explicit():
    if os.environ.get("MINEAGENT_PURE_CANONICAL_REUSE_REQUIRED") != "1":
        pytest.fail("Explicit isolated pure canonical proof required", pytrace=False)


def test_pure_canonical_original_candidate_restored_differential(migrated_engine):
    explicit()
    values = {name: corpus(value, name) for name, value in fixtures().items()}
    with migrated_engine.connect() as connection, connection.begin():
        probe.check(connection)
        before = catalog(connection), rows(connection)
        expected = {
            (name, label): outcome(connection, name, value)
            for name, items in values.items()
            for label, value in items
        }
        if any(expected[(name, "valid")] != ("value", True) for name in probe.CHANGED):
            raise AssertionError(
                "Existing genuine config fixtures must pass original predicates"
            )
        for name in probe.CHANGED:
            for label in ("non_ascii", "snapshot_bytes_32767", "snapshot_bytes_32768"):
                if expected[(name, label)] != ("value", True):
                    raise AssertionError(
                        "Genuine valid boundary must pass original predicate"
                    )
            if expected[(name, "snapshot_bytes_32769")] != ("value", False):
                raise AssertionError(
                    "Original byte boundary must reject oversized snapshot"
                )
        probe.install(connection, candidate_enabled=True)
        for name, items in values.items():
            for label, value in items:
                if outcome(connection, name, value) != expected[(name, label)]:
                    raise AssertionError("Pure candidate differential mismatch")
        probe.install(connection, candidate_enabled=False)
        for name, items in values.items():
            for label, value in items:
                if outcome(connection, name, value) != expected[(name, label)]:
                    raise AssertionError(
                        "Restored pure predicate differential mismatch"
                    )
        if (catalog(connection), rows(connection)) != before:
            raise AssertionError("Pure proof changed retained catalog or rows")
        connection.rollback()


def test_pure_canonical_preflight_rejects_helper_cost_drift(migrated_engine):
    explicit()
    with migrated_engine.connect() as connection, connection.begin():
        probe.check(connection)
        before = catalog(connection), rows(connection)
        with connection.begin_nested() as nested:
            connection.exec_driver_sql(
                "ALTER FUNCTION mineagent_execution_metadata_sha(jsonb) COST 9999"
            )
            with pytest.raises(ValueError, match="metadata differs"):
                probe.install(connection, candidate_enabled=True)
            nested.rollback()
        if (catalog(connection), rows(connection)) != before:
            raise AssertionError("Pure preflight drift probe changed retained state")
        connection.rollback()


def test_pure_canonical_cached_text_hash_helper_equivalence(migrated_engine):
    explicit()
    values = [None, "null", "{}", '"é漢字"', "1.5", '"\\n"']
    values += [
        json.dumps(
            value["workflow_inputs"]
            if name == probe.WORKFLOW
            else value["steer_snapshot"]
        )
        for name, value in fixtures().items()
    ]
    with migrated_engine.connect() as connection, connection.begin():
        probe.check(connection)
        before = catalog(connection), rows(connection)
        for encoded in values:
            original = scalar_outcome(
                connection,
                "SELECT mineagent_execution_metadata_sha(CAST(:value AS jsonb))",
                encoded,
            )
            cached = scalar_outcome(
                connection,
                "SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(mineagent_rd_canonical_json(CAST(:value AS jsonb),true),'UTF8')),'hex')",
                encoded,
            )
            if original != cached:
                raise AssertionError("Exact original hash pipeline differs")
        if (catalog(connection), rows(connection)) != before:
            raise AssertionError("Hash helper proof changed retained state")
        connection.rollback()


def test_pure_canonical_bounded_sql_cost_observation(migrated_engine, capsys):
    explicit()
    values = {
        (name, size): json.dumps(
            value if size == "default" else sized_snapshot(value, name, 32768)
        )
        for name, value in fixtures().items()
        for size in ("default", "large_valid")
    }
    measurements = {}
    with migrated_engine.connect() as connection, connection.begin():
        probe.check(connection)
        before = catalog(connection), rows(connection)
        for phase in ("original", "candidate", "restored"):
            if phase != "original":
                probe.install(connection, candidate_enabled=phase == "candidate")
            for (name, size), encoded in values.items():
                if outcome(connection, name, encoded) != ("value", True):
                    raise AssertionError("Pure cost warmup requires valid fixture")
                started = time.perf_counter()
                for _ in range(8):
                    if outcome(connection, name, encoded) != ("value", True):
                        raise AssertionError(
                            "Pure cost sample requires valid original fixture"
                        )
                measurements[
                    phase
                    + ("_workflow_" if name == probe.WORKFLOW else "_provider_")
                    + size
                ] = min(
                    3_600_000, max(0, round((time.perf_counter() - started) * 1000))
                )
        if (catalog(connection), rows(connection)) != before:
            raise AssertionError("Pure cost observation changed retained state")
        connection.rollback()
    encoded = probe.export_cost(measurements)
    with capsys.disabled():
        print("PURE_CANONICAL_COST_V1", encoded.rstrip())

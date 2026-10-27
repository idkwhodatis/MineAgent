"""Version dispatch and real-byte unit wiring; no whole cold-restore acceptance."""

import ast
from pathlib import Path

import pytest
from test_rdagent_controller_backup import retained as retained  # noqa: PLC0414 - pytest fixture export

from backend.app import numeric_process_backup
from backend.app import research_backup as module


def empty(schema):
    return {
        "schema_version": schema,
        "tables": {key: [] for key in module.FIELDS},
        "external_bindings": {"runs": [], "attempts": []},
    }


@pytest.mark.parametrize(
    "schema",
    [
        module.SCHEMA,
        module.SCHEMA_V2,
        module.SCHEMA_V3,
        module.SCHEMA_V4,
        module.SCHEMA_V5,
    ],
)
def test_explicit_empty_schema_dispatch_preserves_historical_closures(schema):
    module.validate_closure(
        empty(schema), lambda *a: None, lambda *a: None, 100, inventory=[]
    )
    assert module.manifest_keys(empty(schema)) == set()


def test_v5_uses_exact_v4_field_inventory_without_optional_columns():
    assert module.closure_fields(module.SCHEMA_V5) is module.FIELDS_V4
    with pytest.raises(ValueError):
        module.closure_fields("mineagent.research-restore-closure.v11")


@pytest.mark.parametrize(
    "schema",
    [None, module.SCHEMA, module.SCHEMA_V2, module.SCHEMA_V3, module.SCHEMA_V4],
)
def test_child_path_remains_rejected_by_every_historical_revision(schema):
    path = "research-processes/00000000-0000-0000-0000-000000000001/00000000-0000-0000-0000-000000000002/control/child.json"
    with pytest.raises(ValueError):
        module.validate_path(path, schema=schema)
    module.validate_path(path, schema=module.SCHEMA_V5)


def test_v5_dispatch_uses_actual_retained_bytes_and_rd_source_graph(retained):
    f = retained
    indexes = {
        **f["indexes"],
        "research_process_admissions": {f["row"]["id"]: f["row"]},
    }
    calls = []

    def read(key, sha, maximum):
        calls.append(key)
        raw = (f["root"] / key).read_bytes()
        assert len(raw) <= maximum
        return raw

    def check(key, sha, size):
        assert f["physical"][key] == {"sha256": sha, "size_bytes": size}

    module._validate_processes(
        indexes,
        f["physical"],
        check,
        lambda key: None,
        research_schema=module.SCHEMA_V5,
        read_bytes=read,
        rd_source_closure=f["rd"],
    )
    assert set(calls) == set(f["physical"]) and len(calls) == 4
    key = f["prefix"] + "child.json"
    (f["root"] / key).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Actual protected"):
        module._validate_processes(
            indexes,
            f["physical"],
            check,
            lambda key: None,
            research_schema=module.SCHEMA_V5,
            read_bytes=read,
            rd_source_closure=f["rd"],
        )


@pytest.mark.parametrize("missing", ["reader", "rd"])
def test_v5_cannot_fall_back_to_reencoded_json(retained, missing):
    f = retained
    indexes = {
        **f["indexes"],
        "research_process_admissions": {f["row"]["id"]: f["row"]},
    }
    with pytest.raises(ValueError, match="actual bytes and RD source"):
        module._validate_processes(
            indexes,
            f["physical"],
            lambda *a: None,
            lambda key: {},
            research_schema=module.SCHEMA_V5,
            read_bytes=None if missing == "reader" else lambda *a: b"",
            rd_source_closure=None if missing == "rd" else f["rd"],
        )


@pytest.mark.parametrize(
    "schema", [module.SCHEMA_V3, module.SCHEMA_V4, module.SCHEMA_V5]
)
def test_existing_numeric_dispatch_is_not_replaced(schema, monkeypatch):
    row = {
        "id": "fixed",
        "schema_version": "mineagent.process-admission.v2",
        "profile": "mineagent.factor-prefix-candidate.v2",
    }
    indexes = {
        "research_process_admissions": {"fixed": row},
        "research_process_states": {"fixed": {}},
        "research_process_receipts": {},
    }
    seen = []
    monkeypatch.setattr(
        numeric_process_backup,
        "validate_numeric_closure_unit",
        lambda *args: seen.append(args[0]),
    )
    module._validate_processes(
        indexes, {}, lambda *a: None, lambda *a: None, research_schema=schema
    )
    assert seen == [row]


def test_controller_manifest_has_child_not_numeric_extraction(retained, monkeypatch):
    # Isolated manifest routing; full row graph is independently exercised above.
    monkeypatch.setattr(module, "_indexes", lambda *a: None)
    closure = empty(module.SCHEMA_V5)
    closure["tables"]["research_process_admissions"] = [retained["row"]]
    paths = module.manifest_keys(closure)
    assert len(paths) == 4
    assert retained["prefix"] + "child.json" in paths
    assert not any(path.endswith("/extraction.json") for path in paths)


def test_operations_and_retention_forward_same_read_byte_callback_before_validation():
    root = Path(__file__).resolve().parents[2]
    for name in ("operations_backup.py", "operations_retention.py"):
        tree = ast.parse((root / "backend/app" / name).read_text())
        matching = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "validate_closure"
            ):
                keywords = {k.arg: k.value for k in node.keywords}
                if "inventory" in keywords:
                    matching.append(node)
                    assert ast.unparse(keywords["read_bytes"]) == "read_rd_bytes"
                    assert (
                        ast.unparse(keywords["rd_source_closure"])
                        == "snapshot.get('rd_source_closure')"
                    )
        assert len(matching) == 1
        readers = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "read_rd_bytes"
        ]
        assert len(readers) == 1 and readers[0].lineno < matching[0].lineno

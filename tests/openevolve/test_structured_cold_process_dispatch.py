"""Shared dispatch over actual retained controller bytes; daemon/SQL are doubles."""

import json

import pytest

from backend.app import research_backup as module
from packages.research_runtime.process_contracts import ProcessError
from tests.openevolve import test_structured_backup as fixtures


@pytest.fixture
def physical_graph(monkeypatch, tmp_path):
    return fixtures.physical_graph.__wrapped__(monkeypatch, tmp_path)


def test_shared_v11_preserves_distinct_actual_controller_proof(physical_graph):
    root, row, indexes, physical = physical_graph
    indexes["research_process_admissions"] = {row["id"]: row}

    def read(key, sha, size):
        value = (root / key).read_bytes()
        import hashlib

        assert len(value) == size and hashlib.sha256(value).hexdigest() == sha
        return value

    result = module._validate_processes(
        indexes,
        physical,
        lambda *a: None,
        lambda key: json.loads((root / key).read_bytes()),
        research_schema=module.SCHEMA_V13,
        read_bytes=read,
    )
    assert result["legacy_oe"] == {} and result["rejections"] == {}
    assert result["structured"][row["id"]]["completion"] is not None
    with pytest.raises(ProcessError, match="numeric_process_profile"):
        module._validate_processes(
            indexes,
            physical,
            lambda *a: None,
            lambda key: json.loads((root / key).read_bytes()),
            research_schema=module.SCHEMA_V10,
            read_bytes=read,
        )


def test_manifest_only_adds_actual_normal_receipt_names(physical_graph, monkeypatch):
    _, row, indexes, _ = physical_graph
    monkeypatch.setattr(module, "_indexes", lambda *a: None)
    closure = {
        "schema_version": module.SCHEMA_V13,
        "tables": {
            "research_objects": [],
            "research_object_publications": [],
            "research_process_admissions": [row],
            "research_process_receipts": list(
                indexes["research_process_receipts"].values()
            ),
        },
    }
    keys = module.manifest_keys(closure)
    assert {key.rsplit("/", 1)[-1] for key in keys} == {
        "launch.json",
        "identity.json",
        "child.json",
        "stop.json",
        "completion.json",
    }
    closure["tables"]["research_process_receipts"] = [
        r
        for r in closure["tables"]["research_process_receipts"]
        if r["kind"] != "controller_completion"
    ]
    assert not any(
        key.endswith("/completion.json") for key in module.manifest_keys(closure)
    )


def test_shared_rejection_dispatch_returns_actual_failed_evidence_only(tmp_path):
    import hashlib

    from tests.numeric_rejection_runtime.test_backup import fixture

    row, indexes, _ = fixture(tmp_path)
    indexes["research_process_admissions"] = {row["id"]: row}
    physical = {
        p.relative_to(tmp_path).as_posix(): {}
        for p in tmp_path.rglob("*")
        if p.is_file()
    }

    def check(key, sha, size):
        raw = (tmp_path / key).read_bytes()
        assert len(raw) == size and hashlib.sha256(raw).hexdigest() == sha

    result = module._validate_processes(
        indexes,
        physical,
        check,
        lambda key: json.loads((tmp_path / key).read_bytes()),
        research_schema=module.SCHEMA_V13,
    )
    assert result["structured"] == result["legacy_oe"] == {}
    assert result["rejections"][row["id"]][-1].outcome == "child_nonzero"
    with pytest.raises(ValueError):
        module._validate_processes(
            indexes,
            physical,
            check,
            lambda key: json.loads((tmp_path / key).read_bytes()),
            research_schema=module.SCHEMA_V10,
        )

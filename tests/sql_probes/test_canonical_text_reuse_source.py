"""Offline source/provenance contracts, not PostgreSQL execution evidence."""

import copy
import hashlib
from pathlib import Path

import pytest

from tests.sql_probes import canonical_text_reuse_probe as m


def test_original_bodies_match_verified_installed_manifest():
    for name in m.CHANGED:
        assert (
            hashlib.sha256(m._body(name, False).encode()).hexdigest()
            == m.pins()["functions"][name]["body_sha256"]
        )
    rows = list(copy.deepcopy(m.pins()["functions"]).values())
    m.verify_functions(rows)
    for row in rows:
        assert row["volatility"] == "i" and row["security_definer"] is False
    assert m.pins()["functions"]["mineagent_execution_metadata_sha"]["strict"] is True
    assert m.pins()["functions"]["mineagent_rd_canonical_json"]["strict"] is True


def test_canonical_reuse_keeps_schema_gates_prefixes_and_tail_exact():
    for name in m.CHANGED:
        original, changed = m.original(name), m.candidate(name)
        assert (
            original.count("EXCEPTION WHEN OTHERS THEN RETURN false;")
            == changed.count("EXCEPTION WHEN OTHERS THEN RETURN false;")
            == 1
        )
        assert (
            original[original.index(" FOR x IN") :]
            == changed[changed.index(" FOR x IN") :]
        )
        assert "CREATE OR REPLACE FUNCTION" in changed
        assert "IMMUTABLE" in changed
    provider = m.candidate(m.PROVIDER)
    assert provider.count("mineagent_rd_canonical_json(v->'steer_snapshot',true)") == 1
    assert provider.index("mineagent_rd_provider_payload('config',v)") < provider.index(
        "steer_text:="
    )
    assert provider.index(
        "octet_length(mineagent_rd_canonical_json(v,true))>1048576"
    ) < provider.index("steer_text:=")
    workflow = m.candidate(m.WORKFLOW)
    assert workflow.count("mineagent_rd_canonical_json(w,true)") == 1
    for required in (
        "mineagent_rd_schema(",
        "mineagent_rd_provider_config(base)",
        "v->>'max_iterations' IS DISTINCT FROM '1'",
        "v#>>'{steer_snapshot,owner_id}' IS DISTINCT FROM b->>'owner_id'",
    ):
        assert workflow.index(required) < workflow.index("workflow_text:=")
    assert "mineagent_execution_metadata_sha(s)" in workflow
    assert (
        "pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(workflow_text,'UTF8')),'hex')"
        in workflow
    )


def test_frozen_hash_helper_is_exact_strict_pipeline():
    import re

    from backend.app.row_validation_oe_032g_base_sql import literal

    source = literal("021_execution_snapshots.py", "NORMALIZER_RESULT_SQL")
    matches = re.findall(
        r"CREATE FUNCTION @SCHEMA@\.mineagent_execution_metadata_sha\(.*?\$\$;",
        source,
        re.DOTALL,
    )
    assert len(matches) == 1
    body = matches[0].split("$$")[1].replace("@SCHEMA@", '"@SCHEMA@"')
    assert (
        hashlib.sha256(body.encode()).hexdigest()
        == m.pins()["functions"]["mineagent_execution_metadata_sha"]["body_sha256"]
    )
    assert (
        body.strip()
        == "SELECT encode(sha256(convert_to(\"@SCHEMA@\".mineagent_rd_canonical_json(v,true),'UTF8')),'hex')"
    )


def test_preflight_refuses_any_frozen_metadata_change():
    for field, value in (
        ("body_sha256", "f" * 64),
        ("definition_sha256", "e" * 64),
        ("strict", True),
        ("security_definer", True),
    ):
        rows = list(copy.deepcopy(m.pins()["functions"]).values())
        target = next(row for row in rows if row["name"] == m.WORKFLOW)
        target[field] = value
        with pytest.raises(ValueError, match="metadata differs"):
            m.verify_functions(rows)


def test_candidate_full_definition_is_not_blindly_erased():
    rows = list(copy.deepcopy(m.pins()["functions"]).values())
    for row in rows:
        if row["name"] in m.CHANGED:
            row["raw_body"] = m._body(row["name"], True)
            row["raw_definition"] = (
                "changed header COST 9999 AS $$" + row["raw_body"] + "$$"
            )
    with pytest.raises(ValueError, match="attributes changed"):
        m.verify_functions(rows, candidate_enabled=True)


def test_genuine_corpus_sizes_and_expected_python_boundaries():
    from packages.rdagent_adapter.provider_config import ProviderFactorResearchConfig
    from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig
    from tests.integration.test_canonical_text_reuse_probe import corpus, fixtures

    for name, value in fixtures().items():
        entries = dict(corpus(value, name))
        assert entries["sql_null"] is None and entries["json_null"] == "null"
        model = (
            WorkflowFactorResearchConfig
            if name == m.WORKFLOW
            else ProviderFactorResearchConfig
        )
        for label in (
            "valid",
            "non_ascii",
            "snapshot_bytes_32767",
            "snapshot_bytes_32768",
        ):
            model.model_validate_json(entries[label])
        with pytest.raises(ValueError):
            model.model_validate_json(entries["snapshot_bytes_32769"])
        assert "early_schema_reject_deep" in entries
        assert "invalid_max_iterations" in entries


def test_proof_is_separate_bounded_and_never_native_activated():
    root = Path(__file__).resolve().parents[2]
    for directory in ("backend", "workers", "runtimes"):
        assert not any(
            "canonical_text_reuse_probe" in path.read_text()
            for path in (root / directory).rglob("*.py")
        )
    source = (root / "tests/integration/test_canonical_text_reuse_probe.py").read_text()
    assert "MINEAGENT_PURE_CANONICAL_REUSE_REQUIRED" in source
    assert "range(8)" in source
    assert "probe.export_cost(measurements)" in source
    assert source.count("connection.rollback()") == 4
    assert "candidate_enabled=False" in source


def test_candidate_restores_actual_pinned_full_postgresql_definitions():
    rows = list(copy.deepcopy(m.pins()["functions"]).values())
    for row in rows:
        if row["name"] not in m.CHANGED:
            continue
        name = row["name"]
        original = (
            f"CREATE OR REPLACE FUNCTION @SCHEMA@.{name}(v jsonb)\n"
            " RETURNS boolean\n LANGUAGE plpgsql\n IMMUTABLE\n"
            " SET search_path TO '@SCHEMA@', 'pg_catalog'\nAS $function$"
            + m._body(name, False)
            + "$function$\n"
        )
        assert hashlib.sha256(original.encode()).hexdigest() == row["definition_sha256"]
        row["raw_body"] = m._body(name, True)
        row["raw_definition"] = original.replace(
            m._body(name, False), row["raw_body"], 1
        )
        row["body_sha256"] = hashlib.sha256(row["raw_body"].encode()).hexdigest()
        row["definition_sha256"] = hashlib.sha256(
            row["raw_definition"].encode()
        ).hexdigest()
    m.verify_functions(rows, candidate_enabled=True)


def test_cost_export_has_only_closed_cells_and_fixed_destination(monkeypatch, tmp_path):
    import json

    cells = {
        phase + "_" + function + "_" + size: 1
        for phase in ("original", "candidate", "restored")
        for function in ("workflow", "provider")
        for size in ("default", "large_valid")
    }
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "MINEAGENT_SQL_CANDIDATE_MEASUREMENTS_DIR",
        str(tmp_path / "var/sql-candidate-proof"),
    )
    encoded = m.export_cost(cells)
    path = tmp_path / "var/sql-candidate-proof/pure-canonical-cost.json"
    assert path.read_text() == encoded
    assert set(json.loads(encoded)) == {
        "samples_per_cell",
        "warmups_per_cell",
        "scope",
        "elapsed_ms",
    }
    assert json.loads(encoded)["samples_per_cell"] == 8
    for invalid in ({**cells, "secret": 3}, {**cells, next(iter(cells)): "private"}):
        with pytest.raises(ValueError, match="Closed"):
            m.export_cost(invalid)
    monkeypatch.setenv(
        "MINEAGENT_SQL_CANDIDATE_MEASUREMENTS_DIR", str(tmp_path / "other")
    )
    with pytest.raises(ValueError, match="artifact directory"):
        m.export_cost(cells)

"""Offline SQL candidate contracts; no hosted/native acceptance claim."""

import copy
import hashlib
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.sql_probes import reverse_heartbeat_candidate as m


def minimal(monkeypatch):
    manifest = copy.deepcopy(m.pins())
    result = {
        "functions": [manifest["original_function"]],
        "schema_triggers": [],
        "schema_constraints": [],
        "schema_columns": [],
    }
    manifest["function_names"] = [m.NAME]
    manifest["relation_names"] = []
    manifest["counts"] = {key: len(rows) for key, rows in result.items()}
    manifest["digests"] = {key: m._digest(rows) for key, rows in result.items()}
    monkeypatch.setattr(m, "pins", lambda: copy.deepcopy(manifest))
    return copy.deepcopy(result)


def test_exact_guard_is_only_sql_delta_and_all_original_events_remain():
    original, candidate = m.original(), m.candidate()
    assert candidate.replace(m.GUARD, "", 1) == original
    assert candidate.count(m.GUARD) == 1
    assert "TG_TABLE_SCHEMA='@SCHEMA@'" in m.GUARD
    assert "TG_TABLE_NAME='execution_attempts' AND TG_OP='UPDATE'" in m.GUARD
    assert m.GUARD.count("ARRAY['heartbeat_at','lease_expires_at']") == 2
    assert "IS NOT DISTINCT FROM" in m.GUARD
    assert "RETURN NULL" in m.GUARD
    assert "CONTINUE" not in m.GUARD
    assert "mineagent_workflow_lock(root.owner_id,root.id)" in candidate
    assert "ARRAY[to_jsonb(OLD),to_jsonb(NEW)]" in candidate
    assert "TG_TABLE_SCHEMA='public'" in m.render(True)
    assert "@SCHEMA@" not in m.render(True)
    assert (
        hashlib.sha256(original.split("$$")[1].encode()).hexdigest()
        == m.pins()["original_function"]["body_sha256"]
    )


def test_exact_installed_manifest_has_complete_reviewed_coverage():
    pins = m.pins()
    assert len(pins["function_names"]) == 60
    assert len(pins["relation_names"]) == 64
    assert pins["counts"] == {
        "functions": 60,
        "schema_triggers": 353,
        "schema_constraints": 1692,
        "schema_columns": 826,
    }
    assert pins["revision"] == "032g_schema_keyset_cost"
    assert m.NAME in pins["function_names"]
    assert "factor_execution_evaluation_outcomes" in pins["relation_names"]
    assert "factor_generation_boundaries" in pins["relation_names"]


def test_preflight_rejects_function_security_or_body_drift(monkeypatch):
    original = minimal(monkeypatch)
    m.verify(original)
    for key, value in (
        ("body_sha256", "a" * 64),
        ("security_definer", True),
        ("configuration_sha256", "b" * 64),
        ("identity_arguments", "x integer"),
    ):
        changed = copy.deepcopy(original)
        changed["functions"][0][key] = value
        with pytest.raises(ValueError, match="coverage differs"):
            m.verify(changed)


def test_candidate_accepts_only_exact_body_and_timestamp_flag(monkeypatch):
    result = minimal(monkeypatch)
    row = result["functions"][0]
    row["body_sha256"] = hashlib.sha256(
        m.render(True).split("$$")[1].replace("public", "@SCHEMA@").encode()
    ).hexdigest()
    row["definition_sha256"] = "a" * 64
    result["restored_reverse_definition_sha256"] = m.pins()["original_function"][
        "definition_sha256"
    ]
    row["lexical_heartbeat_present"] = True
    m.verify(result, candidate_enabled=True)
    # Verify must not mutate caller's original collected evidence.
    assert row["lexical_heartbeat_present"] is True
    row["body_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="Exact candidate body"):
        m.verify(result, candidate_enabled=True)


def test_preflight_rejects_trigger_constraint_and_column_drift(monkeypatch):
    result = minimal(monkeypatch)
    for key in ("schema_triggers", "schema_constraints", "schema_columns"):
        changed = copy.deepcopy(result)
        # Add relevant row by changing frozen relation coverage, not real pins.
        old_pins = m.pins()
        old_pins["relation_names"] = ["execution_attempts"]
        monkeypatch.setattr(
            m, "pins", lambda old_pins=old_pins: copy.deepcopy(old_pins)
        )
        changed[key].append({"relation": "execution_attempts", "name": "changed"})
        with pytest.raises(ValueError, match="coverage differs"):
            m.verify(changed)


def test_readonly_guard_precedes_every_other_metadata_query():
    class Connection:
        def exec_driver_sql(self, sql):
            assert sql == "SHOW transaction_read_only"
            return self

        def scalar_one(self):
            return "off"

    with pytest.raises(ValueError, match="Read-only"):
        m.inventory(Connection())


def test_original_restores_after_body_or_candidate_check_failure(monkeypatch):
    events = []
    active = [False]
    fail_check = [False]

    class Engine:
        @contextmanager
        def begin(self):
            yield self

        def exec_driver_sql(self, sql):
            if sql == m.render(True):
                active[0] = True
                events.append("install")
            elif sql == m.render(False):
                active[0] = False
                events.append("restore")
            else:
                pytest.fail("Unexpected SQL")

    def check(engine, candidate_enabled=False):
        assert active[0] is candidate_enabled
        if candidate_enabled and fail_check[0]:
            raise ValueError("preflight test failure")

    monkeypatch.setattr(m, "check", check)
    engine = Engine()
    failure = RuntimeError("body failure")
    with pytest.raises(RuntimeError) as caught, m.installed_candidate(engine):
        raise failure
    assert caught.value is failure and not active[0]
    fail_check[0] = True
    with (
        pytest.raises(ValueError, match="preflight test failure"),
        m.installed_candidate(engine),
    ):
        pytest.fail("Bad candidate yielded")
    assert events == ["install", "restore", "install", "restore"]


def test_no_production_import_and_real_probe_keeps_guarded_transaction_cases():
    root = Path(m.__file__).resolve().parents[2]
    for directory in ("backend", "workers", "runtimes"):
        assert not any(
            "reverse_heartbeat_candidate" in p.read_text()
            for p in (root / directory).rglob("*.py")
        )
    source = (
        root / "tests/integration/test_reverse_heartbeat_candidate.py"
    ).read_text()
    for required in (
        "_baseline_contention(state)",
        '"SET CONSTRAINTS ALL IMMEDIATE"',
        "writer.begin_nested()",
        "nested.rollback()",
        '"revert"',
        '"status"',
        "_require_unchanged(state.source, before)",
        "_restore_timestamps(state, times)",
        '"execution_attempts_run_id_fkey"',
    ):
        assert required in source
    assert "DISABLE TRIGGER" not in source
    assert 'ids=["032g"]' in source


def test_schema_normalization_does_not_corrupt_unrelated_identifiers():
    source = (
        'public.fn() "public".tbl public_key publication republic factor_publications'
    )
    normalized = m._normalize_sql(source)
    assert (
        normalized
        == f'{m.SURROGATE}.fn() "{m.SURROGATE}".tbl public_key publication republic factor_publications'
    )
    rows = m._normalize_rows(
        [
            {
                "name": "public",
                "relation": "factor_publications",
                "function_schema": "public",
                "acl": "public=X/owner",
                "body": source,
            }
        ]
    )
    assert rows[0]["name"] == "public"
    assert rows[0]["relation"] == "factor_publications"
    assert rows[0]["acl"] == "public=X/owner"
    assert rows[0]["function_schema"] == m.SURROGATE
    assert rows[0]["body"] == normalized


def test_full_definition_restoration_preserves_cost_and_other_attributes(monkeypatch):
    body = m._candidate_body()
    definition = (
        "CREATE OR REPLACE FUNCTION test() RETURNS trigger COST 100 AS $function$"
        + body
        + "$function$"
    )
    restored = definition.replace(body, m._original_body(), 1)
    expected = hashlib.sha256(restored.encode()).hexdigest()
    assert m.restored_definition_hash(body, definition) == expected
    for changed in (
        definition.replace("COST 100", "COST 200"),
        definition.replace("RETURNS trigger", "RETURNS trigger SECURITY DEFINER"),
    ):
        assert m.restored_definition_hash(body, changed) != expected
    with pytest.raises(ValueError, match="Exact candidate definition body"):
        m.restored_definition_hash(body, definition + body)
    result = minimal(monkeypatch)
    row = result["functions"][0]
    row["body_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    row["lexical_heartbeat_present"] = True
    result["restored_reverse_definition_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="full definition attributes differ"):
        m.verify(result, candidate_enabled=True)


def test_all60_public_rendered_source_bodies_match_verified_installed_pins():
    import ast
    import re

    root = Path(m.__file__).resolve().parents[2]
    sources = {}
    for path in sorted((root / "backend/alembic/versions").glob("*.py")):
        for node in ast.parse(path.read_text()).body:
            if not isinstance(node, ast.Assign) or "DOWNGRADE" in getattr(
                node.targets[0], "id", ""
            ):
                continue
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            if isinstance(value, str):
                for match in re.finditer(
                    r"CREATE(?: OR REPLACE)? FUNCTION (?:@SCHEMA@\.)?(mineagent_\w+)\(.*?\$\$;",
                    value,
                    re.DOTALL,
                ):
                    sources[match[1]] = match[0]
    expected = m.pins()["source_body_sha256"]
    assert len(expected) == 60
    for name, digest in expected.items():
        body = sources[name].split("$$")[1].replace("@SCHEMA@", '"public"')
        normalized = m._normalize_sql(body).replace(m.SURROGATE, "@SCHEMA@")
        assert hashlib.sha256(normalized.encode()).hexdigest() == digest


def test_restoration_matches_verified_installed_full_definition_not_only_synthetic():
    # PostgreSQL18.6 canonical header reconstructed from frozen original SQL;
    # its COMPLETE hash must match the independently captured hosted artifact.
    definition = (
        "CREATE OR REPLACE FUNCTION @SCHEMA@.mineagent_oe_workflow_reverse()\n"
        " RETURNS trigger\n LANGUAGE plpgsql\n"
        " SET search_path TO '@SCHEMA@', 'pg_catalog'\nAS $function$"
        + m._original_body()
        + "$function$\n"
    )
    installed = m.pins()["original_function"]
    assert (
        hashlib.sha256(m._original_body().encode()).hexdigest()
        == installed["body_sha256"]
    )
    assert (
        hashlib.sha256(definition.encode()).hexdigest()
        == installed["definition_sha256"]
    )
    candidate_definition = definition.replace(
        m._original_body(), m._candidate_body(), 1
    )
    assert (
        m.restored_definition_hash(m._candidate_body(), candidate_definition)
        == installed["definition_sha256"]
    )
    changed_cost = candidate_definition.replace(
        " LANGUAGE plpgsql", " LANGUAGE plpgsql\n COST 200"
    )
    assert (
        m.restored_definition_hash(m._candidate_body(), changed_cost)
        != installed["definition_sha256"]
    )

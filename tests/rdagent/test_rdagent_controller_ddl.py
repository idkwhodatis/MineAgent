"""Offline020 renderer checks; not SQL execution/physical identity acceptance."""

import ast
import hashlib
import json
import re
from pathlib import Path

import pytest

from backend.app import rdagent_controller_ddl as module
from backend.app.rdagent_persistence_ddl import CONTROLLER_PROFILE_GATE
from runtimes.rdagent_operational.process_journal import (
    ControllerChild,
    ControllerIdentity,
    ControllerIntent,
    ControllerStop,
)

ROOT = Path(__file__).resolve().parents[2]


def test_reviewed_source_pin_and_deterministic_schema_qualified_render():
    source = ROOT / "backend/alembic/versions/020_rd_controller.py"
    tree = ast.parse(source.read_text())
    frozen = next(
        ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "UPGRADE_SQL" for t in n.targets)
    )
    assert (
        hashlib.sha256(frozen.encode()).hexdigest()
        == "0356ba7a550b5432fdd9dc675f1b62bf7672b546c1196dfdbc68a9d855a0d069"
    )
    assert module.render_sql() == module.render_sql()
    sql = module.render_sql("rd_controller_test")
    assert "@S@" not in sql and "@RULES@" not in sql
    assert '"rd_controller_test".mineagent_rd_controller_consistent()' in sql
    assert 'SET search_path="rd_controller_test",pg_catalog' in sql


@pytest.mark.parametrize(
    "schema", ["x;DROP TABLE users", "Upper", "x.y", "", True, "a" * 64]
)
def test_unsafe_schema_rejected(schema):
    with pytest.raises(ValueError):
        module.render_sql(schema)


def test_preserves_historical_profile_gate_and_no_function_replacement():
    sql = module.render_sql()
    assert CONTROLLER_PROFILE_GATE in sql
    created = set(re.findall(r'CREATE FUNCTION "public"\.(\w+)', sql))
    assert created == {
        "mineagent_rd_controller_schema",
        "mineagent_rd_controller_payload",
        "mineagent_rd_controller_kernel",
        "mineagent_rd_controller_receipt_order",
        "mineagent_rd_controller_consistent",
    }
    assert "CREATE OR REPLACE" not in sql and "DROP FUNCTION" not in sql
    old = next((ROOT / "backend/alembic/versions").glob("015_*.py")).read_text()
    names = set(re.findall(r"CREATE FUNCTION (\w+)", old))
    assert not names & created
    assert "DROP TABLE" not in sql and "CASCADE" not in sql


def test_profile_and_receipt_kind_extensions_are_exact():
    sql = module.render_sql()
    assert (
        "schema_version='mineagent.process-admission.v2' AND role='rd_orchestrator'"
        in sql
    )
    assert (
        "profile='mineagent.rdagent-orchestrator.v2' AND ipc_policy='host_ticket'"
        in sql
    )
    assert "ticket_sha256 IS NOT NULL" in sql
    assert (
        "kind IN ('launch_intent','launch_identity','controller_child','stop','extraction')"
        in sql
    )
    assert "Child receipt belongs only to RD protected controller" in sql
    assert "RD controller cannot publish numeric extraction" in sql


def test_exact_deferred_participants_include_both_ledger_directions():
    actual = re.findall(
        r"CREATE CONSTRAINT TRIGGER rd_controller_closure AFTER INSERT OR UPDATE OR DELETE ON (\w+)",
        module.render_sql(),
    )
    assert tuple(actual) == module.TABLES
    assert len(set(actual)) == 10
    assert {
        "research_process_admissions",
        "research_process_states",
        "research_process_receipts",
        "research_budget_reservations",
        "research_budget_settlements",
        "research_budget_controls",
        "research_runs",
        "factor_research_specs",
        "factor_research_attempt_bindings",
        "factor_controller_launches",
    } == set(actual)
    assert module.render_sql().count("DEFERRABLE INITIALLY DEFERRED") == 10


def test_receipt_sequence_is_immediate_not_timestamp_inference():
    sql = module.render_sql()
    assert "CREATE TRIGGER rd_controller_receipt_order BEFORE INSERT" in sql
    assert "kind='stop')" in sql
    assert "Stopped controller receipt chain is closed" in sql
    assert "NEW.kind<>'launch_intent'" in sql
    assert "NEW.kind IN ('controller_child','stop')" in sql
    assert "Protected leader identity must precede child or stop" in sql
    assert "ORDER BY created_at" not in sql
    assert "ordinal" not in sql.replace("launch_ordinal", "")


def test_all_receipts_full_normalized_schema_fields_are_embedded():
    sql = module.render_sql()
    for kind, cls in zip(
        module.KINDS,
        (ControllerIntent, ControllerIdentity, ControllerChild, ControllerStop),
        strict=True,
    ):
        schema = module._schema(cls)
        encoded = json.dumps(schema, sort_keys=True, separators=(",", ":")).replace(
            "'", "''"
        )
        assert f"WHEN '{kind}' THEN rule := '{encoded}'::jsonb;" in sql
        assert set(schema["properties"]) == set(cls.model_fields)
        assert "exclusiveMinimum" not in encoded and "prefixItems" not in encoded
    kernel = module._schema(ControllerChild)["$defs"]["KernelIdentity"]
    assert set(kernel["properties"]) == {
        "host_pid",
        "start_ticks",
        "pid_namespace",
        "cgroup",
        "ppid",
        "uids",
        "gids",
    }
    assert (
        kernel["properties"]["uids"]["minItems"]
        == kernel["properties"]["uids"]["maxItems"]
        == 4
    )
    assert kernel["properties"]["gids"]["items"] == {"type": "integer"}


@pytest.mark.parametrize(
    "fragment",
    [
        "rr.engine<>'rdagent' OR rr.purpose<>'factor_research'",
        "sp.config<>rr.config OR sp.config_sha256<>rr.request_sha256",
        "ab.spec_id<>sp.id OR ab.resumed_checkpoint_id IS NOT NULL",
        "cl.attempt_binding_id<>ab.id",
        "a.image_id<>sp.config->'runtime'->>'orchestrator_image'",
        "cl.bootstrap<>expected_bootstrap",
        "intent->'admission'<>decl",
        "'runtime_name','mineagent-rd-controller-'||a.id::text",
        "p.payload_sha256<>encode(sha256(convert_to(",
        "ident->>'launch_sha256'<>launch_hash",
        "child->>'started_report_sha256'<>encode(sha256(convert_to(",
        "child->'controller'->'ppid'<>ident->'leader'->'host_pid'",
        "child->'namespace_pids'->0<>child->'controller'->'host_pid'",
        "child->'namespace_pids'->(n-1)<>child->'namespace_pid'",
        "child->'peer_pid'<>child->'controller'->'host_pid'",
        "(stopped->>'child_sha256') IS DISTINCT FROM child_hash",
        "CASE WHEN child IS NULL THEN 'null'::jsonb ELSE 'true'::jsonb END",
    ],
)
def test_semantic_links_are_explicit(fragment):
    assert fragment in module.render_sql()


def test_closed_budget_floor_state_settlement_and_nochild_failure():
    sql = module.render_sql()
    for fragment in [
        "br.purpose<>'process_unit'",
        "(br.amounts->>'process_slots')::numeric<>1",
        "(br.amounts->>'wall_milliseconds')::numeric<60000",
        "(br.amounts->>'storage_bytes')::numeric<67174400",
        "br.amounts->>'calls'<>'0'",
        "br.amounts->>'tokens'<>'0'",
        "br.amounts->>'mutation_slots'<>'0'",
        "br.amounts->>'evaluator_executions'<>'0'",
        "br.state NOT IN ('reserved','uncertain')",
        "bs.charge IS DISTINCT FROM br.amounts",
        "NOT FOUND OR stopped IS NULL OR bs.outcome<>'conservative_unknown'",
        "(stopped IS NOT NULL) IS DISTINCT FROM (ps.status='stopped')",
    ]:
        assert fragment in sql
    assert "child_hash:=NULL;" in sql
    assert "IF child IS NOT NULL THEN" in sql
    assert "IF stopped IS NOT NULL" in sql
    assert "SO_PEERCRED/proc/Docker" in module.__doc__


def test_renderer_has_no_installation_or_execution_side_effects():
    tree = ast.parse(Path(module.__file__).read_text())
    calls = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not calls & {"op.execute", "session.execute", "subprocess.run", "os.system"}
    assert "018" in module.__doc__ and "backupv5" in module.__doc__

"""Literal construction and real producer constants; not PostgreSQL execution."""

from backend.app import oe_structured_epoch_sql as epoch
from packages.openevolve_structured.provider_bootstrap import runtime_profile_sha256
from runtimes.openevolve_structured.profile import STORAGE_FLOOR, profile_sha256


def test_controller_typed_profile_and_physical_floor_come_from_real_producer():
    sql = epoch.render_upgrade_sql()
    assert "mineagent.oe-structured-bootstrap.v2" in sql
    for value in (profile_sha256(), runtime_profile_sha256(), str(STORAGE_FLOOR)):
        assert value in sql
    for token in (
        "@BOOTSTRAP_RULE@",
        "@PHYSICAL_SHA@",
        "@RUNTIME_SHA@",
        "@STORAGE_FLOOR@",
    ):
        assert token not in sql
    assert "'wall_milliseconds',600000" in sql
    assert "'cost_microunits',0" in sql
    assert "'evaluator_executions',0" in sql
    assert "interval '600 seconds'" in sql


def test_original_authenticated_body_and_raw_float_hash_not_jsonb_serialization():
    sql = epoch.render_upgrade_sql()
    assert "u.request_canonical" in sql
    assert "c->>'authenticated_request_sha256'" in sql
    assert "((s.config_canonical::json)->'model_binding')::text" in sql
    assert "mineagent_rd_canonical_json" not in sql
    assert "j->'spec' IS DISTINCT FROM s.config->'search'" in sql


def test_control_chain_and_case_expression_are_exact():
    sql = epoch.render_upgrade_sql()
    assert "c.frontier IS DISTINCT FROM (c.sequence-1)/2" in sql
    assert "c.phase IS DISTINCT FROM (CASE" in sql
    assert "ELSE 'applied' END)" in sql
    assert "p.epoch_id,p.sequence" in sql
    assert "c.epoch_id,c.sequence-1" in sql
    assert "CREATE TRIGGER" not in sql

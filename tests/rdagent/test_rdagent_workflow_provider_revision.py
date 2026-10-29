"""Pinned successor construction; these tests do not execute PostgreSQL."""

import re

import pytest

from backend.app import rdagent_workflow_provider_revision as revision
from backend.app.rdagent_workflow_sql import render_upgrade_sql as child_sql


def test_exact_owned_function_inventory_and_no_schema_or_provider_widening():
    sql = revision.render_upgrade_sql()
    assert sql == revision.render_upgrade_sql()
    assert (
        tuple(re.findall(r"CREATE OR REPLACE FUNCTION (?:@SCHEMA@\.)?(\w+)\(", sql))
        == revision.FUNCTIONS
    )
    assert "CREATE OR REPLACE FUNCTION @SCHEMA@.mineagent_rd_schema(" not in sql
    assert (
        "CREATE OR REPLACE FUNCTION @SCHEMA@.mineagent_rd_provider_config(" not in sql
    )
    assert "mineagent_rd_shared_consistent" not in sql
    assert "mineagent_rd_controller_consistent" not in sql
    assert "mineagent_rd_seed_check" not in sql
    assert "UPDATE research_runs" not in sql


@pytest.mark.parametrize("name", revision.FUNCTIONS)
def test_downgrade_restores_exact_predecessor_body(name):
    assert revision.original(name) in revision.render_downgrade_sql()
    assert revision.original(name) != revision.replacements()[name]
    assert "Workflow child history prevents" in revision.render_downgrade_sql()


def test_source_config_dispatch_preserves_old_closed_parser():
    sql = revision.replacements()[revision.FUNCTIONS[0]]
    assert (
        "IF v->>'schema_version'='mineagent.factor-research.v3' THEN RETURN mineagent_rd_workflow_config(v)"
        in sql
    )
    assert (
        "IF v->>'schema_version'='mineagent.factor-research.v2' THEN RETURN mineagent_rd_provider_config(v)"
        in sql
    )
    assert "RETURN mineagent_rd_fixture_config_026(v); END IF;" in sql
    assert "factor-research.v4" not in sql


def test_entire_source_and_boundary_bodies_differ_only_in_explicit_version_clause():
    for name, path in (
        (revision.FUNCTIONS[2], "r.config->>'schema_version'"),
        (revision.FUNCTIONS[3], "cl.bootstrap#>>'{config,schema_version}'"),
    ):
        actual = revision.replacements()[name]
        assert actual.replace(
            path
            + " IN ('mineagent.factor-research.v2','mineagent.factor-research.v3')",
            path + "='mineagent.factor-research.v2'",
        ) == revision.original(name)


def test_provider_and_failure_require_actual_workflow_receipt_not_profile_alias():
    graph = revision.replacements()[revision.FUNCTIONS[1]]
    failure = revision.replacements()[revision.FUNCTIONS[5]]
    assert "mineagent_rd_workflow_submission_check" in graph
    assert "q.run_id=rid AND q.owner_id=run.owner_id" in graph
    for fragment in (
        "spec.config->>'schema_version'='mineagent.factor-research.v3'",
        "spec.config->>'model_profile'='workflow.rdagent.v1'",
        "spec.config=r.config",
        "spec.config->>'workflow_child_run_id'=r.id::text",
        "(q.owner_id,q.run_id,q.spec_id)=(r.owner_id,r.id,spec.id)",
        "mineagent_rd_workflow_submission_check",
        "a.lease_expires_at>clock_timestamp()",
        "cr.kind='controller_child'",
    ):
        assert fragment in failure
    assert "spec.config->>'model_profile'='configured.rdagent.v1'" in failure


def test_queued_budget_floor_and_pricing_retained_for_historical_recheck():
    name = revision.FUNCTIONS[4]
    old = revision.original(name)
    new = revision.replacements()[name]
    clause = "(CASE WHEN v->>'schema_version'='mineagent.factor-research.v3' THEN mineagent_rd_workflow_config(v) ELSE mineagent_rd_provider_config(v) END)"
    assert new.replace(clause, "mineagent_rd_provider_config(v)") == old
    assert "r.status=" not in new
    assert "PERFORM mineagent_rd_provider_queued_config(r.id);" in child_sql()


@pytest.mark.parametrize("filename", tuple(revision.PINS))
def test_changed_historical_pin_rejected(monkeypatch, filename):
    monkeypatch.setitem(revision.PINS, filename, "0" * 64)
    with pytest.raises(ValueError, match="predecessor bytes"):
        revision.render_upgrade_sql()


def test_provider_dispatch_preserves_function_local_lazy_pure_cache():
    name = revision.FUNCTIONS[1]
    old = revision.original(name)
    new = revision.replacements()[name]
    dispatch = "(CASE WHEN run.config->>'schema_version'='mineagent.factor-research.v3' THEN mineagent_rd_workflow_config(run.config) ELSE mineagent_rd_provider_config(run.config) END)"
    child_check = """ IF run.config->>'schema_version'='mineagent.factor-research.v3' THEN
  PERFORM mineagent_rd_workflow_submission_check((SELECT q.id FROM factor_workflow_submissions q
   WHERE q.run_id=rid AND q.owner_id=run.owner_id));
 END IF;"""
    assert (
        new.replace(dispatch, "mineagent_rd_provider_config(run.config)").replace(
            "\n" + child_check, ""
        )
        == old
    )
    assert new.count("config_valid:=" + dispatch) == 1
    assert "config_checked boolean:=false" in new
    assert new.index("FOR op IN") < new.index("IF NOT config_checked THEN")
    assert new.index("config_valid:=") < new.index("config_checked:=true;")
    assert "IF config_valid IS NOT TRUE" in new
    assert "IS DISTINCT FROM steer_digest" in new


def test_workflow_inherits_latest_fixed_controller_not_old026():
    source = revision.original("mineagent_rd_source_config")
    assert "mineagent_rd_controller_selection(v)" in source
    assert "300000" in source
    for name in (
        "mineagent_rd_provider_graph",
        "mineagent_rd_operational_boundary_consistent",
    ):
        assert "mineagent.rdagent-orchestrator.v3" in revision.original(name)
    assert "THEN 300000 ELSE 240000 END)+32*" in revision.original(
        "mineagent_rd_provider_queued_config"
    )

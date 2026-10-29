"""Pinned delta preservation, not actual PostgreSQL graph acceptance."""

import pytest

from backend.app import rdagent_workflow_revision as m


def test_both_independent_controller_guards_add_the_same_real_workflow_bootstrap():
    for sql in (m.shared_sql(), m.controller_sql()):
        assert "mineagent.rdagent-workflow-operational-bootstrap.v1" in sql
        assert "'continuation','workflow_stage_new_process','seed',NULL" in sql
        assert "PERFORM mineagent_rd_workflow_submission_check" in sql
        assert "v.owner_id=cl.owner_id" in sql
        assert "Workflow actual parent version absent" in sql
        assert "mineagent.rdagent-provider-seeded-bootstrap.v1" in sql
        assert "mineagent.rdagent-orchestrator.v2" in sql


def test_exact_027_branches_not_replaced_by_old_026_body():
    sql = m.shared_sql()
    old = m.predecessor("mineagent_rd_shared_consistent")
    assert "mineagent.oe-structured-provider-run.v1" in old
    assert "mineagent.oe-structured-provider-run.v1" in sql


def test_seed_parent_exception_is_actual_child_receipt_not_same_owner():
    sql = m.seed_sql()
    for text in (
        "factor_workflow_submissions",
        "wst.child_run_id=st.run_id",
        "ws.owner_id=st.owner_id",
        "{workflow_parent,factor_version_id}",
        "definition_canonical::jsonb->>'parent_factor_version_id'",
    ):
        assert text in sql
    assert "factor_seeded_submissions" in sql
    assert "logical_step=0" in sql


def test_reverse_origin_counts_exactly_one_and_old_function_restores_verbatim():
    sql = m.submission_sql()
    assert sql.count("THEN 1 ELSE 0 END") == 3 and "))<>1" in sql
    down = m.render_downgrade_sql()
    for name in m.FUNCTIONS:
        assert m.predecessor(name) in down
    assert "mineagent.rdagent-workflow-operational-bootstrap.v1" not in down


@pytest.mark.parametrize("revision", tuple(m.PINS))
def test_pin_drift_fails_before_any_render(monkeypatch, revision):
    monkeypatch.setitem(m.PINS, revision, "0" * 64)
    with pytest.raises(ValueError, match="predecessor"):
        m.render_upgrade_sql()


def test_controller_predecessor_is_byte_exact_outside_new_branch():
    sql = m.controller_sql()
    extra = m.bootstrap_branch(expected="expected_bootstrap", spec="sp", admission="a")
    assert sql.count(extra) == 1
    assert sql.replace(extra, "") == m.predecessor("mineagent_rd_controller_consistent")
    sql = m.shared_sql()
    extra = m.bootstrap_branch(expected="expected", spec="spec", admission="pa")
    assert sql.count(extra) == 1
    sql = sql.replace(extra, "").replace(
        "CASE WHEN rr.config->>'schema_version' IN ('mineagent.factor-research.v2','mineagent.factor-research.v3') THEN op.protocol",
        "CASE WHEN rr.config->>'schema_version'='mineagent.factor-research.v2' THEN op.protocol",
    )
    assert sql == m.predecessor("mineagent_rd_shared_consistent")


def test_only_explicit_new_bootstrap_and_spec_grammar_added():
    assert (
        m.BOOTSTRAP_UPGRADE.replace(
            ",'mineagent.rdagent-workflow-operational-bootstrap.v1'", ""
        )
        == m.BOOTSTRAP_DOWNGRADE
    )
    assert (
        m.SPEC_UPGRADE.replace(",'mineagent.factor-research.v3'", "")
        == m.SPEC_DOWNGRADE
    )
    assert m.SPEC_UPGRADE in m.render_upgrade_sql()
    assert m.SPEC_DOWNGRADE in m.render_downgrade_sql()

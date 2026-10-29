"""Pinned source/branch construction checks; not actual PostgreSQL acceptance."""

import pytest

from backend.app import oe_structured_shared_sql as shared
from backend.app.rdagent_provider_revision import function
from packages.openevolve_structured.object_identity import KINDS


def test_only_explicit_normal_config_changes_python_search_dispatch():
    sql = shared.shared_function()
    assert sql.count(f"IS DISTINCT FROM '{shared.NORMAL}'") == 5
    assert "FROM search_iteration_intents st" in sql
    assert "FROM oe_structured_sampling_intents st" in sql
    assert "FROM factor_generation_starts st" in sql
    assert "FROM strategy_search_specs WHERE run_id=r.id" in sql
    assert "mineagent_oes_graph_check(r.id)" in sql
    assert "i.context_canonical::jsonb=cx.context" in sql
    assert "i.context_sha256=cx.context_sha256" in sql
    assert "@KINDS@" not in sql


def test_existing_neutral_model_rules_unchanged_except_declared_fullcharge_protocol():
    previous = function(shared.predecessor("026"), "mineagent_neutral_model_consistent")
    actual = shared.neutral_model_function()
    assert actual.replace(",'" + shared.PROTOCOL + "'", "") == previous
    assert (
        "THEN s.outcome IS DISTINCT FROM 'conservative_unknown' OR s.charge IS DISTINCT FROM r.amounts"
        in actual
    )


def test_check_constraints_preserve_old_kinds_and_add_only_exact_new_grammar():
    sql = shared.constraints()
    for kind in KINDS:
        assert sql.count("'" + kind + "'") == 2
    for old in (
        "rd_provider_process_receipt",
        "search_worker_outcome",
        "factor_generation_feedback",
    ):
        assert "'" + old + "'" in sql
    assert "mineagent.oe-structured-provider-chat.v1" in sql
    assert "mineagent_oes_provider_contract('request',request) IS TRUE" in sql
    assert "strategy_version_id IS NULL" in sql
    assert "resume_checkpoint_id IS NULL" in sql
    assert "mineagent_oes_contract('config',config) IS TRUE" in sql
    assert "mineagent.structured-factor" in sql or "factor_strategy" in sql


def test_changed_predecessor_is_rejected_not_best_effort(monkeypatch):
    original = shared.historical
    monkeypatch.setattr(
        shared, "historical", lambda revision, key: original(revision, key) + "\n"
    )
    with pytest.raises(ValueError, match="Exact reviewed026 predecessor"):
        shared.render_upgrade_sql()


def test_downgrade_restores_exact_previous_functions_and_constraints():
    down = shared.render_downgrade_sql()
    for name in (
        "mineagent_rd_shared_consistent",
        "mineagent_neutral_model_consistent",
    ):
        assert (
            function(
                shared.predecessor(
                    "027c" if name == "mineagent_rd_shared_consistent" else "026"
                ),
                name,
            )
            in down
        )
    assert shared.PROTOCOL not in down
    assert "CREATE TRIGGER" not in shared.render_upgrade_sql()

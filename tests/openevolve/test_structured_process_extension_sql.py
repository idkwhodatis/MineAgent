"""Exact extension construction, not PG enforcement evidence."""

from backend.app import oe_structured_process_extension_sql as extension
from backend.app.oe_structured_shared_sql import predecessor
from backend.app.openevolve_revision_ddl import _check


def test_old_profiles_are_retained_and_only_exact_new_controller_is_added():
    sql = extension.render_upgrade_sql()
    assert _check(predecessor("027c"), "ck_process_v2_unregistered") in sql
    assert extension.PROFILE in sql
    assert "role='openevolve_orchestrator'" in extension.PROFILE
    assert "ipc_policy='host_ticket'" in extension.PROFILE


def test_new_kind_spellings_require_typed_profile_and_scope_not_enum_alone():
    sql = extension.render_upgrade_sql()
    for required in (
        "NEW.owner_id,NEW.run_id,NEW.attempt_id,NEW.generation",
        "mineagent_oes_controller_payload(NEW.kind,NEW.payload) IS NOT TRUE",
        "mineagent_numeric_rejection_payload(NEW.kind,NEW.payload) IS NOT TRUE",
        "'mineagent.factor-prefix-candidate.v2','factor_cutoff_candidate','none'",
        "r.config->>'schema_version' IS DISTINCT FROM 'mineagent.oe-structured-provider-run.v1'",
    ):
        assert required in sql
    assert (
        "IF NEW.kind NOT IN ('controller_completion','rejection') THEN RETURN NEW"
        in sql
    )


def test_downgrade_drops_extension_and_restores_original_closed_kind_set():
    sql = extension.render_downgrade_sql()
    assert "DROP TRIGGER oes027_receipt_extension" in sql
    check = sql.split("ADD CONSTRAINT ck_research_process_receipt_kind CHECK ", 1)[
        1
    ].split(";", 1)[0]
    assert "controller_completion" not in check
    assert "rejection" not in check
    assert "extraction" in check

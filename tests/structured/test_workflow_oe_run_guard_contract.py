"""Keep prior installed CHECK arms and the reviewed v1 SQL unchanged."""

from hashlib import sha256
from importlib import import_module

from backend.app.oe_structured_shared_sql import RUN_BRANCH
from backend.app.openevolve_revision_ddl import _check, historical


def test_workflow_oe_run_guard_only_adds_typed_exact_v2_arm():
    migration = import_module("backend.alembic.versions.032h_workflow_oe_run_guard")
    predecessor = historical("028", "UPGRADE_SQL")
    assert migration.PREDECESSOR_SQL_SHA256 == sha256(predecessor.encode()).hexdigest()
    assert migration.ORIGINAL_CHECK == _check(
        predecessor, "ck_research_run_legacy_config"
    )
    assert migration.ORIGINAL_CHECK.count(RUN_BRANCH) == 1
    assert migration.WORKFLOW_CHECK == (
        f"(({migration.ORIGINAL_CHECK}) OR {migration.WORKFLOW_V2_BRANCH}) IS TRUE"
    )
    narrow = (
        "config->>'schema_version'='mineagent.oe-structured-provider-run.v2'\n"
        " AND owner_id::text=config#>>'{search,owner_id}'\n"
        " AND id::text=config#>>'{search,run_id}'\n"
        " AND owner_id::text=config#>>'{workflow_context,inputs,binding,owner_id}'\n"
        " AND id::text=config#>>'{workflow_context,child_run_id}'"
    )
    assert (
        migration.WORKFLOW_V2_BRANCH.replace(
            narrow,
            "config->>'schema_version'='mineagent.oe-structured-provider-run.v1'",
        )
        == RUN_BRANCH
    )

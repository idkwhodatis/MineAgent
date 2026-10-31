"""Successor DDL for explicit normalization.v2/report inventory support.

Revision019 and its literal v1 predicate remain unchanged. The owning successor
migration must freeze this rendered replacement; importing this module installs
nothing and grants no execution authority.
"""

from backend.app.native_storage_ddl import NORMALIZER_SQL, qualified_sql


def normalizer_report_sql():
    original = NORMALIZER_SQL.split("\nCREATE TRIGGER", 1)[0]
    start = original.index(" files:=jsonb_build_array(")
    end = original.index("\n IF (a.schema_version", start)
    files = original[start:end]
    # Try exactly the historical v1 inventory first. Only if it differs, build
    # exactly v2 and run the unchanged full equality/context predicate below.
    # Unknown versions, arbitrary hashes and partial inventory never pass.
    extension = (
        """
 IF li.payload->'inputs'->'files' IS DISTINCT FROM files THEN
  manifest:=jsonb_set(manifest,'{schema_version}','"mineagent.factor-normalization.v2"'::jsonb);
"""
        + files
        + "\n END IF;"
    )
    changed = original[:end] + extension + original[end:]
    changed = changed.replace(
        "CREATE FUNCTION mineagent_native_normalizer_consistent()",
        "CREATE OR REPLACE FUNCTION mineagent_native_normalizer_consistent()",
        1,
    )
    return qualified_sql(changed)


def require_report_capability(session):
    from sqlalchemy import text

    from backend.app.schema_capabilities import capabilities_for
    from packages.research_runtime.process_contracts import ProcessError

    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    if len(revisions) != 1:
        raise ProcessError("numeric_report_schema_unsupported")
    # The installed revision owns its exact triple. A valid successor triple
    # must not grant report access to an older database's inventory.
    versions_by_revision = {
        "021_execution_snapshots": (1, 2, 6),
        "022_factor_analysis": (2, 3, 7),
        "023_rd_submissions": (2, 3, 7),
        "024_openevolve_initial": (2, 3, 8),
        "025_structured_factor_strategy": (2, 3, 9),
        "026_rd_seeded_submissions": (2, 3, 10),
        "027_rd_controller_v3": (2, 3, 11),
        "027a_contract_repairs": (2, 3, 11),
        "027b_provider_material": (2, 3, 11),
        "027c_seeded_controller_v4": (2, 3, 12),
        "028_oe_structured_search": (2, 3, 13),
        "029_research_workflow": (2, 3, 14),
        "030_declared_upload_basket": (2, 3, 15),
        "031_workflow_structured_stage": (2, 3, 16),
        "032_normal_oe_note_pins": (2, 3, 16),
        "032a_rd_terminal_results": (2, 3, 16),
        "032b_oe_append_run_state": (2, 3, 16),
    }
    versions = versions_by_revision.get(revisions[0])
    if versions is None:
        raise ProcessError("numeric_report_schema_unsupported")
    capability = capabilities_for(revisions[0])
    expected = (
        f"mineagent.execution-snapshot-restore-closure.v{versions[0]}",
        f"mineagent.native-hdf-restore-closure.v{versions[1]}",
        f"mineagent.research-restore-closure.v{versions[2]}",
    )
    if (
        getattr(capability, "execution_snapshot_schema", None),
        capability.native_closure_schema,
        capability.research_closure_schema,
    ) != expected:
        raise ProcessError("numeric_report_schema_unsupported")

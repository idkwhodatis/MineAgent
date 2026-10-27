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
    capability = capabilities_for(revisions[0])
    if (
        getattr(capability, "execution_snapshot_schema", None),
        capability.native_closure_schema,
        capability.research_closure_schema,
    ) not in {
        (
            "mineagent.execution-snapshot-restore-closure.v1",
            "mineagent.native-hdf-restore-closure.v2",
            "mineagent.research-restore-closure.v6",
        ),
        (
            "mineagent.execution-snapshot-restore-closure.v2",
            "mineagent.native-hdf-restore-closure.v3",
            "mineagent.research-restore-closure.v7",
        ),
    }:
        raise ProcessError("numeric_report_schema_unsupported")

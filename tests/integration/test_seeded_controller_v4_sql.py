"""Real PostgreSQL v4 binding predicate; isolated rows, not full graph proof."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.app.rdagent_controller_v4_revision import DOWN_GUARD, SEED_SQL

pytestmark = pytest.mark.postgres

TABLES = """
CREATE TEMP TABLE research_runs(id uuid, owner_id uuid, engine text, purpose text, config jsonb);
CREATE TEMP TABLE research_process_admissions(profile text);
CREATE TEMP TABLE factor_seeded_submissions(run_id uuid,owner_id uuid,parent_factor_version_id uuid,parent_code_object_id uuid,parent_factor_id uuid,parent_ordinal integer,parent_code_sha256 text,parent_definition_sha256 text);
CREATE TEMP TABLE factor_versions(id uuid,owner_id uuid,factor_id uuid,ordinal integer,code_object_id uuid,code_sha256 text,definition_sha256 text);
CREATE TEMP TABLE factor_objects(id uuid,owner_id uuid,sha256 text);
CREATE TEMP TABLE factor_research_submissions(run_id uuid);
"""
BASE = """
INSERT INTO pg_temp.research_runs VALUES ('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000002','rdagent','factor_research','{"schema_version":"mineagent.factor-research.v2","controller_profile":"mineagent.rdagent-orchestrator.v4"}');
INSERT INTO pg_temp.factor_seeded_submissions VALUES ('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000003','00000000-0000-0000-0000-000000000004','00000000-0000-0000-0000-000000000005',1,'code','definition');
INSERT INTO pg_temp.factor_versions SELECT parent_factor_version_id,owner_id,parent_factor_id,parent_ordinal,parent_code_object_id,parent_code_sha256,parent_definition_sha256 FROM pg_temp.factor_seeded_submissions;
INSERT INTO pg_temp.factor_objects SELECT parent_code_object_id,owner_id,parent_code_sha256 FROM pg_temp.factor_seeded_submissions;
"""
CALL = "SELECT pg_temp.mineagent_rd_v4_seed_binding('00000000-0000-0000-0000-000000000001')"


def test_seeded_v4_binding_rejects_wrong_parent_owner_and_fresh(migrated_engine):
    # Execute the exact production predicate in an isolated schema. The complete
    # app constraints and runtime/settlement chain are tested by hosted flow gates.
    function = SEED_SQL.split(
        "CREATE FUNCTION @SCHEMA@.mineagent_rd_v4_seed_deferred", 1
    )[0].replace("@SCHEMA@", "pg_temp")
    with migrated_engine.connect() as c:
        c.exec_driver_sql(TABLES)
        c.exec_driver_sql(function, execution_options={"no_parameters": True})
        c.exec_driver_sql(BASE)
        c.execute(text(CALL))
        mutations = [
            "DELETE FROM pg_temp.factor_seeded_submissions",
            "UPDATE pg_temp.factor_seeded_submissions SET owner_id='00000000-0000-0000-0000-000000000099'",
            "UPDATE pg_temp.factor_versions SET owner_id='00000000-0000-0000-0000-000000000099'",
            "UPDATE pg_temp.factor_objects SET owner_id='00000000-0000-0000-0000-000000000099'",
            "UPDATE pg_temp.factor_seeded_submissions SET parent_factor_version_id='00000000-0000-0000-0000-000000000099'",
            "UPDATE pg_temp.factor_seeded_submissions SET parent_code_sha256='wrong'",
            "UPDATE pg_temp.factor_seeded_submissions SET parent_definition_sha256='wrong'",
            "UPDATE pg_temp.factor_seeded_submissions SET parent_ordinal=2",
            "UPDATE pg_temp.research_runs SET config=jsonb_set(config,'{schema_version}','\"mineagent.factor-research.v1\"')",
            "INSERT INTO pg_temp.factor_research_submissions SELECT id FROM pg_temp.research_runs",
        ]
        for mutation in mutations:
            nested = c.begin_nested()
            c.exec_driver_sql(mutation)
            with pytest.raises(DBAPIError, match="exact provider seeded parent"):
                c.execute(text(CALL))
            nested.rollback()
        for retained in ("run", "admission"):
            nested = c.begin_nested()
            if retained == "admission":
                c.exec_driver_sql(
                    "DELETE FROM pg_temp.research_runs; INSERT INTO pg_temp.research_process_admissions VALUES ('mineagent.rdagent-orchestrator.v4')"
                )
            with pytest.raises(DBAPIError, match="forbids downgrade"):
                c.exec_driver_sql(DOWN_GUARD.replace("@SCHEMA@", "pg_temp"))
            nested.rollback()
        c.exec_driver_sql("UPDATE pg_temp.research_runs SET config='{}'")
        c.exec_driver_sql(DOWN_GUARD.replace("@SCHEMA@", "pg_temp"))


def test_v4_provider_only_profile_and_exact_wall_floor(migrated_engine):
    import json

    from tests.integration.test_rd_controller_v3_predicates import configured

    with migrated_engine.connect() as c:
        for provider in (False, True):
            for selection in (
                "missing",
                "mineagent.rdagent-orchestrator.v3",
                "mineagent.rdagent-orchestrator.v4",
                None,
                False,
                180,
                "unknown",
            ):
                for wall in (299999, 300000, 555999, 556000, 615999, 616000, 616001):
                    value = configured(provider)
                    value["budget"]["wall_milliseconds"] = wall
                    if selection != "missing":
                        value["controller_profile"] = selection
                    floor = (
                        616000
                        if selection == "mineagent.rdagent-orchestrator.v4"
                        else 556000
                        if provider
                        else 300000
                    )
                    expected = (
                        selection == "missing"
                        or (
                            selection == "mineagent.rdagent-orchestrator.v3"
                            and wall >= floor
                        )
                        or (
                            provider
                            and selection == "mineagent.rdagent-orchestrator.v4"
                            and wall >= floor
                        )
                    )
                    assert (
                        c.scalar(
                            text(
                                "SELECT mineagent_rd_source_config(CAST(:v AS jsonb))"
                            ),
                            {"v": json.dumps(value)},
                        )
                        is expected
                    )

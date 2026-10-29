"""028 literal assembly boundaries; actual PostgreSQL is separately required."""

import re

from backend.app import research_workflow_revision_ddl as m


def test_all_twelve_tables_precede_semantic_functions_and_deferred_foreign_keys():
    sql = m.render_upgrade_sql()
    names = re.findall(r"CREATE TABLE @SCHEMA@\.(\w+)", sql)
    assert set(names) == set(m.research_workflow_persistence_ddl.TABLES)
    assert len(names) == 12
    assert sql.index("CREATE TABLE") < sql.index(
        "CREATE FUNCTION @SCHEMA@.mineagent_workflow_payload"
    )
    assert sql.index("mineagent_rd_workflow_config(v jsonb)") < sql.rindex(
        "mineagent_rd_provider_graph(rid uuid)"
    )
    assert "%%" not in sql
    assert m.ASSEMBLY_SCHEMA not in sql


def test_source_creation_and_triggers_use_exact_schema_not_ambient_search_path():
    sql = m.input_sql()
    assert len(re.findall(r"CREATE FUNCTION @SCHEMA@\.mineagent_input_", sql)) == 3
    assert sql.count("EXECUTE FUNCTION @SCHEMA@.mineagent_input_") == 10
    assert sql.count("SET search_path TO @SCHEMA@, pg_catalog") == 3
    for table in m.research_input_ddl.TABLES:
        assert sql.count(f" ON @SCHEMA@.{table} ") == 2


def test_downgrade_blocks_standalone_input_and_restores_predicates_before_dropping_tables():
    sql = m.render_downgrade_sql()
    first = sql.split("END $$;", 1)[0]
    for table in m.research_workflow_persistence_ddl.TABLES:
        assert f"EXISTS(SELECT 1 FROM @SCHEMA@.{table})" in first
    assert "mineagent.factor-research.v3" in first
    assert sql.index("CREATE OR REPLACE FUNCTION") < sql.index("DROP TABLE")
    assert sql.index("DROP FUNCTION @SCHEMA@.mineagent_input_before()") < sql.index(
        "DROP FUNCTION @SCHEMA@.mineagent_workflow_lock"
    )
    assert len(re.findall(r"DROP TABLE @SCHEMA@\.", sql)) == 12


def test_table_polymorphic_root_trigger_never_compiles_a_missing_record_field():
    sql = m.research_workflow_sql.render_upgrade_sql()
    assert "CASE WHEN TG_TABLE_NAME" not in sql
    assert (
        "IF TG_TABLE_NAME='research_workflows' THEN\n  w:=NEW.id;\n ELSE\n  w:=NEW.workflow_id;\n END IF;"
        in sql
    )


def test_new_graph_sql_query_aliases_do_not_shadow_declared_records():
    for sql in (
        m.research_workflow_sql.render_upgrade_sql(),
        m.input_sql(),
        m.rdagent_workflow_sql.render_upgrade_sql(),
    ):
        for body in re.split(r"CREATE (?:OR REPLACE )?FUNCTION ", sql)[1:]:
            declaration = body.split("BEGIN", 1)[0].split("DECLARE", 1)[-1]
            records = set(
                re.findall(r"(?:^|;)\s*(\w+)\s+(?:record|\w+%ROWTYPE)", declaration)
            )
            for line in body.splitlines():
                aliases = set(
                    re.findall(r"(?:FROM|JOIN)\s+[\w.]+\s+(\w+)\b", line, re.IGNORECASE)
                )
                assert not records & aliases, (body.split("(")[0], records & aliases)


def test_only_plan_and_creation_preserve_actual_finite_objective_float_tokens():
    sql = m.research_workflow_sql.render_upgrade_sql()
    raw = sql.split("mineagent_workflow_raw(raw text", 1)[1].split("END $$;", 1)[0]
    assert "IF kind IN ('plan','create') THEN" in raw
    assert "mineagent_analysis_raw_commitment(raw,raw::jsonb,sha,1048576)" in raw
    assert "mineagent_rd_canonical_json(raw::jsonb,true)=raw" in raw

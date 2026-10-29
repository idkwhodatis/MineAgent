"""Bounded OLD+NEW owner reverse closure for newly linked workflow evidence."""

from backend.app.oe_structured_trigger_sql import SHARED, TABLES

TABLES_REVERSE = tuple(
    sorted(
        set(
            SHARED
            + TABLES
            + (
                "research_workflows",
                "research_workflow_stages",
                "research_workflow_revisions",
                "research_workflow_checkpoints",
                "research_workflow_commands",
                "research_workflow_model_consumption",
                "research_input_versions",
                "research_input_records",
                "research_input_derivations",
                "research_input_derivation_events",
                "research_input_objects",
                "rd_steer_versions",
                "rd_steers",
                "factor_workflow_submissions",
                "factor_direct_analysis_intents",
                "factor_direct_analysis_outcomes",
            )
        )
    )
)


def render_upgrade_sql():
    return SQL + "\n".join(
        f"CREATE CONSTRAINT TRIGGER oe031_reverse AFTER INSERT OR UPDATE OR DELETE ON @SCHEMA@.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_oe_workflow_reverse();"
        for table in TABLES_REVERSE
    )


def render_downgrade_sql():
    return (
        "\n".join(
            f"DROP TRIGGER oe031_reverse ON @SCHEMA@.{table};"
            for table in TABLES_REVERSE
        )
        + "\nDROP FUNCTION @SCHEMA@.mineagent_oe_workflow_reverse();\n"
    )


SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_oe_workflow_reverse() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE versions jsonb[]; v jsonb; root record; count_n integer; affected_owner uuid;
BEGIN
 versions:=CASE WHEN TG_OP='INSERT' THEN ARRAY[to_jsonb(NEW)] WHEN TG_OP='DELETE' THEN ARRAY[to_jsonb(OLD)] ELSE ARRAY[to_jsonb(OLD),to_jsonb(NEW)] END;
 FOREACH v IN ARRAY versions LOOP
  affected_owner:=(v->>'owner_id')::uuid;
  IF affected_owner IS NULL AND v ? 'run_id' THEN SELECT owner_id INTO affected_owner FROM research_runs WHERE id=(v->>'run_id')::uuid; END IF;
  count_n:=0;
  FOR root IN SELECT owner_id,id FROM research_workflows WHERE owner_id=affected_owner AND plan_canonical::jsonb->>'schema_version'='mineagent.rd-structured-guidance-workflow-plan.v1' ORDER BY id LIMIT 10001 LOOP
   count_n:=count_n+1;
   IF count_n>10000 THEN RAISE EXCEPTION 'Workflow OE reverse bound'; END IF;
   PERFORM mineagent_workflow_lock(root.owner_id,root.id);
   IF mineagent_workflow_consistent(root.owner_id,root.id) IS NOT TRUE THEN RAISE EXCEPTION 'Workflow OE reverse immutable graph differs'; END IF;
  END LOOP;
 END LOOP;
 RETURN NULL;
END $$;
"""

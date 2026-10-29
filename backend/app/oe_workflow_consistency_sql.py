"""031 finite schedule extension over exact029 control/closure guards."""

from backend.app.oe_workflow_predecessors import predecessor
from backend.app.rdagent_provider_revision import changed


def render_upgrade_sql():
    sql = predecessor("mineagent_workflow_consistent")
    sql = changed(
        sql,
        "expected_kind text; k text;",
        "expected_kind text; k text; structured_tail integer:=0; tail_kind text:='rd_factor_generation';",
    )
    sql = changed(
        sql,
        " iterations:=(p->>'maximum_iterations')::integer;",
        " IF p->>'schema_version'='mineagent.rd-structured-guidance-workflow-plan.v1' THEN structured_tail:=1; tail_kind:='structured_factor_execution'; END IF;\n iterations:=(p->>'maximum_iterations')::integer;",
    )
    sql = changed(
        sql,
        "  minimum:=(p->'research_template'->'budget'->>k)::numeric*iterations;",
        "  minimum:=(p->'research_template'->'budget'->>k)::numeric*iterations;\n  IF structured_tail=1 THEN minimum:=minimum+(p#>>ARRAY['structured_template','budget',k])::numeric; END IF;",
    )
    # Only two schedule cardinality guards; never change RD iteration arithmetic.
    for old, new in (
        (
            "IF n>2*iterations-1+offset_n OR",
            "IF n>2*iterations-1+offset_n+structured_tail OR",
        ),
        (
            "expected_kind:=CASE WHEN offset_n=1 AND n=1",
            "expected_kind:=CASE WHEN structured_tail=1 AND n=2*iterations+offset_n THEN 'structured_factor_execution' WHEN offset_n=1 AND n=1",
        ),
        (
            "n<>2*iterations-1+offset_n OR",
            "n<>2*iterations-1+offset_n+structured_tail OR",
        ),
        (
            "id=root.current_stage_id AND kind='rd_factor_generation'",
            "id=root.current_stage_id AND kind=tail_kind",
        ),
    ):
        sql = changed(sql, old, new)
    marker = "  ELSE\n   IF mineagent_workflow_raw(s.definition_canonical,s.definition_sha256,'rd_stage')"
    branch = """  ELSIF s.kind='structured_factor_execution' THEN
   IF structured_tail<>1 OR s.ordinal<>2*iterations+offset_n
    OR mineagent_workflow_raw(s.definition_canonical,s.definition_sha256,'oe_stage') IS NOT TRUE
    OR s.allocation_canonical::jsonb IS DISTINCT FROM p#>'{structured_template,budget}' THEN RETURN false; END IF;
   SELECT * INTO m FROM oe_workflow_submissions WHERE owner_id=o AND workflow_id=w AND stage_id=s.id;
   IF NOT FOUND THEN RETURN false; END IF;
   PERFORM mineagent_oe_workflow_attachment(m.id);
   IF (SELECT count(*) FROM research_workflow_commands c WHERE c.owner_id=o AND c.workflow_id=w
    AND c.command_canonical::jsonb->>'schema_version'='mineagent.workflow-oe-claim.v1'
    AND c.result_canonical::jsonb->>'stage_id'=s.id::text
    AND c.result_canonical::jsonb->>'child_run_id'=s.child_run_id::text)<>1 THEN RETURN false; END IF;
  ELSE
   IF mineagent_workflow_raw(s.definition_canonical,s.definition_sha256,'rd_stage')"""
    sql = changed(sql, marker, branch)
    sql = changed(
        sql,
        "WHEN 'mineagent.workflow-rd-claim.v1' THEN 'claim' END",
        "WHEN 'mineagent.workflow-rd-claim.v1' THEN 'claim' WHEN 'mineagent.workflow-oe-claim.v1' THEN 'oe_claim' END",
    )
    sql = changed(
        sql,
        "CASE WHEN expected_kind='claim' THEN 'claim_receipt' ELSE 'command_receipt' END",
        "CASE WHEN expected_kind='claim' THEN 'claim_receipt' WHEN expected_kind='oe_claim' THEN 'oe_claim_receipt' ELSE 'command_receipt' END",
    )
    command_end = " END LOOP;\n IF EXISTS(SELECT 1 FROM research_workflow_stages sql_s JOIN research_model_operations op"
    sql = changed(
        sql,
        command_end,
        """  IF expected_kind='oe_claim' THEN
   IF structured_tail<>1 OR NOT EXISTS(
    SELECT 1 FROM research_workflow_stages claim_stage
    JOIN oe_workflow_submissions attachment ON attachment.owner_id=claim_stage.owner_id
     AND attachment.workflow_id=claim_stage.workflow_id AND attachment.stage_id=claim_stage.id
    WHERE claim_stage.owner_id=o AND claim_stage.workflow_id=w
     AND claim_stage.kind='structured_factor_execution'
     AND claim_stage.ordinal=2*iterations+offset_n
     AND claim_stage.id::text=result->>'stage_id'
     AND claim_stage.child_run_id::text=result->>'child_run_id'
     AND attachment.run_id=claim_stage.child_run_id
     AND attachment.submission_id::text=result->>'submission_id'
     AND attachment.input_revision_id=claim_stage.input_revision_id
     AND claim_stage.input_revision_id::text=v->>'input_revision_id'
   ) THEN RETURN false; END IF;
  END IF;
"""
        + command_end,
    )
    marker = " FOR m IN SELECT * FROM research_workflow_model_consumption WHERE owner_id=o AND workflow_id=w LOOP\n"
    sql = changed(
        sql,
        marker,
        """ IF EXISTS(SELECT 1 FROM research_workflow_stages sql_stage JOIN oe_structured_provider_transports t
   ON t.owner_id=sql_stage.owner_id AND t.run_id=sql_stage.child_run_id
   WHERE sql_stage.owner_id=o AND sql_stage.workflow_id=w AND sql_stage.kind='structured_factor_execution'
   AND NOT EXISTS(SELECT 1 FROM research_workflow_model_consumption c
    WHERE c.owner_id=o AND c.workflow_id=w AND c.stage_id=sql_stage.id AND c.operation_id=t.operation_id AND c.transport_id=t.transport_id))
 THEN RETURN false; END IF;
"""
        + marker
        + """  IF EXISTS(SELECT 1 FROM research_workflow_stages sql_stage WHERE sql_stage.owner_id=o AND sql_stage.workflow_id=w AND sql_stage.id=m.stage_id AND sql_stage.kind='structured_factor_execution') THEN
   IF mineagent_oe_workflow_consumption(m.id) IS NOT TRUE THEN RETURN false; END IF;
   CONTINUE;
  END IF;
""",
    )
    return sql

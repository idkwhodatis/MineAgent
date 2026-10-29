"""Actual final OE provider request binding, not selected-equals-consumed metadata."""

from backend.app.oe_workflow_predecessors import predecessor
from backend.app.rdagent_provider_revision import changed
from packages.openevolve_structured.runtime_profile import PROFILE
from packages.openevolve_structured.workflow_prompt import BEGIN, END, SYSTEM


def render_upgrade_sql():
    sql = SQL
    for key, value in (("@BEGIN@", BEGIN), ("@END@", END), ("@SYSTEM@", SYSTEM)):
        sql = sql.replace(
            key,
            value.replace("\\", "\\\\")
            .replace("'", "''")
            .replace("\n", "\\n")
            .replace("\r", "\\r"),
        )
    sql = sql.replace("@MAX_CHARS@", str(PROFILE.maximum_message_characters)).replace(
        "@MAX_BYTES@", str(PROFILE.maximum_request_bytes)
    )
    original = predecessor("mineagent_oes_provider_check")
    extra = """ IF config->>'schema_version'='mineagent.oe-structured-provider-run.v2' THEN
  IF @SCHEMA@.mineagent_oe_workflow_rendered(config->'workflow_context',i.rendered_canonical::jsonb) IS NOT TRUE
  THEN RAISE EXCEPTION 'OE actual final workflow prompt differs'; END IF;
 END IF;
END $$;"""
    return sql + changed(original, "END $$;", extra)


SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_oe_workflow_rendered(ctx jsonb,rendered jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE raw text; block text; prompt text; prefix text; begin_marker text:='<MINEAGENT_WORKFLOW_EVIDENCE_V1>'; end_marker text:='</MINEAGENT_WORKFLOW_EVIDENCE_V1>';
BEGIN
 IF @SCHEMA@.mineagent_oe_workflow_shape('context',ctx) IS NOT TRUE OR jsonb_typeof(rendered) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(rendered))<>2
  OR jsonb_typeof(rendered->'system') IS DISTINCT FROM 'string' OR jsonb_typeof(rendered->'user') IS DISTINCT FROM 'string'
 THEN RETURN false; END IF;
 raw:=@SCHEMA@.mineagent_rd_canonical_json(ctx,true); block:=E'@BEGIN@'||raw||E'@END@'; prompt:=rendered->>'user';
 IF strpos(raw,'MINEAGENT_WORKFLOW_EVIDENCE_V1')<>0 OR rendered->>'system' IS DISTINCT FROM E'@SYSTEM@'
  OR right(prompt,length(block)) IS DISTINCT FROM block OR length(prompt)<=length(block)
  OR (length(prompt)-length(replace(prompt,begin_marker,'')))/length(begin_marker)<>1
  OR (length(prompt)-length(replace(prompt,end_marker,'')))/length(end_marker)<>1
  OR length(prompt)>@MAX_CHARS@ OR length(rendered->>'system')>@MAX_CHARS@
  OR octet_length(@SCHEMA@.mineagent_rd_canonical_json(rendered,true))>@MAX_BYTES@
 THEN RETURN false; END IF;
 prefix:=left(prompt,length(prompt)-length(block));
 IF strpos(prefix,raw)<>0 THEN RETURN false; END IF;
 RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_oe_workflow_consumption(cid uuid) RETURNS boolean
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE m record; st record; q record; sp record; op record; transport record; member record; iteration record;
BEGIN
 SELECT * INTO STRICT m FROM research_workflow_model_consumption WHERE id=cid;
 SELECT * INTO STRICT st FROM research_workflow_stages WHERE owner_id=m.owner_id AND workflow_id=m.workflow_id AND id=m.stage_id AND kind='structured_factor_execution';
 SELECT * INTO STRICT q FROM oe_workflow_submissions WHERE owner_id=m.owner_id AND workflow_id=m.workflow_id AND stage_id=m.stage_id;
 SELECT * INTO STRICT sp FROM oe_structured_specs WHERE owner_id=m.owner_id AND run_id=m.run_id;
 SELECT * INTO STRICT op FROM research_model_operations WHERE id=m.operation_id;
 SELECT * INTO STRICT transport FROM research_model_transports WHERE id=m.transport_id;
 SELECT * INTO STRICT member FROM oe_structured_provider_transports WHERE operation_id=op.id AND transport_id=transport.id;
 SELECT * INTO STRICT iteration FROM oe_structured_iteration_intents WHERE id=member.iteration_intent_id;
 IF (st.child_run_id,st.input_revision_id,q.run_id,q.input_revision_id,q.resolved_inputs_sha256)
  IS DISTINCT FROM (m.run_id,m.input_revision_id,m.run_id,m.input_revision_id,m.input_sha256)
 OR m.input_sha256 IS DISTINCT FROM mineagent_execution_metadata_sha(sp.config#>'{workflow_context,inputs}')
 OR (op.owner_id,op.run_id,op.attempt_id,op.generation,op.request_sha256)
  IS DISTINCT FROM (m.owner_id,m.run_id,m.attempt_id,m.generation,m.request_sha256)
 OR (transport.owner_id,transport.run_id,transport.attempt_id,transport.generation,transport.operation_id,transport.ordinal)
  IS DISTINCT FROM (m.owner_id,m.run_id,m.attempt_id,m.generation,m.operation_id,1)
 OR (member.owner_id,member.run_id,member.attempt_id,member.generation,member.request_sha256)
  IS DISTINCT FROM (m.owner_id,m.run_id,m.attempt_id,m.generation,m.request_sha256)
 OR mineagent_oe_workflow_rendered(sp.config->'workflow_context',iteration.rendered_canonical::jsonb) IS NOT TRUE
 THEN RETURN false; END IF;
 PERFORM mineagent_oes_provider_check(member.id);
 RETURN true;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
"""

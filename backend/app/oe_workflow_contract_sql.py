"""031 closed versioned shapes, retaining every030 legacy predicate unchanged."""

import json

from backend.app.analysis_process_ddl import _require_serialized_keys
from backend.app.oe_workflow_predecessors import renamed
from backend.app.oe_workflow_submission_schemas import WorkflowStructuredSearchRequest
from backend.app.research_workflow_structured_schemas import CreateStructuredWorkflow
from backend.app.structured_schema_sql import structured_schema
from packages.openevolve_structured.workflow_bootstrap import (
    WorkflowStructuredBootstrap,
)
from packages.openevolve_structured.workflow_run import (
    WorkflowOEContext,
    WorkflowStructuredRunConfig,
)
from packages.research_workflow.oe_checkpoint import OEStageCheckpoint
from packages.research_workflow.oe_stage import (
    OEStageDefinition,
    WorkflowOEClaim,
    WorkflowOEClaimReceipt,
)
from packages.research_workflow.structured_plan import RDStructuredGuidancePlan

MODELS = {
    "plan": RDStructuredGuidancePlan,
    "create": CreateStructuredWorkflow,
    "oe_stage": OEStageDefinition,
    "oe_checkpoint": OEStageCheckpoint,
    "oe_claim": WorkflowOEClaim,
    "oe_claim_receipt": WorkflowOEClaimReceipt,
    "context": WorkflowOEContext,
    "config": WorkflowStructuredRunConfig,
    "request": WorkflowStructuredSearchRequest,
    "bootstrap": WorkflowStructuredBootstrap,
}


def render_upgrade_sql():
    rules = []
    for kind, model in MODELS.items():
        schema = structured_schema(model)
        _require_serialized_keys(schema)
        # Missing selector is serialized as absence, never explicit null. The
        # immutable029 grammar accidentally required nullable defaults;031 new
        # versions use the actual canonical model without rewriting old bytes.
        for node in schema.get("$defs", {}).values():
            properties = node.get("properties", {})
            if "controller_profile" in properties:
                clause = properties["controller_profile"]
                if clause.get("anyOf") != [
                    {"const": "mineagent.rdagent-orchestrator.v3", "type": "string"},
                    {"type": "null"},
                ]:
                    raise ValueError(
                        "Exact reviewed optional controller selector required"
                    )
                del properties["controller_profile"]
                node["required"].remove("controller_profile")
        raw = json.dumps(schema, sort_keys=True, separators=(",", ":")).replace(
            "'", "''"
        )
        rules.append(f"WHEN '{kind}' THEN rule:='{raw}'::jsonb;")
    return (
        renamed("mineagent_workflow_payload", "mineagent_workflow_payload_before_oe")
        + renamed("mineagent_oes_contract", "mineagent_oes_contract_before_workflow")
        + SQL.replace("@RULES@", "\n".join(rules))
    )


SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_oe_workflow_shape(kind text,v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE rule jsonb; cfg jsonb; path text[];
BEGIN
 IF kind IN ('plan','create') THEN
  path:=CASE WHEN kind='plan' THEN ARRAY['research_template'] ELSE ARRAY['plan','research_template'] END;
  cfg:=v#>path;
  IF jsonb_typeof(cfg) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
  IF cfg ? 'controller_profile' AND cfg->'controller_profile' IS DISTINCT FROM '"mineagent.rdagent-orchestrator.v3"'::jsonb THEN RETURN false; END IF;
  v:=jsonb_set(v,path,cfg-'controller_profile');
 END IF;
 CASE kind @RULES@ ELSE RETURN false; END CASE;
 RETURN @SCHEMA@.mineagent_rd_schema(v,rule,coalesce(rule->'$defs','{}'::jsonb),0) IS TRUE;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
CREATE OR REPLACE FUNCTION @SCHEMA@.mineagent_workflow_payload(kind text,v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE p jsonb; base jsonb; k text; minimum numeric;
BEGIN
 IF kind IN ('oe_stage','oe_checkpoint','oe_claim','oe_claim_receipt') THEN
  RETURN @SCHEMA@.mineagent_oe_workflow_shape(kind,v);
 END IF;
 IF kind='plan' AND v->>'schema_version'='mineagent.rd-structured-guidance-workflow-plan.v1' THEN
  IF @SCHEMA@.mineagent_oe_workflow_shape('plan',v) IS NOT TRUE THEN RETURN false; END IF;
  base:=(v-'structured_template')||jsonb_build_object('schema_version','mineagent.rd-guidance-workflow-plan.v1','profile','rd_guidance_finite_loop.v1');
  IF @SCHEMA@.mineagent_workflow_payload_before_oe('plan',base) IS NOT TRUE THEN RETURN false; END IF;
  p:=v->'structured_template';
  IF p#>>'{model_binding,pricing,status}' IS DISTINCT FROM 'operator_declared'
   OR (v#>>'{research_template,input,window,end}')::date-(v#>>'{research_template,input,window,start}')::date<>1
   OR (p#>>'{budget,process_slots}')::numeric < 2+2*(p->>'maximum_mutation_slots')::integer
   OR (p#>>'{objective,window,end}')::date-(p#>>'{objective,window,start}')::date>8
   OR NOT ((p#>>'{objective,window,start}')::date <= (v#>>'{research_template,input,window,start}')::date
      AND (v#>>'{research_template,input,window,start}')::date < (v#>>'{research_template,input,window,end}')::date
      AND (v#>>'{research_template,input,window,end}')::date < (p#>>'{objective,window,end}')::date)
  THEN RETURN false; END IF;
  FOR k IN SELECT jsonb_object_keys(v->'budget_ceiling') LOOP
   minimum:=(v#>>ARRAY['research_template','budget',k])::numeric*(v->>'maximum_iterations')::integer+(p#>>ARRAY['budget',k])::numeric;
   IF k='storage_bytes' THEN minimum:=minimum+65536*((v->>'maximum_iterations')::integer-1); END IF;
   IF (v->'budget_ceiling'->>k)::numeric<minimum THEN RETURN false; END IF;
  END LOOP;
  RETURN true;
 END IF;
 IF kind='create' AND v->>'schema_version'='mineagent.workflow-create.v2' THEN
  IF @SCHEMA@.mineagent_oe_workflow_shape('create',v) IS NOT TRUE
   OR @SCHEMA@.mineagent_workflow_payload('plan',v->'plan') IS NOT TRUE THEN RETURN false; END IF;
  base:=jsonb_set(v,'{schema_version}','"mineagent.workflow-create.v1"'::jsonb);
  p:=((v->'plan')-'structured_template')||jsonb_build_object('schema_version','mineagent.rd-guidance-workflow-plan.v1','profile','rd_guidance_finite_loop.v1');
  RETURN @SCHEMA@.mineagent_workflow_payload_before_oe('create',jsonb_set(base,'{plan}',p)) IS TRUE;
 END IF;
 RETURN @SCHEMA@.mineagent_workflow_payload_before_oe(kind,v) IS TRUE;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
CREATE OR REPLACE FUNCTION @SCHEMA@.mineagent_oes_contract(kind text,v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE base jsonb; ctx jsonb; b jsonb; s jsonb; inputs jsonb;
BEGIN
 IF kind='config' AND v->>'schema_version'='mineagent.oe-structured-provider-run.v2' THEN
  IF @SCHEMA@.mineagent_oe_workflow_shape('config',v) IS NOT TRUE THEN RETURN false; END IF;
  base:=(v-ARRAY['workflow_request_sha256','workflow_context'])||jsonb_build_object('schema_version','mineagent.oe-structured-provider-run.v1','authenticated_request_sha256',v->'workflow_request_sha256');
  IF @SCHEMA@.mineagent_oes_contract_before_workflow('config',base) IS NOT TRUE THEN RETURN false; END IF;
  ctx:=v->'workflow_context'; inputs:=ctx->'inputs'; b:=inputs->'binding'; s:=inputs->'selection';
  RETURN (v#>>'{search,owner_id}',v#>>'{search,run_id}',v#>>'{fixed_inputs,snapshot_id}',v#>>'{fixed_inputs,snapshot_manifest_sha256}',v#>>'{fixed_inputs,factor_version_id}')
   IS NOT DISTINCT FROM (b->>'owner_id',ctx->>'child_run_id',ctx->>'snapshot_id',ctx->>'snapshot_manifest_sha256',ctx->>'factor_version_id')
   AND v#>'{fixed_inputs,release_receipt_ids}'=jsonb_build_array(ctx->'release_exposure_id')
   AND ctx->>'definition_sha256'=b->>'definition_sha256'
   AND ctx->>'rd_stage_id'<>b->>'stage_id'
   AND octet_length(@SCHEMA@.mineagent_rd_canonical_json(ctx,true))<=65536
   AND octet_length(@SCHEMA@.mineagent_rd_canonical_json(inputs,true))<=32768
   AND (b->>'owner_id',b->>'workflow_id',b->>'input_revision_id',b->>'input_sha256')
    IS NOT DISTINCT FROM (s->>'owner_id',s->>'workflow_id',s->>'revision_id',@SCHEMA@.mineagent_execution_metadata_sha(s))
   AND coalesce((SELECT jsonb_agg(x->'ref' ORDER BY ord) FROM jsonb_array_elements(inputs->'inputs') WITH ORDINALITY t(x,ord)),'[]'::jsonb)=s->'refs';
 END IF;
 IF kind='request' AND v->>'schema_version'='mineagent.oe-structured-workflow-submission.v1' THEN
  IF @SCHEMA@.mineagent_oe_workflow_shape('request',v) IS NOT TRUE THEN RETURN false; END IF;
  base:=(v-'workflow_context')||jsonb_build_object('schema_version','mineagent.oe-structured-provider-submission.v1','profile','fixed_factor_operator_closed_program_v1');
  RETURN @SCHEMA@.mineagent_oes_contract_before_workflow('request',base) IS TRUE
   AND v->>'snapshot_id'=v#>>'{workflow_context,snapshot_id}';
 END IF;
 RETURN @SCHEMA@.mineagent_oes_contract_before_workflow(kind,v) IS TRUE;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
"""

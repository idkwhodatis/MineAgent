"""027b pure provider material reuse; relational guards remain byte-identical.

Only function-local values of captured immutable JSON records are reused. No
relational read, process observation, lock or result is cached across invocations.
"""

import ast
import hashlib
import re
from pathlib import Path


def previous_graph():
    path = Path(__file__).parents[1] / "alembic/versions/027_rd_controller_v3.py"
    module = ast.parse(path.read_text())
    sql = next(
        ast.literal_eval(n.value)
        for n in module.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "UPGRADE_SQL" for t in n.targets)
    )
    if (
        hashlib.sha256(sql.encode()).hexdigest()
        != "01fc04b5bcb0dcb3f691f5a6c5f42a07c00ba7f363ee7ff77c8d92880ef5c971"
    ):
        raise ValueError("Frozen027 SQL changed")
    matches = list(
        re.finditer(
            r"CREATE OR REPLACE FUNCTION @SCHEMA@\.mineagent_rd_provider_graph\(", sql
        )
    )
    if len(matches) != 1:
        raise ValueError("Exact frozen provider graph required")
    start = matches[0].start()
    return sql[start : sql.index("$$;", start) + 3]


# Kept separately so actual PostgreSQL differential tests execute this exact
# same statement block, not a Python reimplementation of its predicates.
MATERIAL_SQL = r"""
  request_valid:=false; request_digest:=NULL; amount:=NULL;
  BEGIN
   IF binding_valid IS TRUE
   AND mineagent_rd_provider_payload(op.request->>'kind',op.request) IS TRUE THEN
    request_utf8:=mineagent_rd_canonical_json(op.request,false);
    request_ascii:=mineagent_rd_canonical_json(op.request,true);
    request_valid:=COALESCE(
      op.request->>'schema_version'='mineagent.rdagent-provider-'||(op.request->>'kind')||'.v1'
      AND op.request->>'model_profile_id'=binding->>'profile_id'
      AND op.request->>'model_binding_sha256'=binding_digest
      AND octet_length(request_utf8)<=262144
      AND octet_length(request_ascii)<=(binding->>'max_request_bytes')::bigint
      AND CASE WHEN op.request->>'kind'='chat' THEN
       op.request->>'model'=binding->>'chat_model'
       AND (op.request->>'max_output_tokens')::bigint<=(binding->>'max_output_tokens')::bigint
      ELSE op.request->>'model'=binding->>'embedding_model'
       AND op.request->'dimensions'=binding->'embedding_dimensions'
       AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(op.request->'inputs') value WHERE length(value) NOT BETWEEN 1 AND 8192)
      END,false);
    IF request_valid IS TRUE THEN
     request_digest:=encode(sha256(convert_to(request_utf8,'UTF8')),'hex');
    END IF;
   END IF;
  EXCEPTION WHEN OTHERS THEN request_valid:=false; request_digest:=NULL;
  END;
  IF request_valid IS TRUE THEN
   BEGIN
    IF binding->'pricing'->>'status'='operator_declared' THEN
     output_tokens:=0;
     IF op.request->>'kind'='chat' THEN
      output_tokens:=(op.request->>'max_output_tokens')::bigint;
      cost:=262144::numeric*(binding->'pricing'->>'chat_input_microusd_per_million')::numeric
       +output_tokens::numeric*(binding->'pricing'->>'chat_output_microusd_per_million')::numeric;
     ELSE cost:=262144::numeric*(binding->'pricing'->>'embedding_microusd_per_million')::numeric;
     END IF;
     amount:=jsonb_build_object('calls',1,'tokens',262144+output_tokens,
      'cost_microunits',ceil(cost/1000000)::bigint,'mutation_slots',0,'evaluator_executions',0,
      'wall_milliseconds',(binding->>'request_deadline_ms')::bigint,'storage_bytes',589824,'process_slots',1);
    END IF;
   EXCEPTION WHEN OTHERS THEN amount:=NULL;
   END;
  END IF;
"""

BINDING_SQL = r"""
   binding:=run.config->'model_binding'; binding_valid:=false; binding_digest:=NULL;
   BEGIN
    binding_valid:=mineagent_rd_provider_payload('binding',binding);
    IF binding_valid IS TRUE THEN
     binding_digest:=encode(sha256(convert_to(mineagent_rd_canonical_json(binding,true),'UTF8')),'hex');
    END IF;
   EXCEPTION WHEN OTHERS THEN binding_valid:=false; binding_digest:=NULL;
   END;
"""


def _once(value, old, new):
    if value.count(old) != 1:
        raise ValueError("Exact frozen provider material fragment required")
    return value.replace(old, new, 1)


def render_upgrade_sql():
    sql = previous_graph()
    sql = _once(
        sql,
        "steer_digest text;",
        "steer_digest text; binding jsonb; binding_valid boolean; binding_digest text; request_valid boolean; request_digest text; request_utf8 text; request_ascii text; output_tokens bigint; cost numeric;",
    )
    sql = _once(
        sql, "   config_checked:=true;", BINDING_SQL + "   config_checked:=true;"
    )
    sql = _once(
        sql,
        "  IF config_valid IS NOT TRUE",
        MATERIAL_SQL + "  IF config_valid IS NOT TRUE",
    )
    sql = _once(
        sql,
        "mineagent_rd_provider_request_binding(op.request,run.config->'model_binding') IS NOT TRUE",
        "request_valid IS NOT TRUE",
    )
    sql = _once(
        sql,
        "encode(sha256(convert_to(mineagent_rd_canonical_json(op.request,false),'UTF8')),'hex')",
        "request_digest",
    )
    sql = _once(
        sql,
        "  amount:=mineagent_rd_provider_reservation(op.request,run.config->'model_binding');\n",
        "",
    )
    return sql

"""Revision024 exact canonical SearchSpec grammar, generated from pinned DTO shape.

Source and nested model validators are supplemented explicitly below. This is not
a generic JSON-schema service: only the internally expanded finite schema literal
is used by the run predicate. PostgreSQL execution/differential gates required.
"""

import json
import re

from packages.openevolve_adapter.contracts import SearchOperationContext, SearchSpec


def _expanded(model):
    original = model.model_json_schema()

    def expand(node):
        if isinstance(node, list):
            return [expand(x) for x in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            if set(node) != {"$ref"} or not node["$ref"].startswith("#/$defs/"):
                raise ValueError("unsupported source schema reference")
            return expand(original["$defs"][node["$ref"].split("/")[-1]])
        result = {
            key: expand(value)
            for key, value in node.items()
            if key not in ("$defs", "title", "default", "discriminator")
        }
        if result.get("type") == "object":
            if result.get("additionalProperties") is not False:
                raise ValueError("search object must be closed")
            result["required"] = sorted(
                result["properties"]
            )  # persisted defaults explicit
        return result

    return expand(original)


def expanded_spec_schema():
    return _expanded(SearchSpec)


def expanded_context_schema():
    return _expanded(SearchOperationContext)


def identity_sql(schema):
    if type(schema) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema) is None:
        raise ValueError("SQL schema identifier")
    grammar = json.dumps(
        expanded_spec_schema(), sort_keys=True, separators=(",", ":")
    ).replace("'", "''")
    sql = r"""
CREATE FUNCTION @S@.mineagent_oe_shape(v jsonb, s jsonb, depth integer DEFAULT 0)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE k text; part jsonb; branch jsonb; actual text; wanted text; n numeric;
BEGIN
 IF depth<0 OR depth>24 OR octet_length(v::text)>1048576 THEN RETURN false; END IF;
 IF s ? 'const' AND v IS DISTINCT FROM s->'const' THEN RETURN false; END IF;
 IF s ? 'enum' AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(s->'enum') e WHERE e=v) THEN RETURN false; END IF;
 IF s ? 'anyOf' OR s ? 'oneOf' THEN
  FOR branch IN SELECT value FROM jsonb_array_elements(coalesce(s->'anyOf',s->'oneOf')) LOOP
   IF @S@.mineagent_oe_shape(v,branch,depth+1) THEN RETURN true; END IF;
  END LOOP;
  RETURN false;
 END IF;
 actual:=jsonb_typeof(v); wanted:=s->>'type';
 IF wanted='integer' THEN
  IF actual IS DISTINCT FROM 'number' OR v::text !~ '^-?(0|[1-9][0-9]*)$' THEN RETURN false; END IF;
  n:=(v::text)::numeric;
  IF s ? 'minimum' AND n<(s->>'minimum')::numeric OR s ? 'maximum' AND n>(s->>'maximum')::numeric THEN RETURN false; END IF;
 ELSIF wanted IN ('object','array','string','boolean','null') THEN
  IF actual IS DISTINCT FROM wanted THEN RETURN false; END IF;
 ELSE RETURN false;
 END IF;
 IF wanted='object' THEN
  IF s->'additionalProperties' IS DISTINCT FROM 'false'::jsonb OR
     (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(v) key)
      IS DISTINCT FROM (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(s->'properties') key) THEN RETURN false; END IF;
  FOR k,part IN SELECT key,value FROM jsonb_each(s->'properties') LOOP
   IF @S@.mineagent_oe_shape(v->k,part,depth+1) IS NOT TRUE THEN RETURN false; END IF;
  END LOOP;
 ELSIF wanted='array' THEN
  IF jsonb_array_length(v)<coalesce((s->>'minItems')::int,0) OR jsonb_array_length(v)>coalesce((s->>'maxItems')::int,10000) THEN RETURN false; END IF;
  FOR part IN SELECT value FROM jsonb_array_elements(v) LOOP
   IF @S@.mineagent_oe_shape(part,s->'items',depth+1) IS NOT TRUE THEN RETURN false; END IF;
  END LOOP;
 ELSIF wanted='string' THEN
  k:=v#>>'{}';
  IF length(k)<coalesce((s->>'minLength')::int,0) OR length(k)>coalesce((s->>'maxLength')::int,1048576) OR s ? 'pattern' AND k !~ (s->>'pattern') THEN RETURN false; END IF;
  IF s->>'format'='uuid' AND (k !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' OR (k::uuid)::text<>k) THEN RETURN false; END IF;
  IF s->>'format'='date' AND (k !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' OR to_char(k::date,'YYYY-MM-DD')<>k) THEN RETURN false; END IF;
  IF s ? 'format' AND s->>'format' NOT IN ('uuid','date') THEN RETURN false; END IF;
 END IF;
 RETURN true;
EXCEPTION WHEN others THEN RETURN false;
END $$;
CREATE FUNCTION @S@.mineagent_oe_source_config(v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE w jsonb; b jsonb;
BEGIN
 IF @S@.mineagent_oe_shape(v,'@GRAMMAR@'::jsonb) IS NOT TRUE THEN RETURN false; END IF;
 IF v#>>'{preparation,kind}'<>'none' OR v#>'{preparation,original}' IS DISTINCT FROM v#>'{preparation,prepared}'
 OR v#>'{preparation,transform_sha256}' IS DISTINCT FROM 'null'::jsonb
 OR v#>'{preparation,parity_profile}' IS DISTINCT FROM 'null'::jsonb
 OR v#>>'{factors,kind}'<>'none'
 OR (v->>'maximum_mutation_slots')::numeric>2 OR (v->>'population_size')::numeric>8 OR (v->>'archive_size')::numeric>8
 OR v#>'{execution,windows}' IS DISTINCT FROM v#>'{objective,windows}'
 OR v#>'{objective,metric_paths}' IS DISTINCT FROM '["metrics.annualized_return","metrics.max_drawdown"]'::jsonb THEN RETURN false; END IF;
 IF (SELECT count(*) FROM jsonb_array_elements(v#>'{execution,files}'))<>(SELECT count(DISTINCT x->>'logical_name') FROM jsonb_array_elements(v#>'{execution,files}') x)
 OR (SELECT count(*) FROM jsonb_array_elements(v#>'{objective,windows}'))<>(SELECT count(DISTINCT x->>'id') FROM jsonb_array_elements(v#>'{objective,windows}') x)
 OR (SELECT sum((x->>'weight_millionths')::numeric) FROM jsonb_array_elements(v#>'{objective,windows}') x)<>1000000 THEN RETURN false; END IF;
 FOR w IN SELECT value FROM jsonb_array_elements(v#>'{objective,windows}') LOOP
  IF (w->>'start')::date>=(w->>'end')::date THEN RETURN false; END IF;
 END LOOP;
 b:=v->'budget';
 IF (b->>'evaluator_executions')::numeric<jsonb_array_length(v#>'{execution,windows}') THEN RETURN false; END IF;
 IF (v->>'maximum_mutation_slots')::numeric>(b->>'mutation_slots')::numeric THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM unnest(ARRAY['calls','tokens','evaluator_executions','wall_milliseconds','storage_bytes','process_slots']) k WHERE (b->>k)::numeric<=0) THEN RETURN false; END IF;
 RETURN true;
EXCEPTION WHEN others THEN RETURN false;
END $$;
"""
    from runtimes.openevolve_operational.recipe_pins import PINS

    pin_checks = " OR ".join(
        f"v->>'{field}' IS DISTINCT FROM '{value[1]}'" for field, value in PINS.items()
    )
    sql = sql.replace(
        " IF v#>>'{preparation,kind}'",
        " IF "
        + pin_checks
        + " THEN RETURN false; END IF;\n IF v#>>'{preparation,kind}'",
    )
    context = json.dumps(
        expanded_context_schema(), sort_keys=True, separators=(",", ":")
    ).replace("'", "''")
    sql += r"""
CREATE FUNCTION @S@.mineagent_oe_context(v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE STRICT AS $$
BEGIN
 IF @S@.mineagent_oe_shape(v,'@CONTEXT@'::jsonb) IS NOT TRUE THEN RETURN false; END IF;
 RETURN (v->>'native_iteration')::numeric BETWEEN 1 AND 2
   AND (v->>'operation_ordinal')::numeric=0;
EXCEPTION WHEN others THEN RETURN false;
END $$;
CREATE FUNCTION @S@.mineagent_oe_request(v jsonb, protocol text) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE message jsonb; ordinal integer:=0;
BEGIN
 IF protocol<>'mineagent.openevolve-chat.v1' OR jsonb_typeof(v) IS DISTINCT FROM 'object'
 OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(v) key)
  IS DISTINCT FROM ARRAY['kind','max_output_tokens','messages','model','temperature_millionths']::text[]
 OR v->'kind' IS DISTINCT FROM '"chat"'::jsonb
 OR v->'model' IS DISTINCT FROM '"openevolve-fixture.v1"'::jsonb
 OR v->'max_output_tokens' IS DISTINCT FROM '32768'::jsonb
 OR v->'temperature_millionths' IS DISTINCT FROM '0'::jsonb
 OR (v->'max_output_tokens')::text !~ '^[0-9]+$'
 OR (v->'temperature_millionths')::text !~ '^[0-9]+$'
 OR jsonb_typeof(v->'messages') IS DISTINCT FROM 'array'
 OR jsonb_array_length(v->'messages')<>2 THEN RETURN false; END IF;
 FOR message IN SELECT value FROM jsonb_array_elements(v->'messages') LOOP
  IF jsonb_typeof(message) IS DISTINCT FROM 'object'
   OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(message) key)
     IS DISTINCT FROM ARRAY['content','role']::text[]
   OR jsonb_typeof(message->'content') IS DISTINCT FROM 'string'
   OR length(message->>'content') NOT BETWEEN 1 AND 262144
   OR message->'role' IS DISTINCT FROM to_jsonb(CASE WHEN ordinal=0 THEN 'system'::text ELSE 'user'::text END)
   THEN RETURN false; END IF;
  ordinal:=ordinal+1;
 END LOOP;
 RETURN octet_length(@S@.mineagent_rd_canonical_json(v,false,0))<=786432;
EXCEPTION WHEN others THEN RETURN false;
END $$;
"""
    return (
        sql.replace("@S@", schema)
        .replace("@GRAMMAR@", grammar)
        .replace("@CONTEXT@", context)
    )

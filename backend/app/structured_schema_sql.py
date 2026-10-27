"""025 numeric/boolean schema successor; predecessor integer grammar is preserved."""

import hashlib

from backend.app.openevolve_revision_ddl import _function, historical

PREDECESSOR_SHA256 = "57047b1e2de302c2cc19e4f64cd974f89555ebc201fc9e90fc3cc727bf966715"


def previous_schema_sql():
    source = historical("018", "UPGRADE_SQL")
    if hashlib.sha256(source.encode()).hexdigest() != PREDECESSOR_SHA256:
        raise ValueError("Reviewed018 schema predecessor changed")
    return _function(source, "mineagent_rd_schema")


def structured_schema(cls):
    """Normalize integer-open bounds only; fractional bounds stay genuinely open."""
    rule = cls.model_json_schema()
    # Review only schema nodes, never interpret const/default payload keys as rules.
    allowed = {
        "$defs",
        "$ref",
        "additionalProperties",
        "properties",
        "required",
        "title",
        "description",
        "type",
        "default",
        "const",
        "enum",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "items",
        "prefixItems",
        "minItems",
        "maxItems",
        "anyOf",
        "oneOf",
        "discriminator",
    }

    def audit(node):
        if type(node) is not dict or set(node) - allowed:
            raise ValueError("Unreviewed structured JSON Schema keyword")
        if "type" in node and node["type"] not in {
            "object",
            "array",
            "string",
            "integer",
            "number",
            "boolean",
            "null",
        }:
            raise ValueError("Unreviewed structured JSON Schema type")
        if (
            node.get("type") == "object"
            and "properties" not in node
            and node.get("additionalProperties") is not True
        ):
            raise ValueError("Unreviewed structured object schema")
        for key in ("properties", "$defs"):
            for child in node.get(key, {}).values():
                audit(child)
        if "items" in node:
            audit(node["items"])
        for key in ("prefixItems", "anyOf", "oneOf"):
            for child in node.get(key, []):
                audit(child)

    audit(rule)

    def visit(node):
        if isinstance(node, dict):
            if node.get("type") == "integer":
                for old, new, delta in (
                    ("exclusiveMinimum", "minimum", 1),
                    ("exclusiveMaximum", "maximum", -1),
                ):
                    if old in node:
                        if type(node[old]) is not int:
                            raise ValueError(
                                "Reviewed integral schema boundary required"
                            )
                        node[new] = node.pop(old) + delta
            if "prefixItems" in node:
                items = node.pop("prefixItems")
                if not items or any(item != items[0] for item in items):
                    raise ValueError("Uniform structured tuple schema required")
                node["items"] = items[0]
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(rule)
    return rule


JSON_VALUE_SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_structured_json_value(v jsonb, depth integer) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE item jsonb; number double precision;
BEGIN
 IF v IS NULL OR depth>24 OR octet_length(v::text)>16777216 THEN RETURN false; END IF;
 CASE jsonb_typeof(v)
 WHEN 'object' THEN
  FOR item IN SELECT value FROM jsonb_each(v) LOOP
   IF NOT @SCHEMA@.mineagent_structured_json_value(item,depth+1) THEN RETURN false; END IF;
  END LOOP;
 WHEN 'array' THEN
  FOR item IN SELECT value FROM jsonb_array_elements(v) LOOP
   IF NOT @SCHEMA@.mineagent_structured_json_value(item,depth+1) THEN RETURN false; END IF;
  END LOOP;
 WHEN 'number' THEN
  number:=(v::text)::double precision;
  IF number::text IN ('NaN','Infinity','-Infinity') OR (number=0 AND (v::text)::numeric<>0) THEN RETURN false; END IF;
 WHEN 'string' THEN NULL;
 WHEN 'boolean' THEN NULL;
 WHEN 'null' THEN NULL;
 ELSE RETURN false;
 END CASE;
 RETURN true;
EXCEPTION WHEN data_exception THEN RETURN false;
END $$;
"""


def schema_sql():
    sql = previous_schema_sql()
    old = " WHEN 'string' THEN\n   t:=v#>>'{}';"
    numeric = """ WHEN 'number' THEN
   IF (v::text)::double precision::text IN ('NaN','Infinity','-Infinity')
    OR ((v::text)::double precision=0 AND (v::text)::numeric<>0) THEN RETURN false; END IF;
   IF rule ? 'minimum' AND (v::text)::numeric<(rule->>'minimum')::numeric THEN RETURN false; END IF;
   IF rule ? 'maximum' AND (v::text)::numeric>(rule->>'maximum')::numeric THEN RETURN false; END IF;
   IF rule ? 'exclusiveMinimum' AND (v::text)::numeric<=(rule->>'exclusiveMinimum')::numeric THEN RETURN false; END IF;
   IF rule ? 'exclusiveMaximum' AND (v::text)::numeric>=(rule->>'exclusiveMaximum')::numeric THEN RETURN false; END IF;
 WHEN 'string' THEN
   t:=v#>>'{}';"""
    assert sql.count(old) == 1
    sql = sql.replace(old, numeric)
    old = " WHEN 'object' THEN\n   IF (SELECT array_agg(key"
    new = """ WHEN 'object' THEN
   IF rule->'additionalProperties'='true'::jsonb AND NOT (rule ? 'properties') THEN
    IF NOT @SCHEMA@.mineagent_structured_json_value(v,depth) THEN RETURN false; END IF;
   ELSE
   IF (SELECT array_agg(key"""
    assert sql.count(old) == 1
    sql = sql.replace(old, new)
    old = " WHEN 'null' THEN NULL;"
    assert sql.count(old) == 1
    sql = sql.replace(
        old,
        "   END IF;\n WHEN 'boolean' THEN NULL; -- Exact JSON type check above is mandatory.\n"
        + old,
    )
    return sql

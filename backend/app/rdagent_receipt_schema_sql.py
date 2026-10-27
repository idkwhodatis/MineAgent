"""Historical020/022 domain-scoped parsers, never a global schema widening.

Both clone the byte-pinned018 recursive grammar. Controller journals introduce
strict booleans; analysis journals/results additionally introduce finite binary64
numbers. Old integer rules, closed objects and global018 parser remain unchanged.
"""

import re

from backend.app.rdagent_controller_revision import frozen_source_sql

NUMBER_BRANCH = """ WHEN 'number' THEN
   IF (v::text)::double precision::text IN ('NaN','Infinity','-Infinity')
    OR ((v::text)::double precision=0 AND (v::text)::numeric<>0) THEN RETURN false; END IF;
   IF rule ? 'minimum' AND (v::text)::numeric<(rule->>'minimum')::numeric THEN RETURN false; END IF;
   IF rule ? 'maximum' AND (v::text)::numeric>(rule->>'maximum')::numeric THEN RETURN false; END IF;
   IF rule ? 'exclusiveMinimum' AND (v::text)::numeric<=(rule->>'exclusiveMinimum')::numeric THEN RETURN false; END IF;
   IF rule ? 'exclusiveMaximum' AND (v::text)::numeric>=(rule->>'exclusiveMaximum')::numeric THEN RETURN false; END IF;
"""


def receipt_schema_sql(domain):
    if domain not in {"controller", "analysis"}:
        raise ValueError("Exact historical receipt domain required")
    source = frozen_source_sql()
    matches = list(
        re.finditer(
            r"CREATE(?: OR REPLACE)? FUNCTION @SCHEMA@\.mineagent_rd_schema\(", source
        )
    )
    if len(matches) != 1:
        raise ValueError("Exact018 schema function required")
    start = matches[0].start()
    sql = source[start : source.index("END $$;", start) + len("END $$;")]
    sql = sql.replace("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION", 1)
    old = " WHEN 'null' THEN NULL;"
    if sql.count(old) != 1:
        raise ValueError("Exact018 null branch required")
    sql = sql.replace(old, " WHEN 'boolean' THEN NULL;\n" + old)
    if domain == "analysis":
        old = " WHEN 'string' THEN\n   t:=v#>>'{}';"
        if sql.count(old) != 1:
            raise ValueError("Exact018 string branch required")
        sql = sql.replace(old, NUMBER_BRANCH + old)
    # Rename every recursive reference, not only the CREATE signature.
    return sql.replace("mineagent_rd_schema", f"mineagent_rd_{domain}_schema")


def receipt_contract_schema(cls, domain):
    """Audit actual historical grammar nodes before freezing the domain literal."""
    if domain not in {"controller", "analysis"}:
        raise ValueError("Exact historical receipt domain required")
    rule = cls.model_json_schema()
    allowed = {
        "$defs",
        "$ref",
        "additionalProperties",
        "properties",
        "required",
        "title",
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
    types = {"object", "array", "integer", "string", "boolean", "null"}
    if domain == "analysis":
        types.add("number")

    def visit(node):
        if type(node) is not dict or set(node) - allowed:
            raise ValueError("Unreviewed receipt schema keyword")
        if "type" in node and node["type"] not in types:
            raise ValueError("Unreviewed receipt schema type")
        if "format" in node and node["format"] not in {"uuid", "date", "date-time"}:
            raise ValueError("Unreviewed receipt schema format")
        if node.get("type") == "object" and (
            node.get("additionalProperties") is not False or "properties" not in node
        ):
            raise ValueError("Receipt objects must remain exact closed records")
        if node.get("type") == "integer":
            for old, new, delta in (
                ("exclusiveMinimum", "minimum", 1),
                ("exclusiveMaximum", "maximum", -1),
            ):
                if old in node:
                    if type(node[old]) is not int:
                        raise ValueError("Integral receipt bound required")
                    node[new] = node.pop(old) + delta
        if "prefixItems" in node:
            items = node.pop("prefixItems")
            if not items or any(item != items[0] for item in items):
                raise ValueError("Uniform receipt tuple required")
            node["items"] = items[0]
        for key in ("properties", "$defs"):
            for child in node.get(key, {}).values():
                visit(child)
        if "items" in node:
            visit(node["items"])
        for key in ("anyOf", "oneOf"):
            for child in node.get(key, []):
                visit(child)

    visit(rule)
    return rule

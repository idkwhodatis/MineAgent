"""027a local serialized-manifest compatibility; never rewrite canonical input."""

import hashlib
import json
import re

from backend.app.openevolve_revision_ddl import _function, historical

PREDECESSOR_SHA256 = "45fa55c439f829f3d7fadbbd7b4920148b3e5b647a4964014cd92150b71115d3"


def previous_function():
    source = historical("025", "UPGRADE_SQL")
    if hashlib.sha256(source.encode()).hexdigest() != PREDECESSOR_SHA256:
        raise ValueError("Exact structured025 predecessor changed")
    result = _function(source, "mineagent_structured_contract")
    matches = re.findall(r"WHEN 'input' THEN rule := '(.*?)'::jsonb;", result)
    if len(matches) != 1:
        raise ValueError("Exact structured input rule absent")
    rule = json.loads(matches[0].replace("''", "'"))
    manifest = rule["$defs"]["StrategyManifest"]
    if (
        manifest["properties"]["authoring"]
        != {
            "anyOf": [{"$ref": "#/$defs/UserPolicyDeclaration"}, {"type": "null"}],
            "default": None,
        }
        or "authoring" not in manifest["required"]
    ):
        raise ValueError("Exact historical optional authoring rule differs")
    return result.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


PROJECTION = """ IF kind='input' THEN
  IF jsonb_typeof(v#>'{engine,manifest}') IS DISTINCT FROM 'object' THEN RETURN false; END IF;
  -- The closed structured engine forbids portable policy declarations entirely.
  -- None is omitted by its existing canonical serializer, never emitted as null.
  IF (v#>'{engine,manifest}') ? 'authoring' THEN RETURN false; END IF;
  -- Project the validation rule only. Original v/raw bytes and every binding remain unchanged.
  rule:=jsonb_set(rule,'{$defs,StrategyManifest,properties}',
   (rule#>'{$defs,StrategyManifest,properties}')-'authoring');
  rule:=jsonb_set(rule,'{$defs,StrategyManifest,required}',
   (rule#>'{$defs,StrategyManifest,required}')-'authoring');
 END IF;
"""


def render_upgrade_sql():
    old = previous_function()
    marker = " RETURN @SCHEMA@.mineagent_rd_schema(v,rule,coalesce(rule->'$defs','{}'::jsonb),0);"
    if old.count(marker) != 1:
        raise ValueError("Exact structured contract return differs")
    return old.replace(marker, PROJECTION + marker)


def render_downgrade_sql():
    # No old execution bytes can be silently reinterpreted under a stricter rule.
    return """DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM @SCHEMA@.factor_strategy_executions
 WHERE NOT ((strategy_input#>'{engine,manifest}') ? 'authoring')) THEN
  RAISE EXCEPTION 'Cannot downgrade retained structured inputs with omitted authoring';
 END IF;
END $$;
""" + previous_function()

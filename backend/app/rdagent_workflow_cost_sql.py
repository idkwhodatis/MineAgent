"""Exact local-expression optimizations over frozen validators.

No cross-call cache, removed predicate, changed deadline or new authority.
"""

import ast
import hashlib
from pathlib import Path

from backend.app import oe_validation_cost_sql
from backend.app.rdagent_provider_revision import changed, function
from backend.app.rdagent_validation_performance_sql import schema_original


def workflow_original():
    path = Path(__file__).parents[1] / "alembic/versions/029_research_workflow.py"
    sql = next(
        ast.literal_eval(node.value)
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "UPGRADE_SQL" for t in node.targets)
    )
    if hashlib.sha256(sql.encode()).hexdigest() != (
        "2848ba2747b703965ad8de8048591ac1931854666cda2f1f6dd512976aaf9ed3"
    ):
        raise ValueError("Exact reviewed workflow predecessor required")
    return function(sql, "mineagent_rd_workflow_submission_check")


def control_scan_sql():
    # PostgreSQL jsonb/text cannot contain U+0000. C collation and explicit
    # character endpoints match ascii(codepoint)<32 OR codepoint=127 exactly,
    # including Unicode text, without a per-character SQL scan/substr loop.
    return changed(
        schema_original(),
        "IF EXISTS(SELECT 1 FROM generate_series(1,n) i WHERE ascii(substr(t,i,1))<32 OR ascii(substr(t,i,1))=127) THEN RETURN false; END IF;",
        "IF t COLLATE \"C\" ~ ('['||chr(1)||'-'||chr(31)||chr(127)||']') THEN RETURN false; END IF;",
    )


MODEL_TITLES = (
    "DateWindow",
    "FittedPreprocessing",
    "CalculationUniverse",
    "RuntimePins",
    "FrozenResearchInput",
    "FactorResearchConfig",
    "RDEmbeddingRequest",
)
CHILD_CALLS = (
    ("r", "rule->'items'"),
    ("v->e.key", "e.value"),
)


def scalar_child_sql(value, rule):
    """Inline the pinned scalar body, with private block-local interpreter state.

    Errors still reach the enclosing data_exception handler: a failed array or
    object child immediately rejected that enclosing call in the predecessor.
    Unions remain recursive, so their branch-local recovery is unchanged.
    """
    source = control_scan_sql()
    scalar = source[
        source.index(" kind:=rule->>'type';") : source.index(" WHEN 'array' THEN")
    ]
    scalar += " WHEN 'boolean' THEN NULL;\n WHEN 'null' THEN NULL;\n ELSE RETURN false;\n END CASE;\n"
    titles = ",".join("'" + title + "'" for title in MODEL_TITLES)
    return f"""     DECLARE
       child_value jsonb := {value};
       child_rule jsonb := {rule};
       child_depth integer := depth+1;
     BEGIN
       IF jsonb_typeof(child_rule)='object'
         AND NOT (child_rule ?| ARRAY['$ref','anyOf','oneOf'])
         AND child_rule->>'type' IN ('integer','number','string','boolean','null')
         AND coalesce(child_rule->>'title' NOT IN ({titles}),true)
       THEN
         DECLARE
           v jsonb := child_value; rule jsonb := child_rule;
           depth integer := child_depth;
           kind text; title text; t text; s text; n integer;
         BEGIN
           IF v IS NULL OR depth>24 THEN RETURN false; END IF;
{scalar}         END;
       ELSE
         IF NOT @SCHEMA@.mineagent_rd_schema(child_value,child_rule,defs,child_depth) THEN RETURN false; END IF;
       END IF;
     END;
"""


def schema_sql():
    sql = control_scan_sql()
    for value, rule in CHILD_CALLS:
        old = f"     IF NOT @SCHEMA@.mineagent_rd_schema({value},{rule},defs,depth+1) THEN RETURN false; END IF;\n"
        sql = changed(sql, old, scalar_child_sql(value, rule))
    return sql


def workflow_sql():
    sql = changed(
        workflow_original(),
        "expected jsonb; fields jsonb; exact_bytes text; profile_sha text; preflight jsonb;",
        "expected jsonb; fields jsonb; exact_bytes text; profile_sha text; preflight jsonb;\n cfg_sha text; input_sha text;",
    )
    cfg = "mineagent_execution_metadata_sha(cfg)"
    inp = "mineagent_execution_metadata_sha(cfg->'input')"
    if sql.count(cfg) != 4 or sql.count(inp) != 2:
        raise ValueError("Exact repeated workflow commitments required")
    sql = sql.replace(cfg, "cfg_sha").replace(inp, "input_sha")
    return changed(
        sql,
        " IF mineagent_rd_workflow_config(cfg) IS NOT TRUE\n OR q.submission_origin",
        " IF mineagent_rd_workflow_config(cfg) IS NOT TRUE THEN\n"
        "  RAISE EXCEPTION 'Workflow child immutable graph differs';\n"
        " END IF;\n"
        f" cfg_sha:={cfg}; input_sha:={inp};\n IF q.submission_origin",
    )


def render_upgrade_sql():
    return (
        f"{schema_sql()}\n{workflow_sql()}\n"
        f"{oe_validation_cost_sql.render_upgrade_sql()}"
    )


def render_downgrade_sql():
    return (
        f"{schema_original()}\n{workflow_original()}\n"
        f"{oe_validation_cost_sql.render_downgrade_sql()}"
    )

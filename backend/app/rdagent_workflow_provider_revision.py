"""029 exact workflow dispatch over pinned 027/028 provider predicates.

This fragment does not replace the schema interpreter or the v2 provider config
validator. Install workflow tables/config/submission predicates before it. The
SQL graph is relational evidence, never a substitute for retained-byte readers.
"""

import ast
import hashlib
from pathlib import Path

from backend.app.rdagent_provider_revision import changed, function

PINS = {
    "027c_seeded_controller_v4.py": "084ab22623c5c0ff3f9a61e94ea0db19be70292e2fd5deb6d30946396b388b2b",
    "026_rd_seeded_submissions.py": "629d918feb22c39f943bc2a7022f77f605adca28fbcce0ce5868c250815f2e2c",
    "028_oe_structured_search.py": "dbb61d771730685de3cf8cb2fd7eeafe48e2c7c77fa54d57c19d70df2e68f7c9",
}
FUNCTIONS = (
    "mineagent_rd_source_config",
    "mineagent_rd_provider_graph",
    "mineagent_rd_source_violation",
    "mineagent_rd_operational_boundary_consistent",
    "mineagent_rd_provider_queued_config",
    "mineagent_rd_numeric_failure_actor",
)


def _literal(filename):
    tree = ast.parse(
        (Path(__file__).parents[1] / "alembic/versions" / filename).read_text()
    )
    sql = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "UPGRADE_SQL" for t in node.targets)
    )
    if hashlib.sha256(sql.encode()).hexdigest() != PINS[filename]:
        raise ValueError("Review exact 029 provider predecessor bytes before repinning")
    return sql


def original(name):
    filename = (
        "027c_seeded_controller_v4.py"
        if name == "mineagent_rd_provider_graph"
        else "028_oe_structured_search.py"
        if name == "mineagent_rd_numeric_failure_actor"
        else "026_rd_seeded_submissions.py"
        if name == "mineagent_rd_source_violation"
        else "027c_seeded_controller_v4.py"
    )
    return function(_literal(filename), name)


def replacements():
    result = {name: original(name) for name in FUNCTIONS}
    result[FUNCTIONS[0]] = changed(
        result[FUNCTIONS[0]],
        "BEGIN\n IF v->>'schema_version'='mineagent.factor-research.v2'",
        "BEGIN\n IF v->>'schema_version'='mineagent.factor-research.v3' THEN RETURN mineagent_rd_workflow_config(v); END IF;\n IF v->>'schema_version'='mineagent.factor-research.v2'",
    )
    result[FUNCTIONS[1]] = changed(
        result[FUNCTIONS[1]],
        "config_valid:=mineagent_rd_provider_config(run.config);",
        "config_valid:=(CASE WHEN run.config->>'schema_version'='mineagent.factor-research.v3' THEN mineagent_rd_workflow_config(run.config) ELSE mineagent_rd_provider_config(run.config) END);",
    )
    result[FUNCTIONS[1]] = changed(
        result[FUNCTIONS[1]],
        " SELECT * INTO run FROM research_runs WHERE id=rid;",
        """ SELECT * INTO run FROM research_runs WHERE id=rid;
 IF run.config->>'schema_version'='mineagent.factor-research.v3' THEN
  PERFORM mineagent_rd_workflow_submission_check((SELECT q.id FROM factor_workflow_submissions q
   WHERE q.run_id=rid AND q.owner_id=run.owner_id));
 END IF;""",
    )
    result[FUNCTIONS[2]] = changed(
        result[FUNCTIONS[2]],
        "r.config->>'schema_version'='mineagent.factor-research.v2'",
        "r.config->>'schema_version' IN ('mineagent.factor-research.v2','mineagent.factor-research.v3')",
    )
    result[FUNCTIONS[3]] = changed(
        result[FUNCTIONS[3]],
        "cl.bootstrap#>>'{config,schema_version}'='mineagent.factor-research.v2'",
        "cl.bootstrap#>>'{config,schema_version}' IN ('mineagent.factor-research.v2','mineagent.factor-research.v3')",
    )
    result[FUNCTIONS[4]] = changed(
        result[FUNCTIONS[4]],
        "mineagent_rd_provider_config(v) IS NOT TRUE",
        "(CASE WHEN v->>'schema_version'='mineagent.factor-research.v3' THEN mineagent_rd_workflow_config(v) ELSE mineagent_rd_provider_config(v) END) IS NOT TRUE",
    )
    result[FUNCTIONS[5]] = changed(
        result[FUNCTIONS[5]],
        "AND spec.config->>'model_profile'='configured.rdagent.v1'",
        """AND (spec.config->>'model_profile'='configured.rdagent.v1'
 OR (spec.config->>'schema_version'='mineagent.factor-research.v3'
 AND spec.config->>'model_profile'='workflow.rdagent.v1'
 AND spec.config=r.config AND spec.config->>'workflow_child_run_id'=r.id::text
 AND mineagent_rd_workflow_config(spec.config) IS TRUE
 AND EXISTS(SELECT 1 FROM factor_workflow_submissions q
  WHERE (q.owner_id,q.run_id,q.spec_id)=(r.owner_id,r.id,spec.id))))""",
    )
    result[FUNCTIONS[5]] = changed(
        result[FUNCTIONS[5]],
        " RETURN NEW;",
        """ IF EXISTS(SELECT 1 FROM research_runs r WHERE r.id=NEW.run_id
  AND r.config->>'schema_version'='mineagent.factor-research.v3') THEN
  PERFORM mineagent_rd_workflow_submission_check((SELECT q.id FROM factor_workflow_submissions q
   WHERE q.run_id=NEW.run_id AND q.owner_id=NEW.owner_id));
 END IF;
 RETURN NEW;""",
    )
    return result


def render_upgrade_sql():
    return "\n".join(replacements().values())


def render_downgrade_sql():
    # Must run before dropping workflow predicates/tables. Never reinterpret v3.
    return """
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM @SCHEMA@.research_runs WHERE config->>'schema_version'='mineagent.factor-research.v3')
 THEN RAISE EXCEPTION 'Workflow child history prevents provider dispatch downgrade'; END IF;
END $$;
""" + "\n".join(original(name) for name in reversed(FUNCTIONS))

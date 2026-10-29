"""028 exact bootstrap/origin deltas over reviewed027/026 literal functions.

No runtime migration imports this renderer. Final assembly must repin the final
predecessors, freeze the result, and execute actual positive/negative PG graphs.
"""

import hashlib

from backend.app.openevolve_revision_ddl import historical
from backend.app.rdagent_provider_revision import changed, function

PINS = {
    "026": "629d918feb22c39f943bc2a7022f77f605adca28fbcce0ce5868c250815f2e2c",
    "028": "dbb61d771730685de3cf8cb2fd7eeafe48e2c7c77fa54d57c19d70df2e68f7c9",
}
FUNCTIONS = {
    "mineagent_rd_shared_consistent": "028",
    "mineagent_rd_controller_consistent": "028",
    "mineagent_rd_seed_consistent": "026",
    "mineagent_rd_submission_consistent": "026",
}


def predecessor(name):
    revision = FUNCTIONS[name]
    sql = historical(revision, "UPGRADE_SQL")
    if hashlib.sha256(sql.encode()).hexdigest() != PINS[revision]:
        raise ValueError("Exact reviewed workflow predecessor required")
    return function(sql, name)


def bootstrap_branch(*, expected, spec, admission):
    return f"""
  IF {spec}.config->>'schema_version'='mineagent.factor-research.v3' THEN
   IF ({admission}.profile IS NULL OR {admission}.profile NOT IN ('mineagent.rdagent-orchestrator.v2','mineagent.rdagent-orchestrator.v3'))
    OR mineagent_rd_controller_profile_matches({spec}.config,{admission}.profile) IS NOT TRUE
    OR {spec}.config->>'workflow_child_run_id' IS DISTINCT FROM cl.run_id::text
    OR EXISTS(SELECT 1 FROM factor_seeded_submissions WHERE run_id=cl.run_id)
    OR EXISTS(SELECT 1 FROM factor_research_submissions WHERE run_id=cl.run_id)
   THEN RAISE EXCEPTION 'Workflow controller requires exact new child origin'; END IF;
   PERFORM mineagent_rd_workflow_submission_check((SELECT id FROM factor_workflow_submissions WHERE run_id=cl.run_id AND owner_id=cl.owner_id));
   {expected}:={expected} || jsonb_build_object(
    'schema_version','mineagent.rdagent-workflow-operational-bootstrap.v1',
    'continuation','workflow_stage_new_process','seed',NULL);
   IF {spec}.config->'workflow_parent' IS DISTINCT FROM 'null'::jsonb THEN
    SELECT {expected} || jsonb_build_object('seed',jsonb_build_object(
     'reference',{spec}.config->'workflow_parent','definition',v.definition,'source',v.code)) INTO {expected}
    FROM factor_versions v WHERE v.owner_id=cl.owner_id
     AND v.id=({spec}.config#>>'{{workflow_parent,factor_version_id}}')::uuid;
    IF NOT FOUND THEN RAISE EXCEPTION 'Workflow actual parent version absent'; END IF;
   END IF;
  END IF;
"""


def shared_sql():
    sql = predecessor("mineagent_rd_shared_consistent")
    sql = changed(
        sql,
        "CASE WHEN rr.config->>'schema_version'='mineagent.factor-research.v2' THEN op.protocol",
        "CASE WHEN rr.config->>'schema_version' IN ('mineagent.factor-research.v2','mineagent.factor-research.v3') THEN op.protocol",
    )
    marker = "  IF ab.resumed_checkpoint_id IS NOT NULL OR cl.bootstrap IS DISTINCT FROM expected"
    return changed(
        sql,
        marker,
        bootstrap_branch(expected="expected", spec="spec", admission="pa") + marker,
    )


def controller_sql():
    marker = (
        "  IF cl.bootstrap<>expected_bootstrap OR cl.bootstrap_sha256<>a.input_sha256"
    )
    return changed(
        predecessor("mineagent_rd_controller_consistent"),
        marker,
        bootstrap_branch(expected="expected_bootstrap", spec="sp", admission="a")
        + marker,
    )


def seed_sql():
    sql = predecessor("mineagent_rd_seed_consistent")
    marker = " AND s.parent_factor_version_id=st.parent_factor_version_id))"
    return changed(
        sql,
        marker,
        """ AND s.parent_factor_version_id=st.parent_factor_version_id)
 AND NOT EXISTS(SELECT 1 FROM factor_workflow_submissions ws
 JOIN research_workflow_stages wst ON wst.id=ws.stage_id AND wst.owner_id=ws.owner_id AND wst.workflow_id=ws.workflow_id
 JOIN research_runs wr ON wr.id=ws.run_id AND wr.owner_id=ws.owner_id
 WHERE ws.run_id=st.run_id AND ws.owner_id=st.owner_id AND wst.child_run_id=st.run_id
 AND wr.config->>'schema_version'='mineagent.factor-research.v3'
 AND wr.config#>>'{workflow_parent,factor_version_id}'=st.parent_factor_version_id::text
 AND wst.definition_canonical::jsonb->>'parent_factor_version_id'=st.parent_factor_version_id::text))""",
    )


def submission_sql():
    sql = predecessor("mineagent_rd_submission_consistent")
    marker = """  AND (EXISTS(SELECT 1 FROM factor_research_submissions WHERE run_id=NEW.id))
   = (EXISTS(SELECT 1 FROM factor_seeded_submissions WHERE run_id=NEW.id))"""
    return changed(
        sql,
        marker,
        """  AND ((CASE WHEN EXISTS(SELECT 1 FROM factor_research_submissions WHERE run_id=NEW.id) THEN 1 ELSE 0 END)
   +(CASE WHEN EXISTS(SELECT 1 FROM factor_seeded_submissions WHERE run_id=NEW.id) THEN 1 ELSE 0 END)
   +(CASE WHEN EXISTS(SELECT 1 FROM factor_workflow_submissions ws WHERE ws.run_id=NEW.id AND ws.owner_id=NEW.owner_id
      AND NEW.config->>'schema_version'='mineagent.factor-research.v3') THEN 1 ELSE 0 END))<>1""",
    )


def render_upgrade_sql():
    return f"{shared_sql()}\n{controller_sql()}\n{seed_sql()}\n{submission_sql()}\n{BOOTSTRAP_UPGRADE}\n{SPEC_UPGRADE}"


def render_downgrade_sql():
    return "\n".join(
        [
            *(predecessor(name) for name in FUNCTIONS),
            BOOTSTRAP_DOWNGRADE,
            SPEC_DOWNGRADE,
        ]
    )


BOOTSTRAP_DOWNGRADE = """
ALTER TABLE @SCHEMA@.factor_controller_launches DROP CONSTRAINT ec_6ffe30774943edd35e0aa6bb;
ALTER TABLE @SCHEMA@.factor_controller_launches ADD CONSTRAINT ec_6ffe30774943edd35e0aa6bb CHECK ((jsonb_typeof(bootstrap)='object' AND bootstrap->>'schema_version' IN ('mineagent.rdagent-operational-bootstrap.v1','mineagent.rdagent-seeded-operational-bootstrap.v1','mineagent.rdagent-provider-operational-bootstrap.v1','mineagent.rdagent-provider-seeded-bootstrap.v1') AND octet_length(bootstrap::text)<=262144) IS TRUE);
"""
BOOTSTRAP_UPGRADE = BOOTSTRAP_DOWNGRADE.replace(
    "'mineagent.rdagent-provider-seeded-bootstrap.v1')",
    "'mineagent.rdagent-provider-seeded-bootstrap.v1','mineagent.rdagent-workflow-operational-bootstrap.v1')",
)

SPEC_DOWNGRADE = """
ALTER TABLE @SCHEMA@.factor_research_specs DROP CONSTRAINT ec_f57efc3d280cd55d4424ea18,
 ADD CONSTRAINT ec_f57efc3d280cd55d4424ea18 CHECK ((jsonb_typeof(config)='object' AND config->>'schema_version' IN ('mineagent.factor-research.v1','mineagent.factor-research.v2')) IS TRUE);
"""
SPEC_UPGRADE = SPEC_DOWNGRADE.replace(
    "'mineagent.factor-research.v2')",
    "'mineagent.factor-research.v2','mineagent.factor-research.v3')",
)

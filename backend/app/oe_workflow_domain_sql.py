"""031 explicit workflow spec/seed/bootstrap semantics over exact030 guards."""

from backend.app.oe_workflow_predecessors import predecessor
from backend.app.rdagent_provider_revision import changed
from packages.openevolve_structured.provider_bootstrap import runtime_profile_sha256
from packages.openevolve_structured.workflow_bootstrap import (
    workflow_runtime_profile_sha256,
)

NAMES = (
    "mineagent_oes_spec_check",
    "mineagent_oes_candidate_check",
    "mineagent_oes_epoch_check",
)
NEW = "'mineagent.oe-structured-provider-run.v2'"


def render_upgrade_sql():
    spec = predecessor(NAMES[0])
    spec = changed(
        spec,
        "c->>'authenticated_request_sha256'",
        "(CASE WHEN c->>'schema_version'="
        + NEW
        + " THEN c->>'workflow_request_sha256' ELSE c->>'authenticated_request_sha256' END)",
    )
    spec = changed(
        spec,
        "END $$;",
        """ IF c->>'schema_version'='mineagent.oe-structured-provider-run.v2' THEN
  IF u.origin IS DISTINCT FROM 'workflow_stage' OR s.request->'workflow_context' IS DISTINCT FROM c->'workflow_context'
  THEN RAISE EXCEPTION 'Workflow declaration origin/context differs'; END IF;
  IF NOT EXISTS(SELECT 1 FROM @SCHEMA@.oe_workflow_submissions q WHERE q.run_id=rid AND q.owner_id=s.owner_id AND q.submission_id=u.id)
  THEN RAISE EXCEPTION 'Workflow declaration attachment missing'; END IF;
  PERFORM @SCHEMA@.mineagent_oe_workflow_attachment(q.id) FROM @SCHEMA@.oe_workflow_submissions q WHERE q.run_id=rid;
 ELSE
  IF u.origin IS DISTINCT FROM 'authenticated_user_submission'
   OR EXISTS(SELECT 1 FROM @SCHEMA@.oe_workflow_submissions q WHERE q.run_id=rid)
  THEN RAISE EXCEPTION 'Legacy user declaration forbids workflow attachment'; END IF;
 END IF;
END $$;""",
    )
    candidate = predecessor(NAMES[1])
    candidate = changed(
        candidate,
        " IF c.ordinal=0 THEN",
        """ IF c.ordinal=0 THEN
  IF c.origin IS DISTINCT FROM (CASE WHEN s.config->>'schema_version'='mineagent.oe-structured-provider-run.v2' THEN 'workflow_seed' ELSE 'authenticated_user_seed' END)
  THEN RAISE EXCEPTION 'Seed origin differs from exact declaration'; END IF;""",
    )
    epoch = predecessor(NAMES[2])
    old = "@SCHEMA@.mineagent_rd_schema(j,rule,coalesce(rule->'$defs','{}'::jsonb),0)"
    epoch = changed(
        epoch,
        old,
        "(CASE WHEN s.config->>'schema_version'="
        + NEW
        + " THEN @SCHEMA@.mineagent_oe_workflow_shape('bootstrap',j) ELSE "
        + old
        + " END)",
    )
    epoch = changed(
        epoch,
        "'" + runtime_profile_sha256() + "'",
        "(CASE WHEN s.config->>'schema_version'="
        + NEW
        + " THEN '"
        + workflow_runtime_profile_sha256()
        + "' ELSE '"
        + runtime_profile_sha256()
        + "' END)",
    )
    epoch = changed(
        epoch,
        "END $$;",
        """ IF s.config->>'schema_version'='mineagent.oe-structured-provider-run.v2' AND
  (j->'workflow_context' IS DISTINCT FROM s.config->'workflow_context'
   OR j->>'workflow_context_sha256' IS DISTINCT FROM @SCHEMA@.mineagent_execution_metadata_sha(s.config->'workflow_context')
   OR j->>'config_sha256' IS DISTINCT FROM s.config_sha256)
 THEN RAISE EXCEPTION 'Workflow bootstrap selected context differs'; END IF;
END $$;""",
    )
    return f"{spec}\n{candidate}\n{epoch}"


def render_downgrade_sql():
    return "\n".join(predecessor(name) for name in NAMES)

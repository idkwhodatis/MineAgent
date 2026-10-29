"""031 assembly: explicit new workflow origin with exact030 rollback semantics."""

import re

from backend.app import oe_workflow_checkpoint_sql as checkpoint
from backend.app import oe_workflow_consistency_sql as consistency
from backend.app import oe_workflow_contract_sql as contract
from backend.app import oe_workflow_dispatch_sql as dispatch
from backend.app import oe_workflow_domain_sql as domain
from backend.app import oe_workflow_graph_sql as graph
from backend.app import oe_workflow_provider_sql as provider
from backend.app import oe_workflow_reverse_sql as reverse
from backend.app.oe_workflow_ddl import origin_sql
from backend.app.oe_workflow_predecessors import predecessor

RESTORE = tuple(
    dict.fromkeys(
        (
            *dispatch.COUNTS,
            *domain.NAMES,
            "mineagent_workflow_payload",
            "mineagent_oes_contract",
            "mineagent_workflow_consistent",
            "mineagent_workflow_checkpoint",
            "mineagent_oes_provider_check",
        )
    )
)


def replaceable(sql):
    return re.sub(r"CREATE (?:OR REPLACE )?FUNCTION", "CREATE OR REPLACE FUNCTION", sql)


def render_upgrade_sql():
    return (
        origin_sql(upgrade=True)
        + "\n"
        + replaceable(
            f"{contract.render_upgrade_sql()}\n{graph.SQL}\n{provider.render_upgrade_sql()}\n{domain.render_upgrade_sql()}\n{dispatch.render_upgrade_sql()}\n{checkpoint.render_upgrade_sql()}\n{consistency.render_upgrade_sql()}\n{reverse.render_upgrade_sql()}"
        )
    )


def render_downgrade_sql():
    guard = r"""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM @SCHEMA@.oe_workflow_submissions)
 OR EXISTS(SELECT 1 FROM @SCHEMA@.research_workflows WHERE plan_canonical::jsonb->>'schema_version'='mineagent.rd-structured-guidance-workflow-plan.v1')
 OR EXISTS(SELECT 1 FROM @SCHEMA@.research_runs WHERE config->>'schema_version'='mineagent.oe-structured-provider-run.v2')
 OR EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_submissions WHERE origin='workflow_stage')
 OR EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_candidates WHERE origin='workflow_seed')
 OR EXISTS(SELECT 1 FROM @SCHEMA@.research_workflow_commands WHERE command_canonical::jsonb->>'schema_version'='mineagent.workflow-oe-claim.v1' OR result_canonical::jsonb->>'schema_version'='mineagent.workflow-oe-claim-receipt.v1')
 OR EXISTS(SELECT 1 FROM @SCHEMA@.research_workflow_checkpoints WHERE terminal_canonical::jsonb->>'schema_version'='mineagent.workflow-oe-checkpoint.v1')
 THEN RAISE EXCEPTION 'Workflow OE history prevents downgrade'; END IF;
END $$;
"""
    restored = replaceable("\n".join(predecessor(name) for name in RESTORE))
    drops = r"""
DROP TABLE @SCHEMA@.oe_workflow_submissions;
DROP FUNCTION @SCHEMA@.mineagent_oe_workflow_check();
DROP FUNCTION @SCHEMA@.mineagent_oe_workflow_before();
DROP FUNCTION @SCHEMA@.mineagent_oe_workflow_checkpoint(uuid,uuid,uuid);
DROP FUNCTION @SCHEMA@.mineagent_oe_workflow_attachment(uuid);
DROP FUNCTION @SCHEMA@.mineagent_oe_workflow_consumption(uuid);
DROP FUNCTION @SCHEMA@.mineagent_oe_workflow_rendered(jsonb,jsonb);
DROP FUNCTION @SCHEMA@.mineagent_oe_workflow_shape(text,jsonb);
DROP FUNCTION @SCHEMA@.mineagent_workflow_payload_before_oe(text,jsonb);
DROP FUNCTION @SCHEMA@.mineagent_oes_contract_before_workflow(text,jsonb);
"""
    return (
        guard
        + reverse.render_downgrade_sql()
        + restored
        + "\n"
        + origin_sql(upgrade=False)
        + "\n"
        + drops
    )

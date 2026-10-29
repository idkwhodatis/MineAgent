"""Explicit030 extension over pinned028 predicates; no historical renderer drift."""

import hashlib

from backend.app import declared_upload_numeric_sql as signed_exit
from backend.app.declared_upload_numeric_schema import (
    contract_sql,
    epoch_sql,
    workflow_claim_sql,
)
from backend.app.declared_upload_sql import BINDINGS
from backend.app.openevolve_revision_ddl import historical
from backend.app.rdagent_provider_revision import changed, function

PREDECESSOR_SHA256 = "dbb61d771730685de3cf8cb2fd7eeafe48e2c7c77fa54d57c19d70df2e68f7c9"


def predecessor(name):
    revision = "029" if name == "mineagent_workflow_payload" else "028"
    expected = (
        "2848ba2747b703965ad8de8048591ac1931854666cda2f1f6dd512976aaf9ed3"
        if revision == "029"
        else PREDECESSOR_SHA256
    )
    sql = historical(revision, "UPGRADE_SQL")
    if hashlib.sha256(sql.encode()).hexdigest() != expected:
        raise ValueError("Exact028 predecessor required")
    return function(sql, name)


def render_upgrade_sql():
    contract = predecessor("mineagent_oes_contract")
    legacy = changed(
        contract, "mineagent_oes_contract(", "mineagent_oes_contract_before_upload("
    )
    wrapper = r"""
CREATE OR REPLACE FUNCTION @SCHEMA@.mineagent_oes_contract(kind text,v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
 IF kind='config' AND v#>>'{fixed_inputs,market_qualification}'='user_declaration_unverified' THEN
  IF v#>>'{fixed_inputs,schema_version}' IS DISTINCT FROM 'mineagent.oe-structured-retained-inputs.v2' THEN RETURN false; END IF;
  RETURN @SCHEMA@.mineagent_oes_contract_v030_shape(kind,
    jsonb_set(jsonb_set(v,'{fixed_inputs,market_qualification}','"synthetic_by_construction"'::jsonb),
      '{fixed_inputs,schema_version}','"mineagent.oe-structured-retained-inputs.v1"'::jsonb)) IS TRUE;
 END IF;
 RETURN @SCHEMA@.mineagent_oes_contract_v030_shape(kind,v) IS TRUE;
END $$;
"""
    spec = changed(
        predecessor("mineagent_oes_spec_check"),
        "END $$;",
        " PERFORM @SCHEMA@.mineagent_upload_oe_qualification(s.owner_id,s.dataset_id,c#>>'{fixed_inputs,market_qualification}');\nEND $$;",
    )
    return (
        BINDINGS
        + legacy
        + contract_sql(contract)[0]
        + wrapper
        + spec
        + epoch_sql(predecessor("mineagent_oes_epoch_check"))[0]
        + workflow_claim_sql(predecessor("mineagent_workflow_payload"))[0]
        + signed_exit.render_upgrade_sql()
        + r"""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM @SCHEMA@.research_workflow_commands
  WHERE command_canonical::jsonb->>'schema_version'='mineagent.workflow-rd-claim.v1'
  AND @SCHEMA@.mineagent_workflow_payload('claim',command_canonical::jsonb) IS NOT TRUE)
 THEN RAISE EXCEPTION 'Retained workflow claim prevents numeric contract correction'; END IF;
END $$;
"""
    )


def render_downgrade_sql():
    guard = r"""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM @SCHEMA@.declared_upload_baskets)
 OR EXISTS(SELECT 1 FROM @SCHEMA@.declared_upload_basket_members)
 OR EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_specs WHERE
  @SCHEMA@.mineagent_oes_contract_before_upload('config',config) IS NOT TRUE
  OR @SCHEMA@.mineagent_oes_contract_before_upload('request',request) IS NOT TRUE)
 OR EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_candidates WHERE
  @SCHEMA@.mineagent_oes_contract_before_upload('candidate',candidate_canonical::jsonb) IS NOT TRUE)
 OR EXISTS(SELECT 1 FROM @SCHEMA@.research_process_receipts WHERE kind='rejection' AND payload->'child_exit_code' < '0'::jsonb)
 THEN RAISE EXCEPTION 'Declared upload history prevents downgrade'; END IF;
END $$;
"""
    restored = (
        predecessor("mineagent_oes_contract")
        + predecessor("mineagent_oes_spec_check")
        + predecessor("mineagent_oes_epoch_check")
        + predecessor("mineagent_workflow_payload")
        + signed_exit.render_downgrade_sql()
    )
    drops = r"""
DROP TRIGGER upload030_dataset_complete ON @SCHEMA@.dataset_versions;
DROP FUNCTION @SCHEMA@.mineagent_upload_dataset_deferred();
DROP TRIGGER upload030_dataset_immutable ON @SCHEMA@.dataset_versions;
DROP TABLE @SCHEMA@.declared_upload_basket_members;
DROP TABLE @SCHEMA@.declared_upload_baskets;
DROP FUNCTION @SCHEMA@.mineagent_upload_dataset_immutable();
DROP FUNCTION @SCHEMA@.mineagent_upload_deferred();
DROP FUNCTION @SCHEMA@.mineagent_upload_immutable();
DROP FUNCTION @SCHEMA@.mineagent_upload_oe_qualification(uuid,uuid,text);
DROP FUNCTION @SCHEMA@.mineagent_upload_basket_check(uuid);
DROP FUNCTION @SCHEMA@.mineagent_oes_contract_before_upload(text,jsonb);
DROP FUNCTION @SCHEMA@.mineagent_oes_contract_v030_shape(text,jsonb);
"""
    return guard + restored + drops

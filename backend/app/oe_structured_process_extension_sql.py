"""Exact027 profile/receipt CHECK successor plus typed extension guard.

Adding an enum spelling is not permission for old units to use it. The new
receipt trigger binds each spelling to its one supported profile and payload;
existing append fencing and deferred physical/accounting predicates still run.
"""

from backend.app.oe_structured_shared_sql import predecessor
from backend.app.openevolve_revision_ddl import _check
from backend.app.rdagent_provider_revision import literal

PROFILE = """(schema_version='mineagent.process-admission.v2'
 AND role='openevolve_orchestrator' AND profile='mineagent.oe-structured-orchestrator.v1'
 AND ipc_policy='host_ticket' AND ticket_sha256 ~ '^[a-f0-9]{64}$')"""
KINDS = "('launch_intent','launch_identity','controller_child','stop','extraction','controller_completion','rejection')"

SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_oes_receipt_extension() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE a record; r record;
BEGIN
 IF NEW.kind NOT IN ('controller_completion','rejection') THEN RETURN NEW; END IF;
 SELECT * INTO a FROM @SCHEMA@.research_process_admissions WHERE id=NEW.admission_id;
 SELECT * INTO r FROM @SCHEMA@.research_runs WHERE id=a.run_id;
 IF (NEW.owner_id,NEW.run_id,NEW.attempt_id,NEW.generation) IS DISTINCT FROM
 (a.owner_id,a.run_id,a.attempt_id,a.generation)
 OR a.schema_version IS DISTINCT FROM 'mineagent.process-admission.v2'
 THEN RAISE EXCEPTION 'Extended process receipt scope differs'; END IF;
 IF NEW.kind='controller_completion' THEN
  IF (a.profile,a.role,a.ipc_policy) IS DISTINCT FROM
  ('mineagent.oe-structured-orchestrator.v1','openevolve_orchestrator','host_ticket')
  OR r.config->>'schema_version' IS DISTINCT FROM 'mineagent.oe-structured-provider-run.v1'
  OR @SCHEMA@.mineagent_oes_controller_payload(NEW.kind,NEW.payload) IS NOT TRUE
  THEN RAISE EXCEPTION 'Completion cannot extend an old or unrelated process'; END IF;
 ELSE
  IF (a.profile,a.role,a.ipc_policy) IS DISTINCT FROM
  ('mineagent.factor-prefix-candidate.v2','factor_cutoff_candidate','none')
  OR @SCHEMA@.mineagent_numeric_rejection_payload(NEW.kind,NEW.payload) IS NOT TRUE
  THEN RAISE EXCEPTION 'Rejection requires exact measured numeric candidate'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER oes027_receipt_extension BEFORE INSERT ON @SCHEMA@.research_process_receipts
FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_oes_receipt_extension();
"""


def render_upgrade_sql():
    return f"""ALTER TABLE @SCHEMA@.research_process_admissions DROP CONSTRAINT ck_process_v2_unregistered,
 ADD CONSTRAINT ck_process_v2_unregistered CHECK (({_check(predecessor("027c"), "ck_process_v2_unregistered")} OR {PROFILE}) IS TRUE);
ALTER TABLE @SCHEMA@.research_process_receipts DROP CONSTRAINT ck_research_process_receipt_kind,
 ADD CONSTRAINT ck_research_process_receipt_kind CHECK (kind IN {KINDS});
{SQL}"""


def render_downgrade_sql():
    # Outer migration first refuses every retained027 extension/domain record.
    old = _check(literal("020_rd_controller.py"), "ck_research_process_receipt_kind")
    return f"""DROP TRIGGER oes027_receipt_extension ON @SCHEMA@.research_process_receipts;
DROP FUNCTION @SCHEMA@.mineagent_oes_receipt_extension();
ALTER TABLE @SCHEMA@.research_process_receipts DROP CONSTRAINT ck_research_process_receipt_kind,
 ADD CONSTRAINT ck_research_process_receipt_kind CHECK {old};
ALTER TABLE @SCHEMA@.research_process_admissions DROP CONSTRAINT ck_process_v2_unregistered,
 ADD CONSTRAINT ck_process_v2_unregistered CHECK {_check(predecessor("027c"), "ck_process_v2_unregistered")};"""

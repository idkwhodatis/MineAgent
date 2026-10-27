"""021 explicit first-completed-generation delta; old legacy valid path stays closed.

SQL proves retained relational/accounting identity, not physical file reads or
SO_PEERCRED. Actual host artifact construction plus cold byte validation remain
required. The new snapshot/result predicates are installed by021's owner.
"""

KINDS = (
    "factor_generation_proposal",
    "factor_generation_feedback",
    "factor_adoption_receipt",
)
TABLES = (
    "factor_generation_outcomes",
    "factor_execution_evaluation_outcomes",
    "factor_generation_observations",
    "factor_generation_boundaries",
    "research_objects",
    "research_object_publications",
    "research_budget_reservations",
    "research_budget_settlements",
)

SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_rd_execution_generation_actor() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
BEGIN
 IF NEW.outcome<>'accepted' OR NOT EXISTS(
 SELECT 1 FROM factor_execution_evaluation_outcomes ev
 JOIN factor_generation_boundaries b ON b.id=ev.boundary_id
 JOIN factor_controller_launches cl ON cl.id=ev.controller_launch_id
 JOIN research_process_admissions p ON p.id=cl.admission_id
 JOIN research_process_states state ON state.admission_id=p.id
 WHERE (ev.owner_id,ev.run_id,ev.attempt_id,ev.generation,ev.generation_start_id)=
       (NEW.owner_id,NEW.run_id,NEW.attempt_id,NEW.generation,NEW.start_id)
 AND b.start_id=NEW.start_id AND b.boundary='returned'
 AND p.profile='mineagent.rdagent-orchestrator.v2' AND state.status='running'
 AND EXISTS(SELECT 1 FROM research_process_receipts r WHERE r.admission_id=p.id AND r.kind='controller_child'))
 THEN RAISE EXCEPTION 'Accepted execution requires current protected actor and actual snapshot result'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_rd_execution_generation_consistent() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE owner_uuid uuid; g record; st record; src record; ev record; b record; o record;
 proposal record; feedback record; adoption record; held record; charged record;
BEGIN
 owner_uuid:=CASE WHEN TG_OP='DELETE' THEN OLD.owner_id ELSE NEW.owner_id END;
 IF EXISTS(SELECT 1 FROM research_object_publications p JOIN research_runs r ON r.id=p.run_id
 WHERE p.owner_id=owner_uuid AND p.object_kind IN ('factor_generation_proposal','factor_generation_feedback','factor_adoption_receipt')
 AND (r.owner_id<>p.owner_id OR r.purpose<>'factor_research' OR r.engine<>'rdagent' OR p.size_bytes>65536))
 THEN RAISE EXCEPTION 'Typed execution objects require exact RD publication scope'; END IF;
 FOR g IN SELECT * FROM factor_generation_outcomes WHERE owner_id=owner_uuid LOOP
  SELECT * INTO st FROM factor_generation_starts WHERE id=g.start_id;
  SELECT * INTO src FROM factor_generation_sources WHERE start_id=st.id;
  SELECT * INTO ev FROM factor_execution_evaluation_outcomes WHERE generation_start_id=st.id;
  SELECT * INTO b FROM factor_generation_boundaries WHERE id=ev.boundary_id;
  SELECT * INTO o FROM factor_generation_observations WHERE id=b.observation_id;
  SELECT * INTO proposal FROM research_objects WHERE id=g.proposal_object_id;
  SELECT * INTO feedback FROM research_objects WHERE id=g.feedback_object_id;
  SELECT * INTO adoption FROM research_objects WHERE id=g.adoption_object_id;
  SELECT * INTO held FROM research_budget_reservations WHERE id=st.mutation_reservation_id;
  SELECT * INTO charged FROM research_budget_settlements WHERE reservation_id=held.id;
  IF st.id IS NULL OR src.id IS NULL OR ev.id IS NULL OR b.id IS NULL OR o.id IS NULL
   OR proposal.id IS NULL OR feedback.id IS NULL OR adoption.id IS NULL OR charged.id IS NULL
   OR (SELECT count(*) FROM factor_execution_evaluation_outcomes WHERE generation_start_id=st.id)<>1
   OR g.outcome<>'accepted' OR g.logical_step<>0 OR g.error_code IS NOT NULL
   OR (g.owner_id,g.run_id,g.attempt_id,g.generation) IS DISTINCT FROM (ev.owner_id,ev.run_id,ev.attempt_id,ev.generation)
   OR (g.evaluated_source_id,g.adopted_source_id,g.continuation_source_id) IS DISTINCT FROM (src.id,src.id,src.id)
   OR (g.factor_version_id,g.adopted_factor_version_id,g.continuation_factor_version_id) IS DISTINCT FROM (src.factor_version_id,src.factor_version_id,src.factor_version_id)
   OR (b.start_id,b.controller_launch_id,b.logical_step,b.boundary) IS DISTINCT FROM (st.id,ev.controller_launch_id,0,'returned'::varchar)
   OR b.next_logical_step IS NOT NULL OR o.controller_launch_id<>ev.controller_launch_id
   OR o.decision->>'adopted_source_id'<>src.id::text OR o.decision->>'adopted_new'<>'true'
   OR (proposal.owner_id,proposal.object_kind) IS DISTINCT FROM (g.owner_id,'factor_generation_proposal'::varchar)
   OR (feedback.owner_id,feedback.object_kind,feedback.id,feedback.sha256) IS DISTINCT FROM (g.owner_id,'factor_generation_feedback'::varchar,ev.feedback_object_id,ev.feedback_sha256)
   OR (adoption.owner_id,adoption.object_kind,adoption.sha256) IS DISTINCT FROM (g.owner_id,'factor_adoption_receipt'::varchar,g.adoption_sha256)
   OR g.adoption_object_kind<>'factor_adoption_receipt'
   OR greatest(proposal.size_bytes,feedback.size_bytes,adoption.size_bytes)>65536
   OR held.purpose<>'engine_mutation' OR held.state<>'settled'
   OR charged.outcome<>'conservative_unknown' OR charged.charge IS DISTINCT FROM held.amounts
   OR (charged.owner_id,charged.run_id,charged.attempt_id,charged.generation) IS DISTINCT FROM (g.owner_id,g.run_id,g.attempt_id,g.generation)
  THEN RAISE EXCEPTION 'Accepted generation lacks exact typed snapshot/adoption/accounting closure'; END IF;
 END LOOP;
 RETURN NULL;
END $$;
"""


def _kind_checks(expanded):
    kinds = (
        "model_request",
        "model_response",
        "process_receipt",
        "factor_response_assembly",
    ) + (KINDS if expanded else ())
    literal = ",".join("'" + kind + "'" for kind in kinds)
    return "\n".join(
        f"ALTER TABLE @SCHEMA@.{table} DROP CONSTRAINT ck_{table}_versioned_kind, ADD CONSTRAINT ck_{table}_versioned_kind CHECK (object_kind IN ({literal}));"
        for table in ("research_objects", "research_object_publications")
    )


def render_upgrade_sql():
    sql = _kind_checks(True) + "\n" + SQL
    sql += "\nDROP TRIGGER rd_source_phase_closed ON @SCHEMA@.factor_generation_outcomes;\nCREATE TRIGGER rd_execution_generation_actor BEFORE INSERT ON @SCHEMA@.factor_generation_outcomes FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_execution_generation_actor();"
    for table in TABLES:
        sql += f"\nCREATE CONSTRAINT TRIGGER rd_execution_generation_closure AFTER INSERT OR UPDATE OR DELETE ON @SCHEMA@.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_execution_generation_consistent();"
    return sql


def render_downgrade_sql():
    sql = """DO $$ BEGIN IF EXISTS(SELECT 1 FROM @SCHEMA@.factor_generation_outcomes)
 OR EXISTS(SELECT 1 FROM @SCHEMA@.research_objects WHERE object_kind IN ('factor_generation_proposal','factor_generation_feedback','factor_adoption_receipt'))
 OR EXISTS(SELECT 1 FROM @SCHEMA@.research_object_publications WHERE object_kind IN ('factor_generation_proposal','factor_generation_feedback','factor_adoption_receipt'))
 THEN RAISE EXCEPTION 'Execution generation evidence prevents downgrade'; END IF; END $$;"""
    for table in TABLES:
        sql += f"\nDROP TRIGGER rd_execution_generation_closure ON @SCHEMA@.{table};"
    sql += "\nDROP TRIGGER rd_execution_generation_actor ON @SCHEMA@.factor_generation_outcomes;\nCREATE TRIGGER rd_source_phase_closed BEFORE INSERT ON @SCHEMA@.factor_generation_outcomes FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_source_only_phase();\nDROP FUNCTION @SCHEMA@.mineagent_rd_execution_generation_consistent();\nDROP FUNCTION @SCHEMA@.mineagent_rd_execution_generation_actor();"
    return sql + "\n" + _kind_checks(False)

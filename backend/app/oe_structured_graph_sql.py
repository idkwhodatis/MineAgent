"""027 bounded run graph predicates; installation remains the assembler's job.

Existing prefixes are checked without demanding future rows. Only a succeeded
run requires the complete, positively stopped graph. This SQL does not inspect
physical files or replace live peer/retained-byte validation.
"""

CHECKS = (
    ("oe_structured_epochs", "controller"),
    ("oe_structured_control_states", "control"),
    ("oe_structured_control_states", "population"),
    ("oe_structured_sampling_intents", "sampling"),
    ("oe_structured_worker_placements", "placement"),
    ("oe_structured_iteration_intents", "iteration"),
    ("oe_structured_provider_transports", "provider"),
    ("oe_structured_candidates", "candidate"),
    ("oe_structured_executions", "execution"),
    ("oe_structured_engine_outcomes", "outcome"),
    ("oe_structured_worker_outcomes", "worker"),
    ("oe_structured_terminal_applications", "application"),
    ("oe_structured_checkpoint_intents", "checkpoint"),
)

NATIVE = r"""
CREATE FUNCTION @SCHEMA@.mineagent_oes_native_check(aid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE a record; e record; s record; li record; b record; expected jsonb;
BEGIN
 SELECT * INTO a FROM @SCHEMA@.research_process_admissions WHERE id=aid;
 SELECT * INTO e FROM @SCHEMA@.oe_structured_executions WHERE id=a.logical_operation_id;
 IF e.id IS NULL THEN RAISE EXCEPTION 'Structured native admission has no execution'; END IF;
 PERFORM @SCHEMA@.mineagent_oes_execution_check(e.id);
 SELECT * INTO s FROM @SCHEMA@.oe_structured_specs WHERE run_id=e.run_id;
 SELECT * INTO li FROM @SCHEMA@.research_process_receipts WHERE admission_id=a.id AND kind='launch_intent';
 SELECT * INTO b FROM @SCHEMA@.research_budget_reservations WHERE id=a.reservation_id;
 expected:=jsonb_build_object('schema_version','mineagent.structured-factor-engine-input-binding.v1',
  'execution_id',e.id,'structured_strategy_version_id',e.program_version_id,'snapshot_id',e.snapshot_id,
  'strategy_input_sha256',e.strategy_input_sha256,'strategy_input_size_bytes',e.strategy_input_size_bytes,
  'market_sha256',e.market_sha256,'market_size_bytes',e.market_size_bytes);
 IF (a.owner_id,a.run_id,a.attempt_id,a.generation,a.role,a.profile,a.ipc_policy,a.image_id,a.launch_ordinal) IS DISTINCT FROM
 (e.owner_id,e.run_id,e.attempt_id,e.generation,'native_evaluator','mineagent.factor-strategy-engine.v1','none',s.config->'runtime'->>'engine_image_id',1)
 OR a.ticket_sha256 IS NOT NULL
 OR @SCHEMA@.mineagent_structured_payload('launch_intent',li.payload) IS NOT TRUE
 OR li.payload->'inputs' IS DISTINCT FROM expected
 OR a.input_sha256 IS DISTINCT FROM @SCHEMA@.mineagent_execution_metadata_sha(expected)
 OR li.payload_sha256 IS DISTINCT FROM @SCHEMA@.mineagent_execution_metadata_sha(li.payload)
 OR li.payload->'admission' IS DISTINCT FROM jsonb_build_object('schema_version',a.schema_version,'id',a.id,'owner_id',a.owner_id,
  'run_id',a.run_id,'attempt_id',a.attempt_id,'generation',a.generation,'role',a.role,'logical_operation_id',a.logical_operation_id,
  'launch_ordinal',a.launch_ordinal,'profile',a.profile,'input_sha256',a.input_sha256,'reservation_id',a.reservation_id,'image_id',a.image_id,
  'ipc',jsonb_build_object('ipc_policy','none','ticket_sha256',null))
 OR (b.owner_id,b.run_id,b.attempt_id,b.generation,b.purpose,b.logical_key) IS DISTINCT FROM
 (e.owner_id,e.run_id,e.attempt_id,e.generation,'process_unit','structured-factor-execution:'||e.id::text)
 OR b.amounts IS DISTINCT FROM jsonb_build_object('calls',0,'tokens',0,'cost_microunits',0,'mutation_slots',0,
  'evaluator_executions',1,'wall_milliseconds',90000,'storage_bytes',268500992,'process_slots',1)
 OR (SELECT count(*) FROM @SCHEMA@.research_process_admissions WHERE logical_operation_id=e.id)<>1
 THEN RAISE EXCEPTION 'Structured native admission/input/hold differs'; END IF;
END $$;
"""

GRAPH = r"""
CREATE FUNCTION @SCHEMA@.mineagent_oes_graph_check(rid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE r record; s record; e record; item record; maximum integer; n bigint;
BEGIN
 SELECT * INTO r FROM @SCHEMA@.research_runs WHERE id=rid;
 SELECT * INTO s FROM @SCHEMA@.oe_structured_specs WHERE run_id=rid;
 IF s.run_id IS NULL THEN
  IF r.config->>'schema_version'='mineagent.oe-structured-provider-run.v1'
  THEN RAISE EXCEPTION 'Structured provider run has no declaration'; END IF;
  RETURN;
 END IF;
 PERFORM @SCHEMA@.mineagent_oes_spec_check(rid);
 maximum:=(s.config->'search'->>'maximum_mutation_slots')::integer;
 IF (SELECT count(*) FROM @SCHEMA@.oe_structured_epochs WHERE run_id=rid)>1
 THEN RAISE EXCEPTION 'Structured initial-only epoch'; END IF;
 @CHECKS@
 -- Shared rows must have actual reverse consumers even during pending prefixes.
 FOR item IN SELECT * FROM @SCHEMA@.research_process_admissions WHERE run_id=rid LOOP
  IF item.profile='mineagent.oe-structured-orchestrator.v1' THEN
   IF (SELECT count(*) FROM @SCHEMA@.oe_structured_epochs WHERE controller_admission_id=item.id)<>1
   THEN RAISE EXCEPTION 'Orphan structured controller admission'; END IF;
  ELSE
   PERFORM @SCHEMA@.mineagent_oes_native_check(item.id);
  END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM @SCHEMA@.research_model_operations op WHERE op.run_id=rid AND
  NOT EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_provider_transports m WHERE m.operation_id=op.id))
 OR EXISTS(SELECT 1 FROM @SCHEMA@.research_model_transports tr WHERE tr.run_id=rid AND
  NOT EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_provider_transports m WHERE m.transport_id=tr.id))
 OR EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_provider_exchanges x WHERE x.run_id=rid AND
  NOT EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_provider_transports m WHERE m.id=x.provider_transport_id))
 THEN RAISE EXCEPTION 'Orphan structured model operation/transport/exchange'; END IF;
 IF r.status IS DISTINCT FROM 'succeeded' THEN RETURN; END IF;
 SELECT * INTO e FROM @SCHEMA@.oe_structured_epochs WHERE run_id=rid;
 IF e.id IS NULL OR @SCHEMA@.mineagent_oes_controller_completed(e.id) IS NOT TRUE
 OR (SELECT count(*) FROM @SCHEMA@.oe_structured_control_states WHERE epoch_id=e.id)<>2*maximum+1
 OR (SELECT count(*) FROM @SCHEMA@.oe_structured_sampling_intents WHERE epoch_id=e.id)<>maximum
 OR (SELECT count(*) FROM @SCHEMA@.oe_structured_iteration_intents WHERE epoch_id=e.id)<>maximum
 OR (SELECT count(*) FROM @SCHEMA@.oe_structured_worker_outcomes WHERE epoch_id=e.id)<>maximum
 OR (SELECT count(*) FROM @SCHEMA@.oe_structured_terminal_applications WHERE epoch_id=e.id)<>maximum+1
 OR (SELECT count(*) FROM @SCHEMA@.oe_structured_checkpoint_intents WHERE epoch_id=e.id)<>maximum+2
 OR (SELECT count(*) FROM @SCHEMA@.oe_structured_checkpoint_publications WHERE epoch_id=e.id)<>maximum+2
 THEN RAISE EXCEPTION 'Structured success lacks final stopped frontier'; END IF;
 -- Every declared candidate has an actual eligible result. Failed physical work
 -- cannot be disguised as a native worker-error terminal and silently omitted.
 IF NOT EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_candidates WHERE epoch_id=e.id AND ordinal=0)
 OR EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_candidates c WHERE c.epoch_id=e.id AND
  (NOT EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_engine_outcomes o WHERE o.candidate_id=c.id
   AND o.measurement_canonical::jsonb->'eligible'='true'::jsonb)
   OR (c.ordinal>0 AND NOT EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_worker_outcomes w
    WHERE w.epoch_id=e.id AND w.status='evaluated' AND w.candidate_id=c.id))))
 OR EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_provider_transports m WHERE m.run_id=rid AND
  NOT EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_provider_exchanges x JOIN @SCHEMA@.oe_structured_candidates c
   ON c.provider_exchange_id=x.id WHERE x.provider_transport_id=m.id AND c.epoch_id=e.id))
 OR EXISTS(SELECT 1 FROM @SCHEMA@.research_process_admissions a WHERE a.run_id=rid AND a.id<>e.controller_admission_id AND
  NOT EXISTS(SELECT 1 FROM @SCHEMA@.oe_structured_engine_outcomes o WHERE o.admission_id=a.id))
 THEN RAISE EXCEPTION 'Structured success has unfinished candidate/model/native obligations'; END IF;
 SELECT count(*) INTO n FROM @SCHEMA@.research_budget_reservations WHERE run_id=rid;
 IF n>256 OR EXISTS(SELECT 1 FROM @SCHEMA@.research_budget_reservations b WHERE b.run_id=rid AND
  (b.state IS DISTINCT FROM 'settled' OR NOT EXISTS(SELECT 1 FROM @SCHEMA@.research_budget_settlements paid
   WHERE paid.reservation_id=b.id AND (paid.owner_id,paid.run_id,paid.attempt_id,paid.generation)=(b.owner_id,b.run_id,b.attempt_id,b.generation)
   AND paid.charge=b.amounts AND paid.outcome='conservative_unknown')))
 THEN RAISE EXCEPTION 'Structured success has unsettled or refunded obligations'; END IF;
END $$;
"""


def render_upgrade_sql():
    checks = []
    for table, predicate in CHECKS:
        checks.append(f"""SELECT count(*) INTO n FROM @SCHEMA@.{table} WHERE run_id=rid;
 IF n>256 THEN RAISE EXCEPTION 'Structured graph bound'; END IF;
 FOR item IN SELECT id FROM @SCHEMA@.{table} WHERE run_id=rid LOOP
  PERFORM @SCHEMA@.mineagent_oes_{predicate}_check(item.id);
 END LOOP;""")
    return NATIVE + GRAPH.replace("@CHECKS@", "\n ".join(checks))


def render_downgrade_sql():
    return """DROP FUNCTION @SCHEMA@.mineagent_oes_graph_check(uuid);
DROP FUNCTION @SCHEMA@.mineagent_oes_native_check(uuid);"""

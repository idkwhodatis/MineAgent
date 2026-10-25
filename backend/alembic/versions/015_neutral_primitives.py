"""Closed neutral durable primitives; no new engine admission is enabled."""

from alembic import op

revision = "015_neutral_primitives"
down_revision = "014_run_identity"
branch_labels = None
depends_on = None

TABLES = (
    "research_objects",
    "research_budget_controls",
    "research_budget_reservations",
    "research_operation_contexts",
    "research_budget_settlements",
    "research_context_aborts",
    "research_model_operations",
    "research_object_publications",
    "research_process_admissions",
    "research_model_transports",
    "research_object_outcomes",
    "research_process_receipts",
    "research_process_states",
    "research_model_outcomes",
)

TABLE_DDL = (
    "\nCREATE TABLE research_objects (\n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\tobject_kind VARCHAR(40) NOT NULL, \n\tsha256 VARCHAR(64) NOT NULL, \n\tsize_bytes BIGINT NOT NULL, \n\tmedia_type VARCHAR(100) NOT NULL, \n\tstorage_key VARCHAR(300) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (owner_id, id), \n\tUNIQUE (owner_id, id, object_kind, sha256), \n\tUNIQUE (owner_id, object_kind, sha256), \n\tUNIQUE (storage_key), \n\tCHECK (object_kind IN ('model_request','model_response','process_receipt')), \n\tCHECK (sha256 ~ '^[0-9a-f]{64}$'), \n\tCHECK (size_bytes BETWEEN 1 AND 1048576), \n\tCHECK (media_type = 'application/json'), \n\tCHECK (storage_key = 'research-objects/' || owner_id::text || '/by-kind/' || object_kind || '/' || sha256 || '.json'), \n\tFOREIGN KEY(owner_id) REFERENCES users (id)\n)\n\n",
    "\nCREATE TABLE research_budget_controls (\n\tlimits JSONB NOT NULL, \n\tused JSONB NOT NULL, \n\treserved JSONB NOT NULL, \n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(owner_id, run_id) REFERENCES research_runs (owner_id, id), \n\tUNIQUE (owner_id, run_id), \n\tCONSTRAINT ck_neutral_budget_vectors CHECK (mineagent_budget_vector(limits) AND mineagent_budget_vector(used) AND mineagent_budget_vector(reserved))\n)\n\n",
    "\nCREATE TABLE research_budget_reservations (\n\tpurpose VARCHAR(32) NOT NULL, \n\tlogical_key VARCHAR(128) NOT NULL, \n\tamounts JSONB NOT NULL, \n\tstate VARCHAR(20) NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tgeneration INTEGER NOT NULL, \n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(owner_id, run_id) REFERENCES research_runs (owner_id, id), \n\tFOREIGN KEY(run_id, generation, attempt_id) REFERENCES execution_attempts (run_id, generation, id), \n\tUNIQUE (owner_id, run_id, attempt_id, generation, id), \n\tCHECK (generation > 0), \n\tFOREIGN KEY(owner_id, run_id) REFERENCES research_budget_controls (owner_id, run_id), \n\tUNIQUE (owner_id, run_id, attempt_id, generation, purpose, logical_key), \n\tCHECK (purpose IN ('model_transport','process_unit','object_storage')), \n\tCHECK (state IN ('reserved','uncertain','settled')), \n\tCONSTRAINT ck_neutral_reservation_vector CHECK (mineagent_budget_vector(amounts))\n)\n\n",
    "\nCREATE TABLE research_operation_contexts (\n\tcontext JSONB NOT NULL, \n\tcontext_sha256 VARCHAR(64) NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tgeneration INTEGER NOT NULL, \n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(owner_id, run_id) REFERENCES research_runs (owner_id, id), \n\tFOREIGN KEY(run_id, generation, attempt_id) REFERENCES execution_attempts (run_id, generation, id), \n\tUNIQUE (owner_id, run_id, attempt_id, generation, id), \n\tCHECK (generation > 0), \n\tUNIQUE (owner_id, run_id, attempt_id, generation, context_sha256), \n\tCHECK (context_sha256 ~ '^[0-9a-f]{64}$'), \n\tCHECK ((jsonb_typeof(context) = 'object' AND context ?& ARRAY['schema_version','scenario','ordinal'] AND context - ARRAY['schema_version','scenario','ordinal'] = '{}'::jsonb AND context->>'schema_version' = 'mineagent.neutral-fixture-context.v1' AND context->>'scenario' IN ('chat','embedding','retry','continuation','cancellation') AND jsonb_typeof(context->'ordinal') = 'number' AND context->>'ordinal' ~ '^[0-9]+$' AND (context->>'ordinal')::numeric BETWEEN 0 AND 1000000) IS TRUE)\n)\n\n",
    "\nCREATE TABLE research_budget_settlements (\n\treservation_id UUID NOT NULL, \n\tcharge JSONB NOT NULL, \n\toutcome VARCHAR(32) NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tgeneration INTEGER NOT NULL, \n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(owner_id, run_id) REFERENCES research_runs (owner_id, id), \n\tFOREIGN KEY(run_id, generation, attempt_id) REFERENCES execution_attempts (run_id, generation, id), \n\tUNIQUE (owner_id, run_id, attempt_id, generation, id), \n\tCHECK (generation > 0), \n\tFOREIGN KEY(owner_id, run_id, attempt_id, generation, reservation_id) REFERENCES research_budget_reservations (owner_id, run_id, attempt_id, generation, id), \n\tCHECK (outcome IN ('measured','not_submitted','conservative_unknown')), \n\tCONSTRAINT ck_neutral_settlement_vector CHECK (mineagent_budget_vector(charge)), \n\tUNIQUE (reservation_id)\n)\n\n",
    "\nCREATE TABLE research_context_aborts (\n\tcontext_id UUID NOT NULL, \n\treason VARCHAR(32) NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tgeneration INTEGER NOT NULL, \n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(owner_id, run_id) REFERENCES research_runs (owner_id, id), \n\tFOREIGN KEY(run_id, generation, attempt_id) REFERENCES execution_attempts (run_id, generation, id), \n\tUNIQUE (owner_id, run_id, attempt_id, generation, id), \n\tCHECK (generation > 0), \n\tFOREIGN KEY(owner_id, run_id, attempt_id, generation, context_id) REFERENCES research_operation_contexts (owner_id, run_id, attempt_id, generation, id), \n\tCHECK (reason IN ('cancelled','budget_exhausted','publication_failed','request_drift','uncertain_transport')), \n\tUNIQUE (context_id)\n)\n\n",
    "\nCREATE TABLE research_model_operations (\n\tcontext_id UUID NOT NULL, \n\tpurpose VARCHAR(20) NOT NULL, \n\tordinal INTEGER NOT NULL, \n\trequest JSONB NOT NULL, \n\trequest_object_id UUID NOT NULL, \n\trequest_object_kind VARCHAR(32) NOT NULL, \n\trequest_sha256 VARCHAR(64) NOT NULL, \n\tstate VARCHAR(20) NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tgeneration INTEGER NOT NULL, \n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(owner_id, run_id) REFERENCES research_runs (owner_id, id), \n\tFOREIGN KEY(run_id, generation, attempt_id) REFERENCES execution_attempts (run_id, generation, id), \n\tUNIQUE (owner_id, run_id, attempt_id, generation, id), \n\tCHECK (generation > 0), \n\tFOREIGN KEY(owner_id, run_id, attempt_id, generation, context_id) REFERENCES research_operation_contexts (owner_id, run_id, attempt_id, generation, id), \n\tFOREIGN KEY(owner_id, request_object_id, request_object_kind, request_sha256) REFERENCES research_objects (owner_id, id, object_kind, sha256), \n\tUNIQUE (context_id, purpose, ordinal), \n\tCHECK (purpose IN ('chat','embedding','continuation')), \n\tCONSTRAINT ck_neutral_operation_request_purpose CHECK (((purpose = 'embedding') = (request->>'kind' = 'embedding')) IS TRUE), \n\tCHECK (ordinal >= 0 AND ordinal <= 1000000), \n\tCHECK (request_object_kind = 'model_request'), \n\tCHECK (request_sha256 ~ '^[0-9a-f]{64}$'), \n\tCHECK (state IN ('open','completed','unknown','aborted')), \n\tCHECK (mineagent_broker_request(request) IS TRUE)\n)\n\n",
    "\nCREATE TABLE research_object_publications (\n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tgeneration INTEGER NOT NULL, \n\tbudget_reservation_id UUID NOT NULL, \n\tobject_kind VARCHAR(40) NOT NULL, \n\tsha256 VARCHAR(64) NOT NULL, \n\tsize_bytes BIGINT NOT NULL, \n\tstorage_key VARCHAR(300) NOT NULL, \n\tpeak_bytes BIGINT NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (owner_id, run_id, attempt_id, generation, id), \n\tUNIQUE (owner_id, id, object_kind, sha256), \n\tUNIQUE (budget_reservation_id), \n\tFOREIGN KEY(owner_id, run_id, attempt_id, generation, budget_reservation_id) REFERENCES research_budget_reservations (owner_id, run_id, attempt_id, generation, id), \n\tCHECK (generation > 0), \n\tCHECK (object_kind IN ('model_request','model_response','process_receipt')), \n\tCHECK (sha256 ~ '^[0-9a-f]{64}$'), \n\tCHECK (size_bytes BETWEEN 1 AND 1048576), \n\tCHECK (peak_bytes = size_bytes * 2 + 8192), \n\tCHECK (storage_key = 'research-objects/' || owner_id::text || '/by-kind/' || object_kind || '/' || sha256 || '.json')\n)\n\n",
    "\nCREATE TABLE research_process_admissions (\n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tgeneration INTEGER NOT NULL, \n\trole VARCHAR(40) NOT NULL, \n\tlogical_operation_id UUID NOT NULL, \n\tlaunch_ordinal INTEGER NOT NULL, \n\tprofile VARCHAR(60) NOT NULL, \n\tinput_sha256 VARCHAR(64) NOT NULL, \n\treservation_id UUID NOT NULL, \n\timage_id VARCHAR(71) NOT NULL, \n\tticket_sha256 VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (owner_id, run_id, attempt_id, generation, id), \n\tCONSTRAINT uq_research_process_launch UNIQUE (owner_id, run_id, attempt_id, generation, role, logical_operation_id, launch_ordinal), \n\tUNIQUE (reservation_id), \n\tFOREIGN KEY(owner_id, run_id) REFERENCES research_runs (owner_id, id), \n\tFOREIGN KEY(run_id, generation, attempt_id) REFERENCES execution_attempts (run_id, generation, id), \n\tFOREIGN KEY(owner_id, run_id, attempt_id, generation, reservation_id) REFERENCES research_budget_reservations (owner_id, run_id, attempt_id, generation, id), \n\tCONSTRAINT ck_research_process_ordinals CHECK (generation > 0 AND launch_ordinal > 0), \n\tCONSTRAINT ck_research_process_profile CHECK (role = 'neutral_fixture' AND profile = 'neutral_fixture.v1'), \n\tCONSTRAINT ck_research_process_hashes CHECK (input_sha256 ~ '^[a-f0-9]{64}$' AND ticket_sha256 ~ '^[a-f0-9]{64}$' AND image_id ~ '^sha256:[a-f0-9]{64}$')\n)\n\n",
    "\nCREATE TABLE research_model_transports (\n\toperation_id UUID NOT NULL, \n\tordinal INTEGER NOT NULL, \n\treservation_id UUID NOT NULL, \n\tstate VARCHAR(20) NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tgeneration INTEGER NOT NULL, \n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(owner_id, run_id) REFERENCES research_runs (owner_id, id), \n\tFOREIGN KEY(run_id, generation, attempt_id) REFERENCES execution_attempts (run_id, generation, id), \n\tUNIQUE (owner_id, run_id, attempt_id, generation, id), \n\tCHECK (generation > 0), \n\tFOREIGN KEY(owner_id, run_id, attempt_id, generation, operation_id) REFERENCES research_model_operations (owner_id, run_id, attempt_id, generation, id), \n\tFOREIGN KEY(owner_id, run_id, attempt_id, generation, reservation_id) REFERENCES research_budget_reservations (owner_id, run_id, attempt_id, generation, id), \n\tUNIQUE (operation_id, ordinal), \n\tCHECK (ordinal > 0), \n\tCHECK (state IN ('reserved','submitted','completed','failed','unknown')), \n\tUNIQUE (reservation_id)\n)\n\n",
    "\nCREATE TABLE research_object_outcomes (\n\tpublication_id UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\tobject_id UUID NOT NULL, \n\tobject_kind VARCHAR(40) NOT NULL, \n\tsha256 VARCHAR(64) NOT NULL, \n\tjournal_sha256 VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (publication_id), \n\tFOREIGN KEY(owner_id, publication_id, object_kind, sha256) REFERENCES research_object_publications (owner_id, id, object_kind, sha256), \n\tFOREIGN KEY(owner_id, object_id, object_kind, sha256) REFERENCES research_objects (owner_id, id, object_kind, sha256), \n\tCHECK (journal_sha256 ~ '^[0-9a-f]{64}$')\n)\n\n",
    "\nCREATE TABLE research_process_receipts (\n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tgeneration INTEGER NOT NULL, \n\tadmission_id UUID NOT NULL, \n\tkind VARCHAR(30) NOT NULL, \n\tpayload JSONB NOT NULL, \n\tpayload_sha256 VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (admission_id, kind), \n\tFOREIGN KEY(owner_id, run_id, attempt_id, generation, admission_id) REFERENCES research_process_admissions (owner_id, run_id, attempt_id, generation, id), \n\tCONSTRAINT ck_research_process_receipt_kind CHECK (kind IN ('launch_intent','launch_identity','stop')), \n\tCONSTRAINT ck_research_process_receipt_payload CHECK (jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 16384 AND payload_sha256 ~ '^[a-f0-9]{64}$')\n)\n\n",
    "\nCREATE TABLE research_process_states (\n\tadmission_id UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tgeneration INTEGER NOT NULL, \n\tstatus VARCHAR(20) NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (admission_id), \n\tFOREIGN KEY(owner_id, run_id, attempt_id, generation, admission_id) REFERENCES research_process_admissions (owner_id, run_id, attempt_id, generation, id), \n\tCONSTRAINT ck_research_process_state CHECK (status IN ('admitted','launch_intended','running','uncertain','stopped'))\n)\n\n",
    "\nCREATE TABLE research_model_outcomes (\n\ttransport_id UUID NOT NULL, \n\toutcome VARCHAR(20) NOT NULL, \n\tusage JSONB NOT NULL, \n\tresponse_object_id UUID, \n\tresponse_object_kind VARCHAR(32), \n\tresponse_sha256 VARCHAR(64), \n\tattempt_id UUID NOT NULL, \n\tgeneration INTEGER NOT NULL, \n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(owner_id, run_id) REFERENCES research_runs (owner_id, id), \n\tFOREIGN KEY(run_id, generation, attempt_id) REFERENCES execution_attempts (run_id, generation, id), \n\tUNIQUE (owner_id, run_id, attempt_id, generation, id), \n\tCHECK (generation > 0), \n\tFOREIGN KEY(owner_id, run_id, attempt_id, generation, transport_id) REFERENCES research_model_transports (owner_id, run_id, attempt_id, generation, id), \n\tFOREIGN KEY(owner_id, response_object_id, response_object_kind, response_sha256) REFERENCES research_objects (owner_id, id, object_kind, sha256), \n\tCHECK (outcome IN ('completed','failed','unknown')), \n\tCONSTRAINT ck_neutral_outcome_vector CHECK (mineagent_budget_vector(usage)), \n\tCHECK (((outcome = 'completed' AND response_object_id IS NOT NULL AND response_object_kind = 'model_response' AND response_sha256 ~ '^[0-9a-f]{64}$') OR (outcome IN ('failed','unknown') AND response_object_id IS NULL AND response_object_kind IS NULL AND response_sha256 IS NULL)) IS TRUE), \n\tUNIQUE (transport_id)\n)\n\n",
)

BROKER_REQUEST_SQL = "\nCREATE FUNCTION mineagent_broker_request(v jsonb) RETURNS boolean\nLANGUAGE sql IMMUTABLE AS $$\nSELECT COALESCE(CASE WHEN jsonb_typeof(v) <> 'object' OR v IS NULL THEN false\nWHEN v->>'kind' = 'chat' THEN\n v ?& ARRAY['kind','model','messages','max_output_tokens','temperature_millionths']\n AND v - ARRAY['kind','model','messages','max_output_tokens','temperature_millionths'] = '{}'::jsonb\n AND v->>'model' = 'neutral-fixture-v1'\n AND jsonb_typeof(v->'max_output_tokens') = 'number'\n AND v->>'max_output_tokens' ~ '^[0-9]+$'\n AND (v->>'max_output_tokens')::numeric BETWEEN 1 AND 4096\n AND jsonb_typeof(v->'temperature_millionths') = 'number'\n AND v->>'temperature_millionths' ~ '^[0-9]+$'\n AND (v->>'temperature_millionths')::numeric BETWEEN 0 AND 1000000\n AND CASE WHEN jsonb_typeof(v->'messages') = 'array' THEN\n jsonb_array_length(v->'messages') BETWEEN 1 AND 16\n AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v->'messages') m\n WHERE (jsonb_typeof(m) = 'object' AND m ?& ARRAY['role','content']\n AND m - ARRAY['role','content'] = '{}'::jsonb\n AND m->>'role' IN ('system','user','assistant')\n AND jsonb_typeof(m->'content') = 'string'\n AND length(m->>'content') BETWEEN 1 AND 8192) IS NOT TRUE)\n ELSE false END\nWHEN v->>'kind' = 'embedding' THEN\n v ?& ARRAY['kind','model','inputs','dimensions']\n AND v - ARRAY['kind','model','inputs','dimensions'] = '{}'::jsonb\n AND v->>'model' = 'neutral-fixture-v1'\n AND jsonb_typeof(v->'dimensions') = 'number' AND v->>'dimensions' ~ '^[0-9]+$'\n AND (v->>'dimensions')::numeric BETWEEN 1 AND 64\n AND CASE WHEN jsonb_typeof(v->'inputs') = 'array' THEN\n jsonb_array_length(v->'inputs') BETWEEN 1 AND 16\n AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v->'inputs') item\n WHERE (jsonb_typeof(item) = 'string' AND length(item #>> '{}') BETWEEN 1 AND 8192) IS NOT TRUE)\n ELSE false END\nELSE false END, false)\n$$;\n"

BUDGET_SQL = r"""
CREATE FUNCTION mineagent_budget_vector(v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE k text; keys text[] := ARRAY['calls','tokens','cost_microunits','mutation_slots',
 'evaluator_executions','wall_milliseconds','storage_bytes','process_slots'];
BEGIN
 IF v IS NULL OR jsonb_typeof(v) <> 'object' THEN RETURN false; END IF;
 IF NOT (v ?& keys) OR v - keys <> '{}'::jsonb THEN RETURN false; END IF;
 FOREACH k IN ARRAY keys LOOP
  IF jsonb_typeof(v->k) IS DISTINCT FROM 'number' OR (v->>k) !~ '^[0-9]+$'
     OR (v->>k)::numeric > 9007199254740991 THEN RETURN false; END IF;
 END LOOP;
 RETURN true;
END $$;

CREATE FUNCTION mineagent_protect_neutral() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE mutable text[] := COALESCE(TG_ARGV, ARRAY[]::text[]); previous text; following text; k text;
BEGIN
 IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Neutral evidence is append-only'; END IF;
 IF TG_OP = 'UPDATE' THEN
  IF TG_NARGS = 0 AND NEW IS DISTINCT FROM OLD THEN
   RAISE EXCEPTION 'Neutral immutable evidence cannot change';
  END IF;
  IF to_jsonb(NEW) - mutable IS DISTINCT FROM to_jsonb(OLD) - mutable THEN
   RAISE EXCEPTION 'Neutral immutable identity cannot change';
  END IF;
  IF TG_TABLE_NAME IN ('research_budget_reservations','research_model_operations','research_model_transports') THEN
   previous := OLD.state; following := NEW.state;
   IF previous <> following AND NOT (
    (TG_TABLE_NAME = 'research_budget_reservations' AND
      ((previous = 'reserved' AND following IN ('uncertain','settled')) OR
       (previous = 'uncertain' AND following = 'settled'))) OR
    (TG_TABLE_NAME = 'research_model_operations' AND previous = 'open' AND following IN ('completed','unknown','aborted')) OR
    (TG_TABLE_NAME = 'research_model_transports' AND
      ((previous = 'reserved' AND following IN ('submitted','failed')) OR
       (previous = 'submitted' AND following IN ('completed','failed','unknown'))))) THEN
     RAISE EXCEPTION 'Neutral state transition is invalid';
   END IF;
  ELSIF TG_TABLE_NAME = 'research_process_states' THEN
   IF OLD.status = 'stopped' AND NEW.status <> 'stopped' THEN
    RAISE EXCEPTION 'Stopped process cannot restart';
   END IF;
  END IF;
 ELSIF TG_OP = 'INSERT' THEN
  IF (TG_TABLE_NAME IN ('research_budget_reservations','research_model_transports') AND to_jsonb(NEW)->>'state' <> 'reserved') OR
     (TG_TABLE_NAME = 'research_model_operations' AND to_jsonb(NEW)->>'state' <> 'open') THEN
   RAISE EXCEPTION 'Neutral initial state is invalid';
  END IF;
 END IF;
 IF TG_TABLE_NAME = 'research_budget_controls' THEN
  FOR k IN SELECT jsonb_object_keys(NEW.limits) LOOP
   IF (NEW.used->>k)::numeric + (NEW.reserved->>k)::numeric > (NEW.limits->>k)::numeric THEN
    RAISE EXCEPTION 'Neutral budget ceiling exceeded';
   END IF;
  END LOOP;
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION mineagent_neutral_ledger_consistent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c research_budget_controls%ROWTYPE; k text; total_used numeric; total_reserved numeric;
BEGIN
 SELECT * INTO c FROM research_budget_controls
  WHERE owner_id = NEW.owner_id AND run_id = NEW.run_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Neutral budget control missing'; END IF;
 IF EXISTS (
  SELECT 1 FROM research_budget_reservations r LEFT JOIN research_budget_settlements s ON s.reservation_id=r.id
  WHERE r.owner_id=NEW.owner_id AND r.run_id=NEW.run_id AND
   ((r.state='settled') IS DISTINCT FROM (s.id IS NOT NULL) OR
    (s.id IS NOT NULL AND EXISTS (SELECT 1 FROM jsonb_object_keys(r.amounts) AS key
      WHERE (s.charge->>key)::numeric > (r.amounts->>key)::numeric OR
       (s.outcome='not_submitted' AND (s.charge->>key)::numeric <> 0) OR
       (s.outcome='conservative_unknown' AND (s.charge->>key)::numeric <> (r.amounts->>key)::numeric))) OR
    (s.id IS NOT NULL AND r.purpose <> 'model_transport' AND
     (s.outcome <> 'conservative_unknown' OR s.charge <> r.amounts)))) THEN
  RAISE EXCEPTION 'Neutral settlement evidence disagrees with reservation';
 END IF;
 FOR k IN SELECT jsonb_object_keys(c.limits) LOOP
  SELECT COALESCE(sum((s.charge->>k)::numeric),0) INTO total_used
   FROM research_budget_settlements s WHERE s.owner_id=NEW.owner_id AND s.run_id=NEW.run_id;
  SELECT COALESCE(sum((r.amounts->>k)::numeric),0) INTO total_reserved
   FROM research_budget_reservations r WHERE r.owner_id=NEW.owner_id AND r.run_id=NEW.run_id AND r.state <> 'settled';
  IF (c.used->>k)::numeric <> total_used OR (c.reserved->>k)::numeric <> total_reserved OR
     total_used + total_reserved > (c.limits->>k)::numeric THEN
   RAISE EXCEPTION 'Neutral budget ledger and projection disagree';
  END IF;
 END LOOP;
 RETURN NEW;
END $$;

CREATE FUNCTION mineagent_neutral_process_consistent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE aid uuid; s research_process_states%ROWTYPE;
BEGIN
 IF TG_TABLE_NAME='research_process_admissions' THEN aid := NEW.id; ELSE aid := NEW.admission_id; END IF;
 SELECT * INTO s FROM research_process_states WHERE admission_id=aid;
 IF NOT FOUND THEN RAISE EXCEPTION 'Neutral process projection missing'; END IF;
 IF s.status='stopped' AND NOT EXISTS (SELECT 1 FROM research_process_receipts WHERE admission_id=aid AND kind='stop') THEN
  RAISE EXCEPTION 'Neutral process stop proof missing';
 END IF;
 IF s.status IN ('launch_intended','running','stopped') AND NOT EXISTS (SELECT 1 FROM research_process_receipts WHERE admission_id=aid AND kind='launch_intent') THEN
  RAISE EXCEPTION 'Neutral process launch intent missing';
 END IF;
 IF s.status IN ('running','stopped') AND NOT EXISTS (SELECT 1 FROM research_process_receipts WHERE admission_id=aid AND kind='launch_identity') THEN
  RAISE EXCEPTION 'Neutral process identity proof missing';
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION mineagent_neutral_terminal_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE rid uuid; aid uuid; moving boolean; terminalizing boolean;
BEGIN
 IF TG_TABLE_NAME = 'research_runs' THEN
  rid := NEW.id; aid := NULL; moving := NEW.generation <> OLD.generation;
  terminalizing := NEW.status IN ('succeeded','failed','cancelled','interrupted','paused') AND NEW.status IS DISTINCT FROM OLD.status;
 ELSE
  rid := NEW.run_id; aid := NEW.id; moving := false;
  terminalizing := (NEW.finished_at IS NOT NULL AND OLD.finished_at IS NULL) OR (NEW.sandbox_stopped AND NOT OLD.sandbox_stopped);
 END IF;
 IF NOT moving AND NOT terminalizing THEN RETURN NEW; END IF;
 IF EXISTS (SELECT 1 FROM research_process_admissions a
   LEFT JOIN research_process_states s ON s.admission_id=a.id
   WHERE a.run_id=rid AND (aid IS NULL OR a.attempt_id=aid) AND
   (s.status IS DISTINCT FROM 'stopped' OR NOT EXISTS (
     SELECT 1 FROM research_process_receipts p WHERE p.admission_id=a.id AND p.kind='stop'))) THEN
  RAISE EXCEPTION 'Neutral writer containment is unconfirmed';
 END IF;
 IF EXISTS (SELECT 1 FROM research_object_publications p
   WHERE p.run_id=rid AND (aid IS NULL OR p.attempt_id=aid) AND NOT EXISTS
    (SELECT 1 FROM research_object_outcomes o WHERE o.publication_id=p.id)) THEN
  RAISE EXCEPTION 'Neutral object writer containment is unconfirmed';
 END IF;
 IF moving AND EXISTS (SELECT 1 FROM research_budget_reservations r WHERE r.run_id=rid AND r.state <> 'settled') THEN
  RAISE EXCEPTION 'Neutral unresolved reservations block a new generation';
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION mineagent_neutral_reference_consistent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r research_budget_reservations%ROWTYPE;
BEGIN
 IF TG_TABLE_NAME = 'research_object_publications' THEN
  SELECT * INTO r FROM research_budget_reservations WHERE id=NEW.budget_reservation_id;
  IF r.purpose IS DISTINCT FROM 'object_storage' OR (r.amounts->>'storage_bytes')::numeric < NEW.peak_bytes THEN
   RAISE EXCEPTION 'Object publication lacks exact physical reservation';
  END IF;
 ELSIF TG_TABLE_NAME = 'research_process_admissions' THEN
  SELECT * INTO r FROM research_budget_reservations WHERE id=NEW.reservation_id;
  IF r.purpose IS DISTINCT FROM 'process_unit' OR (r.amounts->>'process_slots')::numeric < 1 OR (r.amounts->>'wall_milliseconds')::numeric < 60000 OR (r.amounts->>'storage_bytes')::numeric < 1179648 THEN
   RAISE EXCEPTION 'Process admission lacks exact unit reservation';
  END IF;
 ELSIF TG_TABLE_NAME = 'research_model_transports' THEN
  SELECT * INTO r FROM research_budget_reservations WHERE id=NEW.reservation_id;
  IF r.purpose IS DISTINCT FROM 'model_transport' OR (r.amounts->>'calls')::numeric <> 1 THEN
   RAISE EXCEPTION 'Model transport lacks one physical-call reservation';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION mineagent_neutral_model_consistent() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS (
  SELECT 1 FROM research_model_transports t
   JOIN research_budget_reservations r ON r.id=t.reservation_id
   LEFT JOIN research_model_outcomes o ON o.transport_id=t.id
   LEFT JOIN research_budget_settlements s ON s.reservation_id=r.id
  WHERE t.owner_id=NEW.owner_id AND t.run_id=NEW.run_id AND (
   r.purpose <> 'model_transport' OR (r.amounts->>'calls')::numeric <> 1 OR
   (t.state='reserved' AND (r.state<>'reserved' OR o.id IS NOT NULL OR s.id IS NOT NULL)) OR
   (t.state='submitted' AND (r.state<>'uncertain' OR o.id IS NOT NULL OR s.id IS NOT NULL)) OR
   (t.state IN ('completed','failed','unknown') AND
    (o.id IS NULL OR s.id IS NULL OR r.state<>'settled' OR o.outcome<>t.state OR
     o.usage<>s.charge OR (o.usage->>'calls')::numeric<>1 OR
     ((o.outcome='unknown') IS DISTINCT FROM (s.outcome='conservative_unknown')))))) THEN
  RAISE EXCEPTION 'Model physical transport and accounting evidence disagree';
 END IF;
 IF EXISTS (
  SELECT 1 FROM research_model_operations o
   LEFT JOIN LATERAL (SELECT state FROM research_model_transports t WHERE t.operation_id=o.id ORDER BY ordinal DESC LIMIT 1) latest ON true
  WHERE o.owner_id=NEW.owner_id AND o.run_id=NEW.run_id AND (
   ((o.state='completed') IS DISTINCT FROM COALESCE(latest.state='completed',false)) OR
   ((o.state='unknown') IS DISTINCT FROM COALESCE(latest.state='unknown',false)) OR
   EXISTS (SELECT 1 FROM research_model_transports t WHERE t.operation_id=o.id AND
    t.ordinal < (SELECT max(ordinal) FROM research_model_transports WHERE operation_id=o.id) AND t.state<>'failed') OR
   (SELECT count(*) FROM research_model_transports WHERE operation_id=o.id) <>
    COALESCE((SELECT max(ordinal) FROM research_model_transports WHERE operation_id=o.id),0))) THEN
  RAISE EXCEPTION 'Model logical operation and retry evidence disagree';
 END IF;
 IF TG_TABLE_NAME='research_budget_reservations' AND TG_OP='UPDATE' THEN
  IF OLD.state='uncertain' AND NEW.state='settled' AND EXISTS (
   SELECT 1 FROM research_budget_settlements WHERE reservation_id=NEW.id AND outcome='not_submitted') THEN
   RAISE EXCEPTION 'Uncertain physical submission cannot be refunded';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION mineagent_neutral_context_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cid uuid;
BEGIN
 IF TG_TABLE_NAME IN ('research_context_aborts','research_model_operations') THEN
  cid := NEW.context_id;
 ELSE
  SELECT context_id INTO cid FROM research_model_operations WHERE id=NEW.operation_id;
 END IF;
 PERFORM 1 FROM research_operation_contexts WHERE id=cid FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Neutral immutable context missing'; END IF;
 IF TG_TABLE_NAME <> 'research_context_aborts' AND EXISTS
  (SELECT 1 FROM research_context_aborts WHERE context_id=cid) THEN
  IF TG_OP='INSERT' THEN RAISE EXCEPTION 'Aborted context cannot admit work'; END IF;
  IF NEW.state IS DISTINCT FROM OLD.state THEN RAISE EXCEPTION 'Aborted context cannot submit or settle work'; END IF;
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION mineagent_neutral_abort_consistent() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS (SELECT 1 FROM research_model_operations o
  WHERE o.owner_id=NEW.owner_id AND o.run_id=NEW.run_id AND
   ((o.state='aborted' AND NOT EXISTS (SELECT 1 FROM research_context_aborts WHERE context_id=o.context_id)) OR
    (o.state='open' AND EXISTS (SELECT 1 FROM research_context_aborts WHERE context_id=o.context_id)))) THEN
  RAISE EXCEPTION 'Neutral context revocation and operation projection disagree';
 END IF;
 RETURN NEW;
END $$;
"""


def upgrade():
    op.execute("LOCK TABLE research_runs, execution_attempts IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "ALTER TABLE research_runs ADD CONSTRAINT uq_research_run_owner_id UNIQUE(owner_id,id)"
    )
    op.execute(
        "ALTER TABLE execution_attempts ADD CONSTRAINT uq_attempt_scope_id UNIQUE(run_id,generation,id)"
    )
    # Predicate functions must exist before table CHECK constraints. Ledger and
    # receipt functions reference the new relations and are installed afterward.
    predicate, remainder = BUDGET_SQL.split(
        "CREATE FUNCTION mineagent_protect_neutral", 1
    )
    op.execute(predicate)
    op.execute(BROKER_REQUEST_SQL)
    for ddl in TABLE_DDL:
        op.execute(ddl)
    op.execute("CREATE FUNCTION mineagent_protect_neutral" + remainder)
    for table in ("research_runs", "execution_attempts"):
        op.execute(
            f"CREATE TRIGGER guard_neutral_terminal BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION mineagent_neutral_terminal_guard()"
        )
    for table in (
        "research_object_publications",
        "research_process_admissions",
        "research_model_transports",
    ):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER check_{table}_reservation AFTER INSERT ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_neutral_reference_consistent()"
        )
    for table in (
        "research_budget_reservations",
        "research_budget_settlements",
        "research_model_operations",
        "research_model_transports",
        "research_model_outcomes",
    ):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER check_{table}_model AFTER INSERT OR UPDATE ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_neutral_model_consistent()"
        )
    for table in ("research_context_aborts", "research_model_operations"):
        op.execute(
            f"CREATE TRIGGER guard_neutral_context BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION mineagent_neutral_context_guard()"
        )
        op.execute(
            f"CREATE CONSTRAINT TRIGGER check_{table}_abort AFTER INSERT OR UPDATE ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_neutral_abort_consistent()"
        )
    op.execute(
        "CREATE TRIGGER guard_neutral_context BEFORE INSERT OR UPDATE ON research_model_transports FOR EACH ROW EXECUTE FUNCTION mineagent_neutral_context_guard()"
    )
    mutable = {
        "research_budget_controls": ("used", "reserved"),
        "research_budget_reservations": ("state",),
        "research_model_operations": ("state",),
        "research_model_transports": ("state",),
        "research_process_states": ("status", "updated_at"),
    }
    for table in TABLES:
        args = ",".join("'" + name + "'" for name in mutable.get(table, ()))
        events = (
            "INSERT OR UPDATE OR DELETE" if table in mutable else "UPDATE OR DELETE"
        )
        op.execute(
            f"CREATE TRIGGER protect_{table} BEFORE {events} ON {table} FOR EACH ROW EXECUTE FUNCTION mineagent_protect_neutral({args})"
        )
    for table in (
        "research_budget_controls",
        "research_budget_reservations",
        "research_budget_settlements",
    ):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER check_{table}_ledger AFTER INSERT OR UPDATE ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_neutral_ledger_consistent()"
        )
    for table in (
        "research_process_admissions",
        "research_process_receipts",
        "research_process_states",
    ):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER check_{table}_closure AFTER INSERT OR UPDATE ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION mineagent_neutral_process_consistent()"
        )


def downgrade():
    # Evidence-bearing downgrades cannot erase conservative accounting or receipts.
    op.execute("""DO $$ BEGIN
      IF current_setting('transaction_isolation') <> 'read committed'
      THEN RAISE EXCEPTION 'Neutral downgrade requires READ COMMITTED isolation for authoritative emptiness checks'; END IF;
      IF NOT pg_try_advisory_xact_lock(1413960260, 1129270340)
      THEN RAISE EXCEPTION 'Neutral migration requires quiescent maintenance admission'; END IF;
    END $$;""")
    # FK trigger removal takes implicit locks on parents too. Admit all of them
    # without waiting, before checking a fresh authoritative emptiness snapshot.
    op.execute(
        "LOCK TABLE "
        + ", ".join((*TABLES, "users", "research_runs", "execution_attempts"))
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    condition = " OR ".join(f"EXISTS(SELECT 1 FROM {table})" for table in TABLES)
    op.execute(
        f"DO $$ BEGIN IF {condition} THEN RAISE EXCEPTION 'Neutral evidence prevents destructive downgrade'; END IF; END $$"
    )
    op.execute(
        "DROP TRIGGER guard_neutral_terminal ON research_runs; DROP TRIGGER guard_neutral_terminal ON execution_attempts"
    )
    op.execute("DROP FUNCTION mineagent_neutral_terminal_guard()")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    op.execute(
        "DROP FUNCTION mineagent_neutral_abort_consistent(); DROP FUNCTION mineagent_neutral_context_guard(); DROP FUNCTION mineagent_neutral_model_consistent(); DROP FUNCTION mineagent_neutral_reference_consistent(); DROP FUNCTION mineagent_neutral_process_consistent(); DROP FUNCTION mineagent_neutral_ledger_consistent(); DROP FUNCTION mineagent_protect_neutral(); DROP FUNCTION mineagent_broker_request(jsonb); DROP FUNCTION mineagent_budget_vector(jsonb)"
    )
    op.execute("ALTER TABLE execution_attempts DROP CONSTRAINT uq_attempt_scope_id")
    op.execute("ALTER TABLE research_runs DROP CONSTRAINT uq_research_run_owner_id")

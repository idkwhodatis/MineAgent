"""Immutable configured RD queued submission and exact user request receipt."""

import re

from alembic import op

revision = "023_rd_submissions"
down_revision = "022_factor_analysis"
branch_labels = None
depends_on = None

TABLE_SQL = "\nCREATE TABLE factor_research_submissions (\n\tid UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\tplan_id UUID NOT NULL, \n\tidempotency_key VARCHAR(128) NOT NULL, \n\trequest_canonical TEXT NOT NULL, \n\trequest_sha256 VARCHAR(64) NOT NULL, \n\tpreflight_sha256 VARCHAR(64) NOT NULL, \n\tconfig_sha256 VARCHAR(64) NOT NULL, \n\truntime_profile_sha256 VARCHAR(64) NOT NULL, \n\tcatalog_revision VARCHAR(96) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (owner_id, idempotency_key), \n\tUNIQUE (owner_id, run_id), \n\tUNIQUE (owner_id, run_id, id), \n\tFOREIGN KEY(owner_id, run_id) REFERENCES research_runs (owner_id, id), \n\tFOREIGN KEY(owner_id, run_id, plan_id) REFERENCES factor_analysis_plans (owner_id, run_id, id), \n\tCHECK (idempotency_key ~ '^[!-~]{1,128}$'), \n\tCHECK (octet_length(request_canonical) BETWEEN 1 AND 131072), \n\tCHECK (request_sha256 ~ '^[a-f0-9]{64}$' AND preflight_sha256 ~ '^[a-f0-9]{64}$' AND config_sha256 ~ '^[a-f0-9]{64}$' AND runtime_profile_sha256 ~ '^[a-f0-9]{64}$'), \n\tCHECK (catalog_revision='configured_rd_single_output_session_v1')\n)\n\n"
UPGRADE_SQL = "\nCREATE FUNCTION @SCHEMA@.mineagent_rd_submission_check(sid uuid) RETURNS void\nLANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$\nDECLARE s factor_research_submissions%ROWTYPE; r research_runs%ROWTYPE;\n p factor_analysis_plans%ROWTYPE; expected jsonb; exact_bytes text; input_fields jsonb;\nBEGIN\n SELECT * INTO STRICT s FROM factor_research_submissions WHERE id=sid;\n SELECT * INTO STRICT r FROM research_runs WHERE id=s.run_id;\n SELECT * INTO STRICT p FROM factor_analysis_plans WHERE id=s.plan_id;\n PERFORM mineagent_analysis_plan_check(p.id);\n IF (r.owner_id,r.idempotency_key,r.purpose,r.engine,r.request_sha256)\n IS DISTINCT FROM (s.owner_id,s.idempotency_key,'factor_research'::varchar,'rdagent'::varchar,s.config_sha256)\n OR (p.owner_id,p.run_id,p.config_sha256) IS DISTINCT FROM (s.owner_id,s.run_id,s.config_sha256)\n THEN RAISE EXCEPTION 'Submission exact owner/run/plan binding differs'; END IF;\n IF NOT EXISTS(SELECT 1 FROM outbox_events WHERE run_id=r.id AND generation=1)\n OR NOT EXISTS(SELECT 1 FROM research_budget_controls WHERE run_id=r.id AND owner_id=r.owner_id AND limits=r.config->'budget')\n THEN RAISE EXCEPTION 'Submission durable outbox or immutable budget missing'; END IF;\n SELECT jsonb_agg(value->'name' ORDER BY ord) INTO input_fields\n FROM jsonb_array_elements(r.config->'input'->'input_fields') WITH ORDINALITY f(value,ord);\n expected:=jsonb_build_object('schema_version','mineagent.rdagent-submission.v1',\n 'purpose','factor_research','engine','rdagent','dataset_version_id',r.dataset_version_id::text,\n 'window',r.config->'input'->'window','input_fields',input_fields,\n 'lookback_sessions',r.config->'input'->'lookback_sessions',\n 'lag_sessions',r.config->'input'->'lag_sessions','task',r.config->'task',\n 'objective',p.objective,'budget',r.config->'budget','max_iterations',r.config->'max_iterations',\n 'source_policy',p.source_policy);\n SELECT '{'||string_agg(mineagent_rd_canonical_json(to_jsonb(key),true)||':'||\n CASE WHEN key='objective' THEN p.objective_canonical ELSE mineagent_rd_canonical_json(value,true) END,\n ',' ORDER BY key COLLATE \"C\")||'}' INTO exact_bytes FROM jsonb_each(expected);\n IF s.request_canonical IS DISTINCT FROM exact_bytes\n OR s.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(exact_bytes,'UTF8')),'hex')\n OR s.runtime_profile_sha256 IS DISTINCT FROM mineagent_execution_metadata_sha(jsonb_build_object(\n 'schema_version','mineagent.rdagent-server-profile.v1','profile','configured_rd_single_output_session_v1',\n 'model_profile','fixture.rdagent.v1','runtime',r.config->'runtime','qlib_installed_parent_image_id',p.qlib_installed_parent_image_id))\n OR s.preflight_sha256 IS DISTINCT FROM mineagent_execution_metadata_sha(jsonb_build_object(\n 'schema_version','mineagent.rdagent-preflight-binding.v1','owner_id',s.owner_id::text,\n 'request_sha256',s.request_sha256,'dataset_content_sha256',p.label_source_manifest_sha256,\n 'frozen_input_sha256',p.input_sha256,'catalog_revision',s.catalog_revision,\n 'runtime_profile_sha256',s.runtime_profile_sha256))\n THEN RAISE EXCEPTION 'Submission canonical declaration or preflight differs'; END IF;\nEND $$;\nCREATE FUNCTION @SCHEMA@.mineagent_rd_submission_append() RETURNS trigger\nLANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$\nDECLARE r research_runs%ROWTYPE;\nBEGIN\n IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Submission receipt is immutable'; END IF;\n IF current_setting('transaction_isolation')<>'read committed'\n THEN RAISE EXCEPTION 'Submission requires READ COMMITTED'; END IF;\n SELECT * INTO STRICT r FROM research_runs WHERE id=NEW.run_id AND owner_id=NEW.owner_id FOR UPDATE NOWAIT;\n IF r.status<>'queued' OR r.generation<>1 OR r.active_attempt_id IS NOT NULL\n OR EXISTS(SELECT 1 FROM execution_attempts WHERE run_id=r.id)\n OR EXISTS(SELECT 1 FROM factor_generation_starts WHERE run_id=r.id)\n THEN RAISE EXCEPTION 'Initial submission must be queued without execution authority'; END IF;\n RETURN NEW;\nEND $$;\nCREATE TRIGGER rd_submission_append BEFORE INSERT OR UPDATE OR DELETE\nON @SCHEMA@.factor_research_submissions FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_submission_append();\nCREATE FUNCTION @SCHEMA@.mineagent_rd_submission_consistent() RETURNS trigger\nLANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$\nDECLARE sid uuid; rid uuid;\nBEGIN\n IF TG_TABLE_NAME='factor_research_submissions' THEN\n  PERFORM mineagent_rd_submission_check(NEW.id);\n ELSE\n  IF TG_TABLE_NAME='research_runs' THEN rid:=NEW.id;\n  ELSIF TG_OP='DELETE' THEN rid:=OLD.run_id;\n  ELSE rid:=NEW.run_id; END IF;\n  FOR sid IN SELECT id FROM factor_research_submissions WHERE run_id=rid LOOP\n   PERFORM mineagent_rd_submission_check(sid);\n  END LOOP;\n  IF TG_TABLE_NAME='research_runs' THEN\n  IF TG_OP='INSERT' AND NEW.purpose='factor_research' AND NEW.engine='rdagent' AND NEW.status='queued'\n  AND NOT EXISTS(SELECT 1 FROM factor_research_submissions WHERE run_id=NEW.id)\n  THEN RAISE EXCEPTION 'Queued RD submission requires immutable request receipt'; END IF;\n  END IF;\n END IF;\n RETURN NULL;\nEND $$;\nCREATE CONSTRAINT TRIGGER rd_submission_closed AFTER INSERT ON @SCHEMA@.factor_research_submissions\nDEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_submission_consistent();\nCREATE CONSTRAINT TRIGGER rd_submitted_run_closed AFTER INSERT OR UPDATE ON @SCHEMA@.research_runs\nDEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_submission_consistent();\nCREATE CONSTRAINT TRIGGER rd_submission_outbox_closed AFTER INSERT OR UPDATE OR DELETE ON @SCHEMA@.outbox_events\nDEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_submission_consistent();\nCREATE CONSTRAINT TRIGGER rd_submission_budget_closed AFTER INSERT OR UPDATE OR DELETE ON @SCHEMA@.research_budget_controls\nDEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_submission_consistent();\n"
DOWNGRADE_SQL = "\nDO $$ BEGIN\n IF EXISTS(SELECT 1 FROM @SCHEMA@.factor_research_submissions)\n THEN RAISE EXCEPTION 'Submission receipts cannot be represented before023'; END IF;\nEND $$;\nDROP TRIGGER rd_submission_budget_closed ON @SCHEMA@.research_budget_controls;\nDROP TRIGGER rd_submission_outbox_closed ON @SCHEMA@.outbox_events;\nDROP TRIGGER rd_submitted_run_closed ON @SCHEMA@.research_runs;\nDROP TRIGGER rd_submission_closed ON @SCHEMA@.factor_research_submissions;\nDROP TRIGGER rd_submission_append ON @SCHEMA@.factor_research_submissions;\nDROP FUNCTION @SCHEMA@.mineagent_rd_submission_consistent();\nDROP FUNCTION @SCHEMA@.mineagent_rd_submission_append();\nDROP FUNCTION @SCHEMA@.mineagent_rd_submission_check(uuid);\n"


def _schema():
    value = op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one()
    if type(value) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value) is None:
        raise RuntimeError("Exact installation schema required")
    return '"' + value + '"'


def _maintenance(schema):
    op.execute("""DO $$ BEGIN
    IF current_setting('transaction_isolation') <> 'read committed'
    OR NOT pg_try_advisory_xact_lock(1413960260,1129270340)
    THEN RAISE EXCEPTION 'Submission migration requires quiescent maintenance'; END IF;
    END $$;""")
    op.execute(
        "LOCK TABLE "
        + ",".join(
            schema + "." + table
            for table in (
                "research_runs",
                "execution_attempts",
                "factor_analysis_plans",
                "factor_research_specs",
                "outbox_events",
                "research_budget_controls",
                "data_usage_exposures",
            )
        )
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )


def upgrade():
    schema = _schema()
    _maintenance(schema)
    op.execute("SET LOCAL search_path = " + schema + ",pg_catalog")
    op.get_bind().exec_driver_sql(TABLE_SQL, execution_options={"no_parameters": True})
    op.get_bind().exec_driver_sql(
        UPGRADE_SQL.replace("@SCHEMA@", schema),
        execution_options={"no_parameters": True},
    )


def downgrade():
    schema = _schema()
    _maintenance(schema)
    op.execute(
        "LOCK TABLE "
        + schema
        + ".factor_research_submissions IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.get_bind().exec_driver_sql(
        DOWNGRADE_SQL.replace("@SCHEMA@", schema),
        execution_options={"no_parameters": True},
    )
    op.execute("DROP TABLE " + schema + ".factor_research_submissions RESTRICT")

"""Test-only read-only checkpoint/root probes; no predicate or schema mutation."""

import hashlib
import json
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import text

# Exact58-function transitive read-only closure, including immutable validators.
PINS = {
    "mineagent_analysis_raw_commitment": (
        "4dd030f0db0bf1ad611a302c5447a13cb731db14dbd2cbd04352a66abd7b99f5",
        "i",
        "catalog",
    ),
    "mineagent_analysis_raw_json": (
        "c29f5ade6132135f1296c37527ec83dc5889960a01b8cc97e3180c3f689c497a",
        "i",
        "catalog",
    ),
    "mineagent_budget_vector": (
        "793ee62bd606567d25672e1e27fdb3cb431dfcbc81362894408843a9649536c0",
        "i",
        "none",
    ),
    "mineagent_execution_metadata_sha": (
        "b58aae6ffbc813de839dc8515fbcfbad5cd6242de47f2a5c5b0473c4a0b8df8a",
        "i",
        "catalog",
    ),
    "mineagent_oe_workflow_attachment": (
        "70162c93463fbf02beb7c0a5cfe365c0f53deaad3c74bd14477b359e46ea368b",
        "v",
        "schema",
    ),
    "mineagent_oe_workflow_checkpoint": (
        "46fa92713e16d4d2af5d8f59189a428d154a84a3856f7cab552e15fcf58241b5",
        "v",
        "schema",
    ),
    "mineagent_oe_workflow_consumption": (
        "2f76f0b9c79587dd7948cba3edeb1f6d0ff25623d029e7d9ecdd6fb0ee6c86f1",
        "v",
        "schema",
    ),
    "mineagent_oe_workflow_rendered": (
        "fbcbc582a5b0887120e65f06d91ad249ebf86a5f8169c157cda859d15486e168",
        "i",
        "catalog",
    ),
    "mineagent_oe_workflow_shape": (
        "e678048bebd5e174474cf36c000440c03e0ea0176e8fc81bf678fd20d575b73e",
        "i",
        "catalog",
    ),
    "mineagent_oes_application_check": (
        "0bce967e49f10dbb8086519d5e23d1af0232f6080b750287ffa866f6d79f471d",
        "v",
        "catalog",
    ),
    "mineagent_oes_candidate_check": (
        "ec90393ac0de8bf46b38dd2dc1e7f5e83882bd29ed82c312f5034dc31a1a2dfb",
        "v",
        "catalog",
    ),
    "mineagent_oes_checkpoint_check": (
        "5d6b9032e068a5173a7afa33de6a3064569947e3d67e59ed27e60183c46511c7",
        "v",
        "schema",
    ),
    "mineagent_oes_contract": (
        "0ef5e7621c11f2006a0963362d33a01bd95bdbee04ae3dab988a4637365e7720",
        "i",
        "catalog",
    ),
    "mineagent_oes_contract_before_workflow": (
        "9b11dafa830f57465673bbc88a0b46092f955dc7bbd8a1f4585daad20d5ed4df",
        "i",
        "catalog",
    ),
    "mineagent_oes_contract_v030_shape": (
        "333195b34c4071bbdef5f92ebf19df789d0fc4a358ae3b431b69499af1a0a36a",
        "i",
        "catalog",
    ),
    "mineagent_oes_control_check": (
        "46bcfe68b79214d7f31b8d2ea427a839242d8c5e46dcbeeedcc134f8ad7b3fa4",
        "v",
        "catalog",
    ),
    "mineagent_oes_controller_check": (
        "e0d59e5a41c641d727916d282215322dc34bd7b8d3734fdf099446e83ad79441",
        "v",
        "catalog",
    ),
    "mineagent_oes_controller_completed": (
        "d2797507bca8c51b7dbfc33338b8f8d2137a3e23e9d75e8e85953f49f5320331",
        "v",
        "catalog",
    ),
    "mineagent_oes_controller_payload": (
        "aa70bc29016bffcc511889edda35c71088fba3f8081f4f56f69882fbbb35e919",
        "i",
        "catalog",
    ),
    "mineagent_oes_epoch_check": (
        "591c9c996c0798593d31fa35443978053866b87f516e357b9aaf4a409368a922",
        "v",
        "catalog",
    ),
    "mineagent_oes_execution_check": (
        "c0a4dfb91b62ec9ea704221ffaefd3321f4a4a03ff168bf0fd97602e02fc5337",
        "v",
        "catalog",
    ),
    "mineagent_oes_graph_check": (
        "f95b118dc9a65fa3e2361225bb21288732d593908c49dc6523325d8e3001ad27",
        "v",
        "catalog",
    ),
    "mineagent_oes_iteration_check": (
        "c86c4d05f195f38a34ce5b014ddcf6e6cf733f9d1518e691a010efa68f24767c",
        "v",
        "catalog",
    ),
    "mineagent_oes_iteration_contract": (
        "c12a89f353c3c61cd55cf5d9ce748148801048853bf9185c37afa7ab640fe381",
        "i",
        "catalog",
    ),
    "mineagent_oes_measurement_matches": (
        "80d3c0b88f14960cf6bd737a404bbfc838756282782e50e66e62ce810f72013f",
        "i",
        "catalog",
    ),
    "mineagent_oes_native_check": (
        "860dcff14c921a593db7d20d121c16678725ed52dba4c988e7d33aa39ddfa7b1",
        "v",
        "catalog",
    ),
    "mineagent_oes_outcome_check": (
        "8760f6e256145f01b0c9b0f8c4431d8ff6b59944d0f3e90f432895654b51939a",
        "v",
        "catalog",
    ),
    "mineagent_oes_placement_check": (
        "65405259aa2affc9013a9d277cb5064915ef5b707f7e62cd70d1dee83a1f623d",
        "v",
        "catalog",
    ),
    "mineagent_oes_population_check": (
        "81cfd207e406d432613c415a172ca75811f301e156ee215278a9586124fd4149",
        "v",
        "catalog",
    ),
    "mineagent_oes_provider_check": (
        "cf7c0fe04686b6382e72d02f7a0895caca09dcd9f6020000800677b177136e70",
        "v",
        "schema",
    ),
    "mineagent_oes_provider_contract": (
        "25092e259d877517c3856032cdf9853e53a8c00ca29bf78e0af413a1ffa718b0",
        "i",
        "schema",
    ),
    "mineagent_oes_sampling_check": (
        "0cbbc88c41d9b055cedd312d0bda0bd0e0e6464c1796d1b2af60ec2be5a92652",
        "v",
        "catalog",
    ),
    "mineagent_oes_spec_check": (
        "857011f128bb7c574563ae382ab0d8292c12ab218ee7cde87989cdb9b5937ca5",
        "v",
        "catalog",
    ),
    "mineagent_oes_worker_check": (
        "aca1497ce44a23941b77d5f028cfd08eca516cd7b1a86c236fccdebb94634d22",
        "v",
        "catalog",
    ),
    "mineagent_rd_ascii_metadata": (
        "cf618bd72155d25661ef428cc0bbc2e1e3af5b6430d9fb26eb7ce3510cae59c8",
        "i",
        "catalog",
    ),
    "mineagent_rd_canonical_json": (
        "e64976ca7931a79957c139e9cf1f526ed4e42d9ab95c4231f0ccca62f9920f43",
        "i",
        "catalog",
    ),
    "mineagent_rd_controller_kernel": (
        "d2ff9f00de16db2dd4930d6b0ae312028932ad27c515e7f650bf033d8d17b88a",
        "i",
        "schema",
    ),
    "mineagent_rd_controller_selection": (
        "d8de28ba24eb3a4dcf858da11b28cf88d6276c6a48aac8bd557f9400389565fd",
        "i",
        "schema",
    ),
    "mineagent_rd_json_bytes": (
        "538aee2f498ceffe42e07ee17e2bfc6b6c4e189bf0ac3e67061886d90820d7a8",
        "i",
        "catalog",
    ),
    "mineagent_rd_provider_config": (
        "3213c0e644ed961bef86cd6d32681ae1fcccdaf641ea7c5358ac87431ef8f16f",
        "i",
        "schema",
    ),
    "mineagent_rd_provider_config_026_frozen": (
        "f084d4e423ca6d14ec0bc1f14369eed7f2d1ba1e11d4c6ce662c7991f106adf7",
        "i",
        "schema",
    ),
    "mineagent_rd_provider_payload": (
        "b978266c53e2039eea1d6e2c0de1abc02b6e3478b1db4515a4bd637abe291e66",
        "i",
        "schema",
    ),
    "mineagent_rd_schema": (
        "fbb1e9e697d26ad5314816eb676226265061d5c82724a045cb66be09ff430208",
        "i",
        "catalog",
    ),
    "mineagent_rd_schema_recursive_032e": (
        "04ed4f9d9203803ede3f97959e8c01c738f3290ecb81ea943c22c221c773eff0",
        "i",
        "catalog",
    ),
    "mineagent_structured_contract": (
        "f4ef87d3706edf77154160011f38925a8cdf9de64f7bf0406a060e38b68f05e2",
        "i",
        "catalog",
    ),
    "mineagent_structured_json_value": (
        "a58e7feeeccd790a8e622d0540fa7a7d53c9dcfe20a20ef52a10fa3dde4be166",
        "i",
        "catalog",
    ),
    "mineagent_structured_payload": (
        "b130dd7e6352a04464c61d90e21761dafe23c3c5577fc16453b30c6d6f747a73",
        "i",
        "schema",
    ),
    "mineagent_structured_raw_commitment": (
        "4ba67775642f398d1c79d2f4f39bd509dcdb8b650f1cc9ffd876c5e3c6d42435",
        "i",
        "catalog",
    ),
    "mineagent_structured_raw_json": (
        "e9b352169c8e0c2ad68440fd3c9e3d41ffaeaf5682cbd98958579c4366db1ed0",
        "i",
        "catalog",
    ),
    "mineagent_structured_stopped": (
        "95248c8783099965a96d55ecb356a7b6ad0751e98cd1c8cf16235b1fa07b54da",
        "s",
        "schema",
    ),
    "mineagent_upload_basket_check": (
        "43b214f204e40fec2b624d3206ed709b086363e60782b32c0b5782982af6e93d",
        "v",
        "catalog",
    ),
    "mineagent_upload_oe_qualification": (
        "695f9892031af1e932626434b06802cc98cbba058b56f463806d6b2d29a3d6d0",
        "v",
        "catalog",
    ),
    "mineagent_workflow_checkpoint": (
        "443e6d377d647982ecc3d2f24923e2681fe670a748fd3315bc6bda77e64021f7",
        "v",
        "schema",
    ),
    "mineagent_workflow_consistent": (
        "62401fc3275300eb624c25499a0a7d6aa4591d87624ba236d9e63c72c5771f2d",
        "v",
        "schema",
    ),
    "mineagent_workflow_payload": (
        "6411235259e3802b74ee7d8d4c32adf20a7c897d30de13b7dae78b6e56d37f02",
        "i",
        "catalog",
    ),
    "mineagent_workflow_payload_before_oe": (
        "0d883624784219f6777507c6a2f0636f419821f05c7ca6c7310d576935efe890",
        "i",
        "schema",
    ),
    "mineagent_workflow_raw": (
        "9547ec2088c6ae34882761be966acb10f3111a917cc74fa890b7bc0f80ab563c",
        "i",
        "schema",
    ),
    "mineagent_workflow_refs": (
        "cc1f38ff6eda14e5fa8b01717e9c0b593aacd5706ab9834b5b02ce89248595c4",
        "v",
        "schema",
    ),
}

STATES = frozenset({"23514", "42703", "42702", "42P01", "57014", "55P03", "P0001"})
PIN_SQL = """SELECT p.proname,replace(p.prosrc,chr(34)||n.nspname||chr(34),'@SCHEMA@'),p.prosecdef,p.provolatile,
 CASE WHEN p.proconfig IS NULL THEN 'none'
 WHEN p.proconfig=ARRAY['search_path=pg_catalog'] THEN 'catalog'
 WHEN p.proconfig=ARRAY['search_path='||n.nspname||', pg_catalog'] THEN 'schema'
 ELSE 'other' END FROM pg_proc p
 JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname=current_schema() AND p.proname = ANY(:names)"""

PREFIX = """WITH selected AS (
 SELECT c.*,c.terminal_canonical::jsonb AS v,
 s.child_run_id,s.input_revision_id,s.definition_sha256,s.allocation_sha256,
 r.inputs_sha256
 FROM research_workflow_checkpoints c
 JOIN research_workflow_stages s ON s.owner_id=c.owner_id AND s.workflow_id=c.workflow_id AND s.id=c.stage_id
 JOIN research_workflow_revisions r ON r.owner_id=s.owner_id AND r.workflow_id=s.workflow_id AND r.id=s.input_revision_id
 WHERE c.owner_id=:owner AND c.workflow_id=:workflow AND c.id=:checkpoint)
"""
# Fixed, explicitly reviewed SELECTs. Missing rows are false; unknown is never true.
CHECKS = {
    "checkpoint_header": """SELECT count(*)=1 AND coalesce(bool_and(
 mineagent_workflow_raw(terminal_canonical,terminal_sha256,'rd_checkpoint') IS TRUE
 AND (v->>'owner_id',v->>'workflow_id',v->>'stage_id',v->>'input_revision_id',v->>'input_sha256',v->>'definition_sha256',v->>'allocation_sha256')
 IS NOT DISTINCT FROM (owner_id::text,workflow_id::text,stage_id::text,input_revision_id::text,inputs_sha256,definition_sha256,allocation_sha256)),false) FROM selected""",
    "child_attempt": """SELECT EXISTS(SELECT 1 FROM selected q JOIN research_runs x ON x.owner_id=q.owner_id AND x.id=q.child_run_id
 JOIN execution_attempts a ON a.run_id=x.id AND a.id=x.active_attempt_id
 WHERE x.id::text=q.v->>'child_run_id' AND x.status='succeeded' AND x.engine='rdagent' AND x.purpose='factor_research'
 AND a.id::text=q.v->>'attempt_id' AND a.generation=(q.v->>'generation')::integer AND x.generation=a.generation
 AND a.finished_at IS NOT NULL AND a.sandbox_stopped IS TRUE
 AND (SELECT count(*) FROM execution_attempts z WHERE z.run_id=x.id)=1)""",
    "modern_result_join": """SELECT EXISTS(SELECT 1 FROM selected q
 JOIN factor_workflow_submissions f ON f.owner_id=q.owner_id AND f.workflow_id=q.workflow_id AND f.stage_id=q.stage_id AND f.run_id=q.child_run_id
 JOIN factor_generation_starts g ON g.owner_id=f.owner_id AND g.run_id=f.run_id AND g.spec_id=f.spec_id
 JOIN factor_generation_sources src ON src.owner_id=g.owner_id AND src.run_id=g.run_id AND src.start_id=g.id
 JOIN factor_evaluation_starts ev ON ev.owner_id=g.owner_id AND ev.run_id=g.run_id AND ev.start_id=g.id
 JOIN factor_execution_evaluation_outcomes eo ON eo.evaluation_id=ev.id
 JOIN factor_execution_snapshot_publications snap ON snap.owner_id=g.owner_id AND snap.run_id=g.run_id AND snap.evaluation_id=ev.id
 JOIN factor_direct_analysis_intents ai ON ai.owner_id=f.owner_id AND ai.run_id=f.run_id AND ai.plan_id=f.plan_id AND ai.snapshot_id=snap.id
 JOIN factor_direct_analysis_outcomes ao ON ao.analysis_id=ai.id
 WHERE (f.id::text,f.spec_id::text,f.plan_id::text,g.id::text,src.id::text,src.factor_version_id::text,ev.id::text,snap.id::text,ai.id::text)
 IS NOT DISTINCT FROM (q.v->>'submission_id',q.v->>'spec_id',q.v->>'plan_id',q.v->>'start_id',q.v->>'source_id',q.v->>'factor_version_id',q.v->>'evaluation_id',q.v->>'snapshot_id',q.v->>'analysis_id')
 AND g.attempt_id::text=q.v->>'attempt_id' AND g.generation=(q.v->>'generation')::integer AND g.logical_step=0 AND g.task_index=0
 AND src.attempt_id=g.attempt_id AND src.generation=g.generation AND src.source_sha256=q.v->>'source_sha256'
 AND ev.evaluated_source_id=src.id AND ev.factor_version_id=src.factor_version_id AND eo.status='valid'
 AND ev.attempt_id=g.attempt_id AND ev.generation=g.generation
 AND (eo.owner_id,eo.run_id,eo.attempt_id,eo.generation)=(g.owner_id,g.run_id,g.attempt_id,g.generation)
 AND eo.generation_start_id=g.id AND eo.snapshot_id=snap.id AND eo.snapshot_manifest_sha256=snap.manifest_sha256
 AND eo.snapshot_input_sha256=snap.manifest->>'input_sha256' AND eo.snapshot_output_sha256=snap.manifest->>'output_rows_sha256'
 AND snap.attempt_id=g.attempt_id AND snap.generation=g.generation AND snap.start_id=g.id
 AND snap.source_id=src.id AND snap.factor_version_id=src.factor_version_id AND snap.manifest_sha256=q.v->>'snapshot_manifest_sha256'
 AND ao.result_sha256=q.v->>'analysis_result_sha256' AND ao.measurement_sha256=q.v->>'analysis_measurement_sha256')""",
    "returned_boundary": """SELECT EXISTS(SELECT 1 FROM selected q JOIN factor_execution_evaluation_outcomes eo ON eo.evaluation_id::text=q.v->>'evaluation_id'
 JOIN factor_generation_boundaries b ON b.id=eo.boundary_id JOIN factor_controller_launches l ON l.id=b.controller_launch_id
 WHERE (b.owner_id,b.run_id,b.attempt_id,b.generation)=(q.owner_id,q.child_run_id,(q.v->>'attempt_id')::uuid,(q.v->>'generation')::integer)
 AND (l.owner_id,l.run_id,l.attempt_id,l.generation)=(b.owner_id,b.run_id,b.attempt_id,b.generation)
 AND b.start_id::text=q.v->>'start_id' AND b.controller_launch_id=eo.controller_launch_id
 AND b.logical_step=0 AND b.boundary='returned' AND b.next_logical_step IS NULL AND l.admission_id::text=q.v->>'controller_admission_id')""",
    "process_membership": """SELECT EXISTS(SELECT 1 FROM selected q WHERE
 (SELECT count(*) FROM research_process_admissions p WHERE p.owner_id=q.owner_id AND p.run_id=q.child_run_id AND p.attempt_id::text=q.v->>'attempt_id' AND p.generation=(q.v->>'generation')::integer)=jsonb_array_length(q.v->'process_admission_ids')
 AND (SELECT count(DISTINCT value) FROM jsonb_array_elements(q.v->'process_admission_ids'))=jsonb_array_length(q.v->'process_admission_ids')
 AND NOT EXISTS(SELECT 1 FROM research_process_admissions p WHERE p.owner_id=q.owner_id AND p.run_id=q.child_run_id AND p.attempt_id::text=q.v->>'attempt_id' AND p.generation=(q.v->>'generation')::integer
 AND (NOT q.v->'process_admission_ids' @> jsonb_build_array(p.id::text) OR NOT EXISTS(SELECT 1 FROM research_process_states st JOIN research_process_receipts stop ON stop.admission_id=st.admission_id AND stop.kind='stop' WHERE st.admission_id=p.id AND st.status='stopped'))))""",
    "controller_receipt": """SELECT EXISTS(SELECT 1 FROM selected q JOIN factor_controller_launches l ON l.owner_id=q.owner_id AND l.run_id=q.child_run_id
 JOIN research_process_receipts stop ON stop.admission_id=l.admission_id AND stop.kind='stop'
 WHERE l.attempt_id::text=q.v->>'attempt_id' AND l.generation=(q.v->>'generation')::integer AND l.admission_id::text=q.v->>'controller_admission_id' AND stop.payload_sha256=q.v->>'controller_stop_sha256')""",
    "reservation_membership": """SELECT EXISTS(SELECT 1 FROM selected q WHERE
 (SELECT count(*) FROM research_budget_reservations b WHERE b.owner_id=q.owner_id AND b.run_id=q.child_run_id AND b.attempt_id::text=q.v->>'attempt_id' AND b.generation=(q.v->>'generation')::integer)=jsonb_array_length(q.v->'reservation_ids')
 AND (SELECT count(DISTINCT value) FROM jsonb_array_elements(q.v->'reservation_ids'))=jsonb_array_length(q.v->'reservation_ids')
 AND NOT EXISTS(SELECT 1 FROM research_budget_reservations b WHERE b.owner_id=q.owner_id AND b.run_id=q.child_run_id AND b.attempt_id::text=q.v->>'attempt_id' AND b.generation=(q.v->>'generation')::integer
 AND (b.state<>'settled' OR NOT q.v->'reservation_ids' @> jsonb_build_array(b.id::text) OR NOT EXISTS(SELECT 1 FROM research_budget_settlements st WHERE st.reservation_id=b.id))))""",
    "provider_closure": """SELECT EXISTS(SELECT 1 FROM selected q WHERE NOT EXISTS(
 SELECT 1 FROM research_model_transports t JOIN research_budget_reservations b ON b.id=t.reservation_id LEFT JOIN research_budget_settlements z ON z.reservation_id=b.id
 WHERE t.owner_id=q.owner_id AND t.run_id=q.child_run_id AND (t.state NOT IN ('completed','failed','unknown') OR b.state<>'settled' OR z.id IS NULL OR z.outcome<>'conservative_unknown' OR z.charge IS DISTINCT FROM b.amounts
 OR (SELECT array_agg(pr.kind ORDER BY pr.kind) FROM rd_provider_transport_receipts pr WHERE pr.transport_id=t.id) IS DISTINCT FROM ARRAY['intent','started','stopped']::varchar[])))""",
    "root_checkpoint_head": """SELECT EXISTS(SELECT 1 FROM selected q JOIN research_workflows r ON r.owner_id=q.owner_id AND r.id=q.workflow_id
 WHERE r.latest_checkpoint_id=q.id AND r.current_stage_id=q.stage_id AND r.status IN ('blocked','paused','completed','cancelled')
 AND NOT EXISTS(SELECT 1 FROM research_workflow_checkpoints c JOIN research_workflow_stages s ON s.id=c.stage_id AND s.owner_id=c.owner_id AND s.workflow_id=c.workflow_id
 WHERE c.owner_id=q.owner_id AND c.workflow_id=q.workflow_id AND s.ordinal>(SELECT ordinal FROM research_workflow_stages WHERE id=q.stage_id)))""",
}


def sqlstate(error):
    original = getattr(error, "orig", error)
    state = getattr(original, "sqlstate", None)
    return state if type(state) is str and state in STATES else "other"


def probe(connection, owner, workflow, checkpoint):
    if not all(type(value) is UUID for value in (owner, workflow, checkpoint)):
        return {"category": "invalid_identity"}
    params = {"owner": owner, "workflow": workflow, "checkpoint": checkpoint}
    records = []
    # Connection savepoints do not flush/commit the owning ORM session.
    savepoint = connection.begin_nested()
    try:
        rows = connection.execute(text(PIN_SQL), {"names": list(PINS)}).all()
        found = {}
        for name, body, security, volatility, setting in rows:
            if (
                name not in PINS
                or name in found
                or security is not False
                or volatility != PINS[name][1]
                or setting != PINS[name][2]
                or type(body) is not str
            ):
                return {"category": "installed_body_mismatch"}
            found[name] = (
                hashlib.sha256(body.encode()).hexdigest(),
                volatility,
                setting,
            )
        if found != PINS:
            return {"category": "installed_body_mismatch"}
    except Exception as error:  # noqa: BLE001 -- bounded metadata only
        return {"category": "catalog_error", "sqlstate": sqlstate(error)}
    finally:
        savepoint.rollback()
    queries = {
        "checkpoint_predicate": "SELECT mineagent_workflow_checkpoint(:owner,:workflow,:checkpoint)",
        "root_predicate": "SELECT mineagent_workflow_consistent(:owner,:workflow)",
        **{name: PREFIX + query for name, query in CHECKS.items()},
    }
    for name, query in queries.items():
        savepoint = connection.begin_nested()
        try:
            value = connection.execute(text(query), params).scalar_one()
            records.append(
                {"category": name, "value": value if type(value) is bool else "unknown"}
            )
        except Exception as error:  # noqa: BLE001 -- never emit raw exception
            records.append({"category": name, "sqlstate": sqlstate(error)})
        finally:
            savepoint.rollback()
    return {"category": "observed", "checks": records}


@contextmanager
def diagnose_workflow_checkpoint():
    from backend.app import research_workflow_rd_checkpoint as service

    original = service.observe_rd_terminal
    observations = []

    def observed(session, owner_id, workflow_id, *args, **kwargs):
        result = original(session, owner_id, workflow_id, *args, **kwargs)
        if not observations:
            try:
                observations.append(
                    probe(session.connection(), owner_id, workflow_id, result[0].id)
                )
            except Exception as error:  # noqa: BLE001 -- do not mask original commit
                observations.append(
                    {"category": "observer_error", "sqlstate": sqlstate(error)}
                )
        return result

    service.observe_rd_terminal = observed
    try:
        yield
    finally:
        service.observe_rd_terminal = original
        print(
            "WORKFLOW_CHECKPOINT_DIAGNOSTICS:"
            + json.dumps(observations, sort_keys=True)
        )

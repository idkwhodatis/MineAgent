"""Exact027 shared model/run/object successors; no live migration import.

Pinned predecessor bytes retain all RD, neutral and Python OE branches. Only
explicit normal structured config/protocol/kinds are added. Literal SQL is frozen
by the eventual assembler; this module is not called by Alembic at runtime.
"""

import hashlib

from backend.app.openevolve_revision_ddl import _check, historical
from backend.app.rdagent_provider_revision import changed, function
from packages.openevolve_structured.object_identity import KINDS

PINS = {
    "027c": "084ab22623c5c0ff3f9a61e94ea0db19be70292e2fd5deb6d30946396b388b2b",
    "025": "45fa55c439f829f3d7fadbbd7b4920148b3e5b647a4964014cd92150b71115d3",
    "026": "629d918feb22c39f943bc2a7022f77f605adca28fbcce0ce5868c250815f2e2c",
}
NORMAL = "mineagent.oe-structured-provider-run.v1"
PROTOCOL = "mineagent.oe-structured-provider-chat.v1"


def predecessor(revision):
    sql = historical(revision, "UPGRADE_SQL")
    if hashlib.sha256(sql.encode()).hexdigest() != PINS[revision]:
        raise ValueError("Exact reviewed026 predecessor required")
    return sql


def shared_function():
    sql = function(predecessor("027c"), "mineagent_rd_shared_consistent")
    # Two existing owner-wide checks retain the old Python grammar for every
    # other configuration, including unknown future schemas (not a wildcard).
    sql = changed(
        sql,
        "(rr.engine='openevolve' AND (op.protocol<>'mineagent.openevolve-chat.v1'",
        f"(rr.engine='openevolve' AND rr.config->>'schema_version' IS DISTINCT FROM '{NORMAL}' AND (op.protocol<>'mineagent.openevolve-chat.v1'",
    )
    sql = changed(
        sql,
        "(rr.engine='openevolve' AND (mineagent_oe_context(cx.context) IS NOT TRUE",
        f"(rr.engine='openevolve' AND rr.config->>'schema_version' IS DISTINCT FROM '{NORMAL}' AND (mineagent_oe_context(cx.context) IS NOT TRUE",
    )
    sql = changed(
        sql,
        "FOR r IN SELECT * FROM research_runs WHERE owner_id=owner_uuid AND engine='openevolve' LOOP",
        f"FOR r IN SELECT * FROM research_runs WHERE owner_id=owner_uuid AND engine='openevolve' AND config->>'schema_version' IS DISTINCT FROM '{NORMAL}' LOOP",
    )
    old = """OR (rr.engine='openevolve' AND EXISTS(
       SELECT 1 FROM search_iteration_intents st WHERE st.mutation_reservation_id=br.id
       AND (st.owner_id,st.run_id,st.attempt_id,st.generation)=(br.owner_id,br.run_id,br.attempt_id,br.generation)))"""
    new = f"""OR (rr.engine='openevolve' AND rr.config->>'schema_version' IS DISTINCT FROM '{NORMAL}' AND EXISTS(
       SELECT 1 FROM search_iteration_intents st WHERE st.mutation_reservation_id=br.id
       AND (st.owner_id,st.run_id,st.attempt_id,st.generation)=(br.owner_id,br.run_id,br.attempt_id,br.generation)))
       OR (rr.engine='openevolve' AND rr.config->>'schema_version'='{NORMAL}' AND EXISTS(
       SELECT 1 FROM oe_structured_sampling_intents st WHERE st.mutation_reservation_id=br.id
       AND (st.owner_id,st.run_id,st.attempt_id,st.generation)=(br.owner_id,br.run_id,br.attempt_id,br.generation)))"""
    sql = changed(sql, old, new)
    return changed(
        sql,
        " RETURN NULL;\nEND $$;",
        EXTRA.replace("@KINDS@", ",".join("'" + k + "'" for k in sorted(KINDS)))
        + "\n RETURN NULL;\nEND $$;",
    )


EXTRA = r"""
 IF EXISTS(SELECT 1 FROM research_object_publications p JOIN research_runs rr ON rr.id=p.run_id
  WHERE p.owner_id=owner_uuid AND
  ((p.object_kind IN (@KINDS@) AND rr.config->>'schema_version' IS DISTINCT FROM 'mineagent.oe-structured-provider-run.v1')
   OR (rr.config->>'schema_version'='mineagent.oe-structured-provider-run.v1' AND p.object_kind NOT IN (@KINDS@))))
 THEN RAISE EXCEPTION 'Structured publication kind/run mismatch'; END IF;
 IF EXISTS(SELECT 1 FROM research_model_operations op JOIN research_runs rr ON rr.id=op.run_id
  WHERE op.owner_id=owner_uuid AND rr.config->>'schema_version'='mineagent.oe-structured-provider-run.v1'
  AND (op.protocol IS DISTINCT FROM 'mineagent.oe-structured-provider-chat.v1'
   OR @SCHEMA@.mineagent_oes_provider_contract('request',op.request) IS NOT TRUE
   OR NOT EXISTS(SELECT 1 FROM oe_structured_provider_transports m WHERE m.operation_id=op.id
    AND (m.owner_id,m.run_id,m.attempt_id,m.generation)=(op.owner_id,op.run_id,op.attempt_id,op.generation))))
 THEN RAISE EXCEPTION 'Structured provider operation requires exact membership'; END IF;
 IF EXISTS(SELECT 1 FROM research_operation_contexts cx JOIN research_runs rr ON rr.id=cx.run_id
  WHERE cx.owner_id=owner_uuid AND rr.config->>'schema_version'='mineagent.oe-structured-provider-run.v1'
  AND NOT EXISTS(SELECT 1 FROM oe_structured_iteration_intents i
   WHERE (i.owner_id,i.run_id,i.attempt_id,i.generation)=(cx.owner_id,cx.run_id,cx.attempt_id,cx.generation)
   AND i.context_sha256=cx.context_sha256 AND i.context_canonical::jsonb=cx.context))
 THEN RAISE EXCEPTION 'Structured context requires actual rendered intent'; END IF;
 FOR r IN SELECT * FROM research_runs WHERE owner_id=owner_uuid
  AND config->>'schema_version'='mineagent.oe-structured-provider-run.v1' LOOP
  PERFORM @SCHEMA@.mineagent_oes_graph_check(r.id);
  SELECT * INTO b FROM research_budget_controls WHERE run_id=r.id;
  IF NOT FOUND OR b.owner_id IS DISTINCT FROM r.owner_id OR b.limits IS DISTINCT FROM r.config->'budget'
  THEN RAISE EXCEPTION 'Structured immutable global budget differs'; END IF;
 END LOOP;
"""


def neutral_model_function():
    sql = function(predecessor("026"), "mineagent_neutral_model_consistent")
    return changed(
        sql,
        "'mineagent.rdagent-provider-chat.v1','mineagent.rdagent-provider-embedding.v1'",
        "'mineagent.rdagent-provider-chat.v1','mineagent.rdagent-provider-embedding.v1','mineagent.oe-structured-provider-chat.v1'",
    )


def constraints():
    result = []
    old = predecessor("026")
    for table in ("research_objects", "research_object_publications"):
        name = f"ck_{table}_versioned_kind"
        values = ",".join("'" + kind + "'" for kind in sorted(KINDS))
        result.append(
            f"ALTER TABLE @SCHEMA@.{table} DROP CONSTRAINT {name}, ADD CONSTRAINT {name} CHECK (({_check(old, name)} OR object_kind IN ({values})) IS TRUE);"
        )
    name = "ck_model_versioned_request"
    result.append(
        f"ALTER TABLE @SCHEMA@.research_model_operations DROP CONSTRAINT {name}, ADD CONSTRAINT {name} CHECK (({_check(old, name)} OR (protocol='{PROTOCOL}' AND @SCHEMA@.mineagent_oes_provider_contract('request',request) IS TRUE)) IS TRUE);"
    )
    name = "ck_research_run_legacy_config"
    result.append(
        f"ALTER TABLE @SCHEMA@.research_runs DROP CONSTRAINT {name}, ADD CONSTRAINT {name} CHECK (({_check(predecessor('025'), name)} OR {RUN_BRANCH}) IS TRUE);"
    )
    return "\n".join(result)


RUN_BRANCH = r"""(purpose='strategy_search' AND engine='openevolve' AND strategy_version_id IS NULL AND generation=1
 AND experiment_id IS NULL AND split_version_id IS NULL AND final_selection_id IS NULL
 AND submission_binding IS NULL AND portable_policy_image_digest IS NULL
 AND latest_checkpoint_id IS NULL AND resume_checkpoint_id IS NULL
 AND config->>'schema_version'='mineagent.oe-structured-provider-run.v1'
 AND @SCHEMA@.mineagent_oes_contract('config',config) IS TRUE
 AND dataset_version_id::text=config->'fixed_inputs'->>'dataset_version_id'
 AND research_image_digest=config->'runtime'->>'controller_image_id')"""


def render_upgrade_sql():
    return f"{shared_function()}\n{neutral_model_function()}\n{constraints()}"


def render_downgrade_sql():
    result = [
        function(
            predecessor("027c" if name == "mineagent_rd_shared_consistent" else "026"),
            name,
        )
        for name in (
            "mineagent_rd_shared_consistent",
            "mineagent_neutral_model_consistent",
        )
    ]
    for table, name, revision in (
        ("research_objects", "ck_research_objects_versioned_kind", "026"),
        (
            "research_object_publications",
            "ck_research_object_publications_versioned_kind",
            "026",
        ),
        ("research_model_operations", "ck_model_versioned_request", "026"),
        ("research_runs", "ck_research_run_legacy_config", "025"),
    ):
        result.append(
            f"ALTER TABLE @SCHEMA@.{table} DROP CONSTRAINT {name}, ADD CONSTRAINT {name} CHECK {_check(predecessor(revision), name)};"
        )
    return "\n".join(result)

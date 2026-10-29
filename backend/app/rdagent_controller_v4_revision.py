"""Seeded-only controller v4; frozen historical functions restored on downgrade."""

import ast
import hashlib
import re
from pathlib import Path

V3 = "mineagent.rdagent-orchestrator.v3"
V4 = "mineagent.rdagent-orchestrator.v4"
PINS = {
    "027_rd_controller_v3.py": "01fc04b5bcb0dcb3f691f5a6c5f42a07c00ba7f363ee7ff77c8d92880ef5c971",
    "027a_contract_repairs.py": "bb5492b3a451330bb6317352dd400cf937c4b4219def083d0146d4d5dd403ba6",
    "027b_provider_material.py": "c4b9d0f8dd2b79ea91736f89a5d74c422e82e48d8f7db9468f03f40acbab592c",
}
SOURCES = {
    "mineagent_rd_controller_selection": "027_rd_controller_v3.py",
    "mineagent_rd_controller_profile_matches": "027_rd_controller_v3.py",
    "mineagent_rd_provider_config": "027_rd_controller_v3.py",
    "mineagent_rd_source_config": "027_rd_controller_v3.py",
    "mineagent_rd_shared_consistent": "027_rd_controller_v3.py",
    "mineagent_rd_controller_receipt_order": "027_rd_controller_v3.py",
    "mineagent_rd_controller_consistent": "027_rd_controller_v3.py",
    "mineagent_rd_operational_actor": "027_rd_controller_v3.py",
    "mineagent_rd_operational_boundary_consistent": "027_rd_controller_v3.py",
    "mineagent_rd_execution_generation_actor": "027_rd_controller_v3.py",
    "mineagent_rd_provider_graph": "027b_provider_material.py",
    "mineagent_rd_fixture_source_violation_026": "027_rd_controller_v3.py",
    "mineagent_rd_provider_source_violation": "027_rd_controller_v3.py",
    "mineagent_rd_provider_queued_config": "027_rd_controller_v3.py",
    "mineagent_rd_seed_check": "027_rd_controller_v3.py",
}


def frozen(name):
    filename = SOURCES[name]
    module = ast.parse(
        (Path(__file__).parents[1] / "alembic/versions" / filename).read_text()
    )
    sql = next(
        ast.literal_eval(n.value)
        for n in module.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "UPGRADE_SQL" for t in n.targets)
    )
    if hashlib.sha256(sql.encode()).hexdigest() != PINS[filename]:
        raise ValueError("Frozen controller predecessor changed")
    matches = list(
        re.finditer(
            r"CREATE (?:OR REPLACE )?FUNCTION (?:@SCHEMA@\.)?"
            + re.escape(name)
            + r"\(",
            sql,
        )
    )
    if len(matches) != 1:
        raise ValueError("Exact predecessor function required")
    start = matches[0].start()
    return sql[start : sql.index("$$;", start) + 3].replace(
        "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
    )


def once(sql, old, new):
    if sql.count(old) != 1:
        raise ValueError("Exact v4 predecessor fragment required: " + old)
    return sql.replace(old, new, 1)


def render_upgrade_sql():
    result = []
    for name in SOURCES:
        sql = frozen(name)
        if name == "mineagent_rd_controller_selection":
            sql = once(
                sql,
                " ELSE NULL END",
                " WHEN v->>'controller_profile'='"
                + V4
                + "' THEN jsonb_build_object('controller_profile','"
                + V4
                + "')\n ELSE NULL END",
            )
        elif name == "mineagent_rd_controller_profile_matches":
            sql = once(
                sql,
                "v->'controller_profile'='\""
                + V3
                + "\"'::jsonb\n   AND profile='"
                + V3
                + "'",
                "v->>'controller_profile' IN ('"
                + V3
                + "','"
                + V4
                + "') AND jsonb_typeof(v->'controller_profile')='string' AND profile=v->>'controller_profile'",
            )
        else:
            # Expand exact profile membership lists only, never plain equality.
            sql = sql.replace("'" + V3 + "')", "'" + V3 + "','" + V4 + "')")
        if name == "mineagent_rd_controller_consistent":
            sql = once(
                sql,
                "CASE WHEN a.profile='" + V3 + "' THEN 120000",
                "CASE WHEN a.profile='"
                + V4
                + "' THEN 180000 WHEN a.profile='"
                + V3
                + "' THEN 120000",
            )
            sql = once(
                sql,
                "CASE WHEN a.profile='" + V3 + "' THEN interval '120 seconds'",
                "CASE WHEN a.profile='"
                + V4
                + "' THEN interval '180 seconds' WHEN a.profile='"
                + V3
                + "' THEN interval '120 seconds'",
            )
        if name == "mineagent_rd_provider_queued_config":
            sql = once(
                sql,
                "CASE WHEN v->>'controller_profile'='" + V3 + "' THEN 300000",
                "CASE WHEN v->>'controller_profile'='"
                + V4
                + "' THEN 360000 WHEN v->>'controller_profile'='"
                + V3
                + "' THEN 300000",
            )
        if name == "mineagent_rd_provider_config":
            sql = once(
                sql,
                ">=300000+32*",
                ">=(CASE WHEN v->>'controller_profile'='"
                + V4
                + "' THEN 360000 ELSE 300000 END)+32*",
            )
        if name == "mineagent_rd_source_config":
            sql = once(
                sql,
                " IF NOT (v ? 'controller_profile')",
                " IF v->>'controller_profile'='"
                + V4
                + "' THEN RETURN false; END IF;\n IF NOT (v ? 'controller_profile')",
            )
        if name == "mineagent_rd_seed_check":
            # Runtime declaration projection is request-specific. Fresh profiles
            # omit seeded_controller_profile; v4 seeds retain both explicit keys.
            marker = "'model_profile','configured.rdagent.v1','runtime',r.config->'runtime','qlib_installed_parent_image_id',p.qlib_installed_parent_image_id) || mineagent_rd_controller_selection(r.config))"
            sql = once(
                sql,
                marker,
                marker.replace(
                    "mineagent_rd_controller_selection(r.config)",
                    "CASE WHEN r.config->>'controller_profile'='"
                    + V4
                    + "' THEN jsonb_build_object('controller_profile','"
                    + V3
                    + "','seeded_controller_profile','"
                    + V4
                    + "') ELSE mineagent_rd_controller_selection(r.config) END",
                ),
            )
        if name == "mineagent_rd_provider_graph":
            sql = once(
                sql,
                " SELECT * INTO run FROM research_runs WHERE id=rid;",
                " SELECT * INTO run FROM research_runs WHERE id=rid;\n IF run.config->>'controller_profile'='mineagent.rdagent-orchestrator.v4' THEN\n  PERFORM mineagent_rd_v4_seed_binding(rid);\n END IF;",
            )
        result.append(sql)
    return SEED_SQL + "\n" + "\n".join(result) + GATE_UP + TRIGGERS


def render_downgrade_sql():
    return (
        DOWN_GUARD
        + "\n"
        + "\n".join(frozen(name) for name in SOURCES)
        + GATE_DOWN
        + CLEANUP
    )


SEED_SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_rd_v4_seed_binding(rid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE r research_runs%ROWTYPE;
BEGIN
 SELECT * INTO r FROM research_runs WHERE id=rid;
 IF r.config->>'controller_profile' IS DISTINCT FROM 'mineagent.rdagent-orchestrator.v4' THEN RETURN; END IF;
 IF (r.engine,r.purpose,r.config->>'schema_version') IS DISTINCT FROM ('rdagent','factor_research','mineagent.factor-research.v2')
 OR (SELECT count(*) FROM factor_seeded_submissions s JOIN factor_versions v ON v.id=s.parent_factor_version_id
 JOIN factor_objects o ON o.id=s.parent_code_object_id
 WHERE s.run_id=r.id AND s.owner_id=r.owner_id AND v.owner_id=r.owner_id AND o.owner_id=r.owner_id
 AND v.factor_id=s.parent_factor_id AND v.ordinal=s.parent_ordinal
 AND v.code_object_id=o.id AND v.code_sha256=s.parent_code_sha256
 AND v.definition_sha256=s.parent_definition_sha256 AND o.sha256=s.parent_code_sha256)<>1
 OR EXISTS(SELECT 1 FROM factor_research_submissions WHERE run_id=r.id)
 THEN RAISE EXCEPTION 'Controller v4 requires exact provider seeded parent binding'; END IF;
END $$;
CREATE FUNCTION @SCHEMA@.mineagent_rd_v4_seed_deferred() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE rid uuid; sid uuid; ids uuid[];
BEGIN
 IF TG_TABLE_NAME='research_runs' THEN ids:=ARRAY[NEW.id,OLD.id];
 ELSE ids:=ARRAY[NEW.run_id,OLD.run_id]; END IF;
 FOR rid IN SELECT DISTINCT value FROM unnest(ids) value WHERE value IS NOT NULL LOOP
 PERFORM mineagent_rd_v4_seed_binding(rid);
 IF EXISTS(SELECT 1 FROM research_runs WHERE id=rid AND config->>'controller_profile'='mineagent.rdagent-orchestrator.v4') THEN
  FOR sid IN SELECT id FROM factor_seeded_submissions WHERE run_id=rid LOOP
   PERFORM mineagent_rd_seed_check(sid);
  END LOOP;
 END IF;
 END LOOP;
 RETURN NULL;
END $$;
"""
TRIGGERS = "\n".join(
    "CREATE CONSTRAINT TRIGGER rd_v4_seed_binding AFTER INSERT OR UPDATE OR DELETE ON @SCHEMA@."
    + name
    + " DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_v4_seed_deferred();"
    for name in (
        "research_runs",
        "factor_seeded_submissions",
        "factor_research_submissions",
        "research_process_admissions",
    )
)
CLEANUP = (
    "\n".join(
        "DROP TRIGGER rd_v4_seed_binding ON @SCHEMA@." + name + ";"
        for name in (
            "research_runs",
            "factor_seeded_submissions",
            "factor_research_submissions",
            "research_process_admissions",
        )
    )
    + "\nDROP FUNCTION @SCHEMA@.mineagent_rd_v4_seed_deferred();\nDROP FUNCTION @SCHEMA@.mineagent_rd_v4_seed_binding(uuid);"
)
DOWN_GUARD = """DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM @SCHEMA@.research_process_admissions WHERE profile='mineagent.rdagent-orchestrator.v4')
 OR EXISTS(SELECT 1 FROM @SCHEMA@.research_runs WHERE config->>'controller_profile'='mineagent.rdagent-orchestrator.v4')
 THEN RAISE EXCEPTION 'Retained controller v4 data forbids downgrade'; END IF;
END $$;
"""


def previous_gate():
    module = ast.parse(
        (
            Path(__file__).parents[1] / "alembic/versions/027_rd_controller_v3.py"
        ).read_text()
    )
    sql = next(
        ast.literal_eval(n.value)
        for n in module.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "UPGRADE_SQL" for t in n.targets)
    )
    if hashlib.sha256(sql.encode()).hexdigest() != PINS["027_rd_controller_v3.py"]:
        raise ValueError("Frozen process gate changed")
    start = sql.index(
        "ALTER TABLE @SCHEMA@.research_process_admissions DROP CONSTRAINT ck_process_v2_unregistered"
    )
    return sql[start : sql.index(";", start) + 1]


GATE_DOWN = previous_gate()
GATE_UP = once(GATE_DOWN, "'" + V3 + "')", "'" + V3 + "','" + V4 + "')")

"""Explicit027 controller-v3 deltas; frozen026 semantics remain recoverable."""

import ast
import hashlib
import re
from pathlib import Path

SOURCES = {
    "mineagent_rd_source_config": ["026_rd_seeded_submissions.py", "UPGRADE_SQL"],
    "mineagent_rd_provider_config": ["026_rd_seeded_submissions.py", "UPGRADE_SQL"],
    "mineagent_rd_shared_consistent": ["026_rd_seeded_submissions.py", "UPGRADE_SQL"],
    "mineagent_rd_controller_receipt_order": [
        "024_openevolve_initial.py",
        "UPGRADE_SQL",
    ],
    "mineagent_rd_controller_consistent": [
        "026_rd_seeded_submissions.py",
        "UPGRADE_SQL",
    ],
    "mineagent_rd_operational_actor": ["020_rd_controller.py", "UPGRADE_SQL"],
    "mineagent_rd_operational_boundary_consistent": [
        "026_rd_seeded_submissions.py",
        "UPGRADE_SQL",
    ],
    "mineagent_rd_execution_generation_actor": [
        "021_execution_snapshots.py",
        "RD_UPGRADE_SQL",
    ],
    "mineagent_rd_provider_graph": ["026_rd_seeded_submissions.py", "UPGRADE_SQL"],
    "mineagent_rd_fixture_source_violation_026": [
        "026_rd_seeded_submissions.py",
        "UPGRADE_SQL",
    ],
    "mineagent_rd_provider_source_violation": [
        "026_rd_seeded_submissions.py",
        "UPGRADE_SQL",
    ],
    "mineagent_rd_provider_queued_config": [
        "026_rd_seeded_submissions.py",
        "UPGRADE_SQL",
    ],
    "mineagent_rd_provider_submission_check": [
        "026_rd_seeded_submissions.py",
        "UPGRADE_SQL",
    ],
    "mineagent_rd_fixture_submission_check_026": [
        "026_rd_seeded_submissions.py",
        "UPGRADE_SQL",
    ],
    "mineagent_rd_seed_check": ["026_rd_seeded_submissions.py", "UPGRADE_SQL"],
}
PINS = {
    (
        "026_rd_seeded_submissions.py",
        "UPGRADE_SQL",
    ): "629d918feb22c39f943bc2a7022f77f605adca28fbcce0ce5868c250815f2e2c",
    (
        "024_openevolve_initial.py",
        "UPGRADE_SQL",
    ): "521554c544e96012b9c05606f364cc80959bae93113eba5225d045d34cc3feb5",
    (
        "020_rd_controller.py",
        "UPGRADE_SQL",
    ): "0356ba7a550b5432fdd9dc675f1b62bf7672b546c1196dfdbc68a9d855a0d069",
    (
        "021_execution_snapshots.py",
        "RD_UPGRADE_SQL",
    ): "d3a986ad14306e494d5aba5e2c2e6049fe5941d8781321922beaae7b56fe2d16",
}

LEGACY = "mineagent.rdagent-orchestrator.v2"
COMPOSED = "mineagent.rdagent-orchestrator.v3"


def frozen(name):
    filename, key = SOURCES[name]
    module = ast.parse(
        (Path(__file__).parents[1] / "alembic/versions" / filename).read_text()
    )
    sql = next(
        ast.literal_eval(n.value)
        for n in module.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == key for t in n.targets)
    )
    if hashlib.sha256(sql.encode()).hexdigest() != PINS[filename, key]:
        raise ValueError("Frozen controller predecessor changed")
    matches = list(
        re.finditer(
            r"CREATE(?: OR REPLACE)? FUNCTION (?:@SCHEMA@\.)?"
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


def broaden_profiles(sql):
    # These exact predicate spellings occur in pinned historical functions only.
    sql = re.sub(
        r"(\b(?:\w+\.)?profile) IS DISTINCT FROM 'mineagent\.rdagent-orchestrator\.v2'",
        r"(\1 IS NULL OR \1 NOT IN ('mineagent.rdagent-orchestrator.v2','mineagent.rdagent-orchestrator.v3'))",
        sql,
    )
    sql = re.sub(
        r"(\b(?:\w+\.)?profile)='mineagent\.rdagent-orchestrator\.v2'",
        r"\1 IN ('mineagent.rdagent-orchestrator.v2','mineagent.rdagent-orchestrator.v3')",
        sql,
    )
    sql = sql.replace(
        "'mineagent.rdagent-orchestrator.v1','mineagent.rdagent-orchestrator.v2')",
        "'mineagent.rdagent-orchestrator.v1','mineagent.rdagent-orchestrator.v2','mineagent.rdagent-orchestrator.v3')",
    )
    return sql


def render_upgrade_sql():
    previous_provider = frozen("mineagent_rd_provider_config").replace(
        "mineagent_rd_provider_config(", "mineagent_rd_provider_config_026_frozen(", 1
    )
    result = [broaden_profiles(previous_gate()), previous_provider, PROFILE_SQL]
    for name in SOURCES:
        if name in {"mineagent_rd_source_config", "mineagent_rd_provider_config"}:
            continue
        sql = broaden_profiles(frozen(name))
        if name == "mineagent_rd_controller_consistent":
            if (
                sql.count("(br.amounts->>'wall_milliseconds')::numeric<60000") != 1
                or sql.count(">interval '60 seconds'") != 1
            ):
                raise ValueError(
                    "Exact predecessor controller budget/deadline required"
                )
            sql = sql.replace(
                "(br.amounts->>'wall_milliseconds')::numeric<60000",
                "(br.amounts->>'wall_milliseconds')::numeric < (CASE WHEN a.profile='mineagent.rdagent-orchestrator.v3' THEN 120000 ELSE 60000 END)",
            )
            sql = sql.replace(
                ">interval '60 seconds'",
                "> (CASE WHEN a.profile='mineagent.rdagent-orchestrator.v3' THEN interval '120 seconds' ELSE interval '60 seconds' END)",
            )
        if name == "mineagent_rd_provider_queued_config":
            if sql.count("<240000+32*") != 1:
                raise ValueError("Exact predecessor provider admission floor required")
            sql = sql.replace(
                "<240000+32*",
                "< (CASE WHEN v->>'controller_profile'='mineagent.rdagent-orchestrator.v3' THEN 300000 ELSE 240000 END)+32*",
            )
        marker = "'qlib_installed_parent_image_id',p.qlib_installed_parent_image_id))"
        if name in {
            "mineagent_rd_provider_submission_check",
            "mineagent_rd_fixture_submission_check_026",
            "mineagent_rd_seed_check",
        }:
            expected = 2 if name == "mineagent_rd_seed_check" else 1
            if sql.count(marker) != expected:
                raise ValueError("Exact predecessor profile hash document required")
            sql = sql.replace(
                marker,
                "'qlib_installed_parent_image_id',p.qlib_installed_parent_image_id) || mineagent_rd_controller_selection(r.config))",
            )
        result.append(sql)
    result.append(BINDING_SQL)
    return "\n".join(result)


def render_downgrade_sql():
    return (
        DOWN_GUARD
        + "\n"
        + "\n".join(frozen(name) for name in SOURCES)
        + DOWN_CLEANUP
        + "\n"
        + previous_gate()
    )


PROFILE_SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_rd_controller_selection(v jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=@SCHEMA@,pg_catalog AS $$
 SELECT CASE WHEN jsonb_typeof(v) IS DISTINCT FROM 'object' THEN NULL
 WHEN NOT (v ? 'controller_profile') THEN '{}'::jsonb
 WHEN v->'controller_profile'='"mineagent.rdagent-orchestrator.v3"'::jsonb
 THEN jsonb_build_object('controller_profile','mineagent.rdagent-orchestrator.v3')
 ELSE NULL END
$$;
CREATE FUNCTION @SCHEMA@.mineagent_rd_controller_profile_matches(v jsonb, profile text) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=@SCHEMA@,pg_catalog AS $$
 SELECT (jsonb_typeof(v)='object' AND CASE WHEN v ? 'controller_profile' THEN
   v->'controller_profile'='"mineagent.rdagent-orchestrator.v3"'::jsonb
   AND profile='mineagent.rdagent-orchestrator.v3'
 ELSE profile IN ('mineagent.rdagent-orchestrator.v1','mineagent.rdagent-orchestrator.v2') END) IS TRUE
$$;
CREATE OR REPLACE FUNCTION @SCHEMA@.mineagent_rd_provider_config(v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=@SCHEMA@,pg_catalog AS $$
BEGIN
 IF NOT (v ? 'controller_profile') THEN RETURN mineagent_rd_provider_config_026_frozen(v); END IF;
 RETURN (mineagent_rd_controller_selection(v) IS NOT NULL
  AND mineagent_rd_json_bytes(v,true)<=1048576
  AND mineagent_rd_provider_config_026_frozen(v-'controller_profile') IS TRUE
  AND v->>'max_iterations'='1'
  AND (v#>>'{budget,wall_milliseconds}')::numeric>=300000+32*(v#>>'{model_binding,request_deadline_ms}')::numeric) IS TRUE;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
CREATE OR REPLACE FUNCTION @SCHEMA@.mineagent_rd_source_config(v jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=@SCHEMA@,pg_catalog AS $$
BEGIN
 IF v->>'schema_version'='mineagent.factor-research.v2' THEN RETURN mineagent_rd_provider_config(v); END IF;
 IF NOT (v ? 'controller_profile') THEN RETURN mineagent_rd_fixture_config_026(v); END IF;
 RETURN (mineagent_rd_controller_selection(v) IS NOT NULL
  AND mineagent_rd_json_bytes(v,true)<=1048576
  AND mineagent_rd_fixture_config_026(v-'controller_profile') IS TRUE
  AND v->>'max_iterations'='1'
  AND (v#>>'{budget,wall_milliseconds}')::numeric>=300000) IS TRUE;
EXCEPTION WHEN OTHERS THEN RETURN false;
END $$;
"""


def previous_gate():
    module = ast.parse(
        (
            Path(__file__).parents[1]
            / "alembic/versions/025_structured_factor_strategy.py"
        ).read_text()
    )
    sql = next(
        ast.literal_eval(n.value)
        for n in module.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "UPGRADE_SQL" for t in n.targets)
    )
    if (
        hashlib.sha256(sql.encode()).hexdigest()
        != "45fa55c439f829f3d7fadbbd7b4920148b3e5b647a4964014cd92150b71115d3"
    ):
        raise ValueError("Frozen025 process gate changed")
    start = sql.index(
        "ALTER TABLE @SCHEMA@.research_process_admissions DROP CONSTRAINT ck_process_v2_unregistered"
    )
    return sql[start : sql.index(";", start) + 1]


BINDING_SQL = r"""
CREATE FUNCTION @SCHEMA@.mineagent_rd_controller_profile_binding() RETURNS trigger
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE r research_runs%ROWTYPE; a research_process_admissions%ROWTYPE;
BEGIN
 SELECT * INTO a FROM research_process_admissions WHERE id=NEW.id;
 IF NOT FOUND THEN RETURN NULL; END IF;
 SELECT * INTO r FROM research_runs WHERE id=a.run_id AND owner_id=a.owner_id;
 IF r.engine='rdagent' AND a.role='rd_orchestrator' THEN
  IF mineagent_rd_controller_profile_matches(r.config,a.profile) IS NOT TRUE
   OR ((r.config ? 'controller_profile') AND mineagent_rd_source_config(r.config) IS NOT TRUE)
  THEN RAISE EXCEPTION 'RD controller version/config binding differs'; END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER rd_controller_profile_binding AFTER INSERT OR UPDATE ON @SCHEMA@.research_process_admissions
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_controller_profile_binding();
"""

DOWN_GUARD = r"""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM @SCHEMA@.research_process_admissions WHERE profile='mineagent.rdagent-orchestrator.v3')
 OR EXISTS(SELECT 1 FROM @SCHEMA@.research_runs WHERE engine='rdagent' AND config ? 'controller_profile')
 THEN RAISE EXCEPTION 'Controller v3 data requires revision027; downgrade refused'; END IF;
END $$;
DROP TRIGGER rd_controller_profile_binding ON @SCHEMA@.research_process_admissions;
DROP FUNCTION @SCHEMA@.mineagent_rd_controller_profile_binding();
"""
DOWN_CLEANUP = r"""
DROP FUNCTION @SCHEMA@.mineagent_rd_provider_config_026_frozen(jsonb);
DROP FUNCTION @SCHEMA@.mineagent_rd_controller_selection(jsonb);
DROP FUNCTION @SCHEMA@.mineagent_rd_controller_profile_matches(jsonb,text);
"""

"""026 explicit successors of pinned historical predicates; old literals stay intact."""

import ast
import hashlib
import re
from pathlib import Path

PINS = {
    (
        "015_neutral_primitives.py",
        "BUDGET_SQL",
    ): "ee040eb53caa39281d02ec192417e44ce2cd79d37e35242013a8f0b6cea5ea2f",
    (
        "018_rd_source_persistence.py",
        "UPGRADE_SQL",
    ): "57047b1e2de302c2cc19e4f64cd974f89555ebc201fc9e90fc3cc727bf966715",
    (
        "020_rd_controller.py",
        "UPGRADE_SQL",
    ): "0356ba7a550b5432fdd9dc675f1b62bf7672b546c1196dfdbc68a9d855a0d069",
    (
        "023_rd_submissions.py",
        "UPGRADE_SQL",
    ): "9cf5b775ea1faf0a1116bf825700d0a9a80c24425a0f18b98f2674f87eb56492",
    (
        "024_openevolve_initial.py",
        "UPGRADE_SQL",
    ): "521554c544e96012b9c05606f364cc80959bae93113eba5225d045d34cc3feb5",
    (
        "025_structured_factor_strategy.py",
        "UPGRADE_SQL",
    ): "45fa55c439f829f3d7fadbbd7b4920148b3e5b647a4964014cd92150b71115d3",
}


def literal(filename, key="UPGRADE_SQL"):
    tree = ast.parse(
        (Path(__file__).parents[1] / "alembic/versions" / filename).read_text()
    )
    result = next(
        ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == key for t in n.targets)
    )
    if hashlib.sha256(result.encode()).hexdigest() != PINS[filename, key]:
        raise ValueError(
            "Historical predicate bytes changed; review before composing026"
        )
    return result


def function(sql, name):
    matches = list(
        re.finditer(
            r"CREATE(?: OR REPLACE)? FUNCTION (?:@SCHEMA@\.)?"
            + re.escape(name)
            + r"\(",
            sql,
        )
    )
    if len(matches) != 1:
        raise ValueError("Exact historical function inventory required: " + name)
    start = matches[0].start()
    result = sql[start : sql.index("$$;", start) + 3]
    return result.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def changed(sql, old, new):
    if sql.count(old) != 1:
        raise ValueError("Exact reviewed historical clause required: " + old)
    return sql.replace(old, new)


def schema_boolean_sql():
    """026 retains exact025 numeric/boolean schema support; no duplicate branch."""
    return function(literal("025_structured_factor_strategy.py"), "mineagent_rd_schema")


def request_config_sql():
    source = literal("018_rd_source_persistence.py")
    config = function(source, "mineagent_rd_source_config")
    request = function(source, "mineagent_rd_source_request")
    return "\n".join(
        (
            config.replace(
                "mineagent_rd_source_config(", "mineagent_rd_fixture_config_026("
            ),
            request.replace(
                "mineagent_rd_source_request(", "mineagent_rd_fixture_request_026("
            ),
            r"""
CREATE OR REPLACE FUNCTION @SCHEMA@.mineagent_rd_source_config(v jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=@SCHEMA@,pg_catalog AS $$
 SELECT CASE v->>'schema_version' WHEN 'mineagent.factor-research.v2' THEN mineagent_rd_provider_config(v)
 ELSE mineagent_rd_fixture_config_026(v) END
$$;
CREATE OR REPLACE FUNCTION @SCHEMA@.mineagent_rd_source_request(v jsonb,protocol text) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=@SCHEMA@,pg_catalog AS $$
 SELECT coalesce(CASE protocol
 WHEN 'mineagent.rdagent-provider-chat.v1' THEN mineagent_rd_provider_payload('chat',v)
 WHEN 'mineagent.rdagent-provider-embedding.v1' THEN mineagent_rd_provider_payload('embedding',v)
 ELSE mineagent_rd_fixture_request_026(v,protocol) END
 AND mineagent_rd_json_bytes(v,false)<=262144,false)
$$;
""",
        )
    )


def neutral_model_sql():
    original = function(
        literal("015_neutral_primitives.py", "BUDGET_SQL"),
        "mineagent_neutral_model_consistent",
    )
    return changed(
        original,
        "((o.outcome='unknown') IS DISTINCT FROM (s.outcome='conservative_unknown'))",
        """(CASE WHEN EXISTS(SELECT 1 FROM research_model_operations op WHERE op.id=t.operation_id
       AND op.protocol IN ('mineagent.rdagent-provider-chat.v1','mineagent.rdagent-provider-embedding.v1'))
      THEN s.outcome IS DISTINCT FROM 'conservative_unknown' OR s.charge IS DISTINCT FROM r.amounts
      ELSE ((o.outcome='unknown') IS DISTINCT FROM (s.outcome='conservative_unknown')) END)""",
    )


def source_sql():
    original = function(
        literal("020_rd_controller.py"), "mineagent_rd_source_violation"
    )
    provider = original.replace(
        "mineagent_rd_source_violation(", "mineagent_rd_provider_source_violation("
    )
    provider = changed(
        provider,
        "w.parser_profile<>'mineagent.rdagent-source-parser.v1'",
        "w.parser_profile<>'mineagent.rdagent-provider-source-parser.v1'",
    )
    provider = changed(
        provider,
        "'mineagent.rdagent-chat.v1'::varchar",
        "'mineagent.rdagent-provider-chat.v1'::varchar",
    )
    provider = changed(
        provider,
        "op.request->>'model' IS DISTINCT FROM 'fixture.rdagent.v1'",
        "op.request->>'model' IS DISTINCT FROM (SELECT config#>>'{model_binding,chat_model}' FROM @SCHEMA@.research_runs WHERE id=s.run_id)",
    )
    provider = changed(
        provider, "count_transports NOT BETWEEN 1 AND 256", "count_transports<>1"
    )
    provider = changed(
        provider,
        "bs.outcome<>'measured'",
        "(bs.outcome<>'conservative_unknown' OR bs.charge IS DISTINCT FROM br.amounts)",
    )
    return "\n".join(
        (
            original.replace(
                "mineagent_rd_source_violation(",
                "mineagent_rd_fixture_source_violation_026(",
            ),
            provider,
            r"""
CREATE OR REPLACE FUNCTION @SCHEMA@.mineagent_rd_source_violation(source_uuid uuid) RETURNS text
LANGUAGE plpgsql VOLATILE SET search_path=@SCHEMA@,pg_catalog AS $$
DECLARE rid uuid;
BEGIN
 SELECT s.run_id INTO rid FROM factor_generation_sources s JOIN research_runs r ON r.id=s.run_id
 WHERE s.id=source_uuid AND r.config->>'schema_version'='mineagent.factor-research.v2';
 IF rid IS NOT NULL THEN
  PERFORM mineagent_rd_provider_graph(rid);
  RETURN mineagent_rd_provider_source_violation(source_uuid);
 END IF;
 RETURN mineagent_rd_fixture_source_violation_026(source_uuid);
END $$;
""",
        )
    )


def boundary_sql():
    original = function(
        literal("020_rd_controller.py"), "mineagent_rd_operational_boundary_consistent"
    )
    if "mo.operation_id" in original or original.count("mo.transport_id=mt.id") != 1:
        raise ValueError("Reviewed completed transport join required")
    return changed(
        original,
        "m.protocol=CASE c.kind WHEN 'chat' THEN 'mineagent.rdagent-chat.v1' ELSE 'mineagent.rdagent-embedding.v1' END",
        """m.protocol=(CASE WHEN cl.bootstrap#>>'{config,schema_version}'='mineagent.factor-research.v2'
      THEN (CASE c.kind WHEN 'chat' THEN 'mineagent.rdagent-provider-chat.v1' ELSE 'mineagent.rdagent-provider-embedding.v1' END)
      ELSE (CASE c.kind WHEN 'chat' THEN 'mineagent.rdagent-chat.v1' ELSE 'mineagent.rdagent-embedding.v1' END) END)""",
    )


def submission_sql():
    original = function(
        literal("023_rd_submissions.py"), "mineagent_rd_submission_check"
    )
    return "\n".join(
        (
            original.replace(
                "mineagent_rd_submission_check(",
                "mineagent_rd_fixture_submission_check_026(",
            ),
            r"""
CREATE OR REPLACE FUNCTION @SCHEMA@.mineagent_rd_submission_check(sid uuid) RETURNS void
LANGUAGE plpgsql SET search_path=@SCHEMA@,pg_catalog AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM factor_research_submissions s JOIN research_runs r ON r.id=s.run_id
  WHERE s.id=sid AND r.config->>'schema_version'='mineagent.factor-research.v2')
 THEN PERFORM mineagent_rd_provider_submission_check(sid);
 ELSE PERFORM mineagent_rd_fixture_submission_check_026(sid); END IF;
END $$;
""",
        )
    )


def kind_request_checks():
    old = literal("024_openevolve_initial.py")
    kinds = [line for line in old.splitlines() if "versioned_kind CHECK" in line]
    if len(kinds) != 2:
        raise ValueError("Exact predecessor kind inventory required")
    result = [
        line.replace(
            "'process_receipt',", "'process_receipt','rd_provider_process_receipt',"
        )
        for line in kinds
    ]
    start = old.index(
        "ALTER TABLE @SCHEMA@.research_model_operations DROP CONSTRAINT ck_model_versioned_request"
    )
    clause = old[start : old.index(";", start) + 1]
    clause = changed(
        clause,
        "'mineagent.rdagent-chat.v1','mineagent.rdagent-embedding.v1'",
        "'mineagent.rdagent-chat.v1','mineagent.rdagent-embedding.v1','mineagent.rdagent-provider-chat.v1','mineagent.rdagent-provider-embedding.v1'",
    )
    result.append(clause)
    result.append(r"""
ALTER TABLE @SCHEMA@.factor_research_specs DROP CONSTRAINT ec_f57efc3d280cd55d4424ea18,
 ADD CONSTRAINT ec_f57efc3d280cd55d4424ea18 CHECK ((jsonb_typeof(config)='object' AND config->>'schema_version' IN ('mineagent.factor-research.v1','mineagent.factor-research.v2')) IS TRUE);
ALTER TABLE @SCHEMA@.factor_generation_model_operations DROP CONSTRAINT ec_db3ca75700a1080c80e65a84,
 ADD CONSTRAINT ec_db3ca75700a1080c80e65a84 CHECK (protocol IN ('mineagent.rdagent-chat.v1','mineagent.rdagent-embedding.v1','mineagent.rdagent-provider-chat.v1','mineagent.rdagent-provider-embedding.v1'));
ALTER TABLE @SCHEMA@.factor_source_read_witnesses DROP CONSTRAINT ec_442864cb809d7e164ca3511a,
 ADD CONSTRAINT ec_442864cb809d7e164ca3511a CHECK (source_object_kind='code' AND parser_profile IN ('mineagent.rdagent-source-parser.v1','mineagent.rdagent-provider-source-parser.v1'));
ALTER TABLE @SCHEMA@.factor_research_submissions DROP CONSTRAINT factor_research_submissions_catalog_revision_check,
 ADD CONSTRAINT factor_research_submissions_catalog_revision_check CHECK (catalog_revision IN ('configured_rd_single_output_session_v1','configured_rd_model_single_output_session_v1'));
""")
    return "\n".join(result)


def render_upgrade_sql():
    return f"{schema_boolean_sql()}\n{request_config_sql()}\n{neutral_model_sql()}\n{source_sql()}\n{boundary_sql()}\n{submission_sql()}\n{kind_request_checks()}"


def render_downgrade_sql():
    old = literal("024_openevolve_initial.py")
    kinds = [line for line in old.splitlines() if "versioned_kind CHECK" in line]
    start = old.index(
        "ALTER TABLE @SCHEMA@.research_model_operations DROP CONSTRAINT ck_model_versioned_request"
    )
    check = old[start : old.index(";", start) + 1]
    return "\n".join(
        (
            r"""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM @SCHEMA@.research_runs WHERE config->>'schema_version'='mineagent.factor-research.v2')
 OR EXISTS(SELECT 1 FROM @SCHEMA@.rd_steers)
 OR EXISTS(SELECT 1 FROM @SCHEMA@.rd_provider_transport_receipts)
 OR EXISTS(SELECT 1 FROM @SCHEMA@.research_objects WHERE object_kind='rd_provider_process_receipt')
 OR EXISTS(SELECT 1 FROM @SCHEMA@.research_object_publications WHERE object_kind='rd_provider_process_receipt')
 THEN RAISE EXCEPTION 'Configured provider or steer declarations prevent downgrade'; END IF;
END $$;
""",
            function(
                literal("025_structured_factor_strategy.py"), "mineagent_rd_schema"
            ),
            function(
                literal("018_rd_source_persistence.py"), "mineagent_rd_source_config"
            ),
            function(
                literal("018_rd_source_persistence.py"), "mineagent_rd_source_request"
            ),
            function(
                literal("015_neutral_primitives.py", "BUDGET_SQL"),
                "mineagent_neutral_model_consistent",
            ),
            function(literal("020_rd_controller.py"), "mineagent_rd_source_violation"),
            function(
                literal("020_rd_controller.py"),
                "mineagent_rd_operational_boundary_consistent",
            ),
            function(literal("023_rd_submissions.py"), "mineagent_rd_submission_check"),
            *kinds,
            check,
            r"""
ALTER TABLE @SCHEMA@.factor_research_specs DROP CONSTRAINT ec_f57efc3d280cd55d4424ea18,
 ADD CONSTRAINT ec_f57efc3d280cd55d4424ea18 CHECK ((jsonb_typeof(config)='object' AND config->>'schema_version' ='mineagent.factor-research.v1') IS TRUE);
ALTER TABLE @SCHEMA@.factor_generation_model_operations DROP CONSTRAINT ec_db3ca75700a1080c80e65a84,
 ADD CONSTRAINT ec_db3ca75700a1080c80e65a84 CHECK (protocol IN ('mineagent.rdagent-chat.v1','mineagent.rdagent-embedding.v1'));
ALTER TABLE @SCHEMA@.factor_source_read_witnesses DROP CONSTRAINT ec_442864cb809d7e164ca3511a,
 ADD CONSTRAINT ec_442864cb809d7e164ca3511a CHECK (source_object_kind='code' AND parser_profile='mineagent.rdagent-source-parser.v1');
ALTER TABLE @SCHEMA@.factor_research_submissions DROP CONSTRAINT factor_research_submissions_catalog_revision_check,
 ADD CONSTRAINT factor_research_submissions_catalog_revision_check CHECK (catalog_revision='configured_rd_single_output_session_v1');
DROP FUNCTION @SCHEMA@.mineagent_rd_fixture_config_026(jsonb);
DROP FUNCTION @SCHEMA@.mineagent_rd_fixture_request_026(jsonb,text);
DROP FUNCTION @SCHEMA@.mineagent_rd_fixture_source_violation_026(uuid);
DROP FUNCTION @SCHEMA@.mineagent_rd_provider_source_violation(uuid);
DROP FUNCTION @SCHEMA@.mineagent_rd_fixture_submission_check_026(uuid);
""",
        )
    )

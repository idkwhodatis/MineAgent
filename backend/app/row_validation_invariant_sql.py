"""Isolated candidate: freshly proved row-local invariants, never graph caching.

NOT a migration and NOT wired into the installed-schema gate. Only the complete
render_candidate_sql() batch is suitable for the opt-in PostgreSQL probe. It
requires a caller-owned transaction, the exact032f predecessor, and quiescent
maintenance. There is deliberately no commit, CLI installation, or JSON-taking
shortcut API. Existing validators and CHECKs are never replaced.

The six new CHECKs scan historical tuples before any shortcut is installed.
IS TRUE rejects SQL NULL. RD CASE guards leave other engines/versions/protocols
to their existing constraints. Every shortcut uses the very row just fetched;
all relational reads and every other predicate are byte-for-byte unchanged.
Future helper changes must re-prove/revalidate these obligations, not trust an
old convalidated flag. The predecessor SQL and entire pure closure are pinned.
"""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

VERSIONS = Path(__file__).parents[1] / "alembic/versions"
PREDECESSOR = "032f_shared_validation_cost"
PINS = {
    (
        "018_rd_source_persistence.py",
        "UPGRADE_SQL",
    ): "7aab728d7e5300e8d8718663276b8cba8c7262f38af56230a0d463297f596bc3",
    (
        "021_execution_snapshots.py",
        "NORMALIZER_RESULT_SQL",
    ): "9c0665b463bbc493e22c57659b52b99a62e00738680feac8ef8a648c8cd99de0",
    (
        "025_structured_factor_strategy.py",
        "UPGRADE_SQL",
    ): "45fa55c439f829f3d7fadbbd7b4920148b3e5b647a4964014cd92150b71115d3",
    (
        "026_rd_seeded_submissions.py",
        "UPGRADE_SQL",
    ): "629d918feb22c39f943bc2a7022f77f605adca28fbcce0ce5868c250815f2e2c",
    (
        "027_rd_controller_v3.py",
        "UPGRADE_SQL",
    ): "01fc04b5bcb0dcb3f691f5a6c5f42a07c00ba7f363ee7ff77c8d92880ef5c971",
    (
        "027c_seeded_controller_v4.py",
        "UPGRADE_SQL",
    ): "084ab22623c5c0ff3f9a61e94ea0db19be70292e2fd5deb6d30946396b388b2b",
    (
        "028_oe_structured_search.py",
        "UPGRADE_SQL",
    ): "dbb61d771730685de3cf8cb2fd7eeafe48e2c7c77fa54d57c19d70df2e68f7c9",
    (
        "029_research_workflow.py",
        "UPGRADE_SQL",
    ): "2848ba2747b703965ad8de8048591ac1931854666cda2f1f6dd512976aaf9ed3",
    (
        "030_declared_upload_basket.py",
        "UPGRADE_SQL",
    ): "1f587094ed8d30048865a13f77effdf162ec77b8c5488d70a64c7b1e94c38a7e",
    (
        "031_workflow_structured_stage.py",
        "UPGRADE_SQL",
    ): "67c916d766057c38db8f9e275de1936dcc3edf7a9803931dff597ed6b912ec84",
    (
        "032b_oe_append_run_state.py",
        "UPGRADE_SQL",
    ): "3b69896e96b2c76c31ce1a883b407e2cb1b0e6e7f4796c94c9574e42169d12b3",
    (
        "032d_rd_validation_cost.py",
        "UPGRADE_SQL",
    ): "3b511de1b313f8f043ad2b1d7b651486e78224e6cccca17f2d160766681e1a63",
    (
        "032e_rd_workflow_cost.py",
        "UPGRADE_SQL",
    ): "166126de92db41e911154a9a108b4f4dcea32f5a7928249d16035a7ac73ef9ee",
    (
        "032f_shared_validation_cost.py",
        "UPGRADE_SQL",
    ): "d43aa6e77092da70b15a670f13ebbffcd46baf2b82e6c37bb9b0c0b14e5e13de",
    (
        "028_oe_structured_search.py",
        "TABLE_SQL",
    ): "fdcc04a45c7dd800b6a1e575b114f115a10a406f69ceb7f72e5b07cbf6b245a4",
}

# Current definitions, not whichever renderer happens to be imported today.
SOURCES = {
    "mineagent_rd_provider_graph": "029_research_workflow.py",
    "mineagent_rd_provider_queued_config": "029_research_workflow.py",
    "mineagent_rd_workflow_submission_check": "032e_rd_workflow_cost.py",
    "mineagent_oes_spec_check": "031_workflow_structured_stage.py",
    "mineagent_oes_candidate_check": "031_workflow_structured_stage.py",
    "mineagent_oe_workflow_attachment": "031_workflow_structured_stage.py",
    "mineagent_rd_json_bytes": "018_rd_source_persistence.py",
    "mineagent_execution_metadata_sha": "021_execution_snapshots.py",
    "mineagent_structured_json_value": "025_structured_factor_strategy.py",
    "mineagent_structured_raw_commitment": "025_structured_factor_strategy.py",
    "mineagent_rd_provider_payload": "026_rd_seeded_submissions.py",
    "mineagent_rd_provider_config_026_frozen": "027_rd_controller_v3.py",
    "mineagent_rd_provider_config": "027c_seeded_controller_v4.py",
    "mineagent_rd_controller_selection": "027c_seeded_controller_v4.py",
    "mineagent_rd_workflow_config": "029_research_workflow.py",
    "mineagent_oes_contract_v030_shape": "030_declared_upload_basket.py",
    "mineagent_oes_contract_before_workflow": "031_workflow_structured_stage.py",
    "mineagent_oes_contract": "031_workflow_structured_stage.py",
    "mineagent_oe_workflow_shape": "031_workflow_structured_stage.py",
    "mineagent_rd_canonical_json": "032d_rd_validation_cost.py",
    "mineagent_rd_schema": "032f_shared_validation_cost.py",
    "mineagent_rd_schema_recursive_032e": "032f_shared_validation_cost.py",
    "mineagent_structured_raw_json": "032f_shared_validation_cost.py",
    "mineagent_oes_append": "032b_oe_append_run_state.py",
}
CHANGED = tuple(SOURCES)[:6]
PURE = tuple(SOURCES)[6:-1]
ROOTS = (
    "mineagent_rd_provider_config",
    "mineagent_rd_workflow_config",
    "mineagent_rd_provider_payload",
    "mineagent_oes_contract",
    "mineagent_structured_raw_commitment",
)


def literal(filename: str, key: str = "UPGRADE_SQL") -> str:
    tree = ast.parse((VERSIONS / filename).read_text())
    values = [
        ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == key for t in n.targets)
    ]
    if (
        len(values) != 1
        or hashlib.sha256(values[0].encode()).hexdigest() != PINS[filename, key]
    ):
        raise ValueError(
            "Exact reviewed frozen literal required: " + filename + ":" + key
        )
    return values[0]


def original(name: str) -> str:
    key = (
        "NORMALIZER_RESULT_SQL"
        if name == "mineagent_execution_metadata_sha"
        else "UPGRADE_SQL"
    )
    sql = literal(SOURCES[name], key)
    matches = list(
        re.finditer(
            r"CREATE(?: OR REPLACE)? FUNCTION (?:@SCHEMA@\.)?"
            + re.escape(name)
            + r"\(",
            sql,
        )
    )
    if len(matches) != 1:
        raise ValueError("Exact frozen function inventory required: " + name)
    start = matches[0].start()
    return sql[start : sql.index("$$;", start) + 3].replace(
        "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
    )


def replace_once(sql: str, old: str, new: str) -> str:
    if sql.count(old) != 1:
        raise ValueError("Exact reviewed clause required: " + old)
    return sql.replace(old, new, 1)


def run_guard(row: str, config: str, *, workflow_only: bool = False) -> str:
    version = (
        f"{config}->>'schema_version'='mineagent.factor-research.v3'"
        if workflow_only
        else f"{config}->>'schema_version' IN ('mineagent.factor-research.v2','mineagent.factor-research.v3')"
    )
    return f"{row}.purpose='factor_research' AND {row}.engine='rdagent' AND {version}"


# No table reads, subqueries, or deferred evidence in these expressions. Every
# branch/predicate returns non-null boolean; the outer IS TRUE is intentional.
CONSTRAINTS = (
    (
        "research_runs",
        "ck_rvi_rd_run_config",
        """(CASE
 WHEN purpose='factor_research' AND engine='rdagent' AND config->>'schema_version'='mineagent.factor-research.v2'
 THEN @SCHEMA@.mineagent_rd_provider_config(config) IS TRUE
 WHEN purpose='factor_research' AND engine='rdagent' AND config->>'schema_version'='mineagent.factor-research.v3'
 THEN @SCHEMA@.mineagent_rd_workflow_config(config) IS TRUE
 ELSE true END) IS TRUE""",
    ),
    (
        "research_model_operations",
        "ck_rvi_rd_provider_payload",
        """(CASE WHEN protocol IN ('mineagent.rdagent-provider-chat.v1','mineagent.rdagent-provider-embedding.v1')
 THEN @SCHEMA@.mineagent_rd_provider_payload(request->>'kind',request) IS TRUE
 ELSE true END) IS TRUE""",
    ),
    (
        "oe_structured_specs",
        "ck_rvi_oe_spec_config",
        "@SCHEMA@.mineagent_oes_contract('config',config) IS TRUE",
    ),
    (
        "oe_structured_specs",
        "ck_rvi_oe_spec_request",
        "@SCHEMA@.mineagent_oes_contract('request',request) IS TRUE",
    ),
    (
        "oe_structured_specs",
        "ck_rvi_oe_spec_config_raw",
        "@SCHEMA@.mineagent_structured_raw_commitment(config_canonical,config,config_sha256,1048576) IS TRUE",
    ),
    (
        "oe_structured_specs",
        "ck_rvi_oe_spec_request_raw",
        "@SCHEMA@.mineagent_structured_raw_commitment(request_canonical,request,request_sha256,65536) IS TRUE",
    ),
)
REQUIRED_COLUMNS = {
    "research_runs": ("purpose", "engine", "config"),
    "research_model_operations": ("protocol", "request"),
    "oe_structured_specs": (
        "owner_id",
        "config",
        "request",
        "config_canonical",
        "request_canonical",
        "config_sha256",
        "request_sha256",
    ),
    "oe_structured_candidates": ("owner_id",),
}


def edits() -> dict[str, tuple[tuple[str, str], ...]]:
    """The complete reviewed edit surface, usable by source-equivalence tests."""
    dispatch = "(CASE WHEN run.config->>'schema_version'='mineagent.factor-research.v3' THEN mineagent_rd_workflow_config(run.config) ELSE mineagent_rd_provider_config(run.config) END)"
    queued = "(CASE WHEN v->>'schema_version'='mineagent.factor-research.v3' THEN mineagent_rd_workflow_config(v) ELSE mineagent_rd_provider_config(v) END)"
    return {
        CHANGED[0]: (
            (
                "config_valid:=" + dispatch + ";",
                "config_valid:=(CASE WHEN "
                + run_guard("run", "run.config")
                + " THEN true ELSE "
                + dispatch
                + " END);",
            ),
            (
                "   IF binding_valid IS TRUE\n   AND mineagent_rd_provider_payload(op.request->>'kind',op.request) IS TRUE THEN",
                "   IF binding_valid IS TRUE THEN",
            ),
        ),
        CHANGED[1]: (
            (
                queued,
                "(CASE WHEN "
                + run_guard("r", "v")
                + " THEN true ELSE "
                + queued
                + " END)",
            ),
        ),
        CHANGED[2]: (
            (
                "IF mineagent_rd_workflow_config(cfg) IS NOT TRUE THEN",
                "IF (CASE WHEN "
                + run_guard("r", "cfg", workflow_only=True)
                + " THEN true ELSE mineagent_rd_workflow_config(cfg) END) IS NOT TRUE THEN",
            ),
        ),
        CHANGED[3]: tuple(
            (line, "")
            for line in (
                " OR @SCHEMA@.mineagent_oes_contract('config',c) IS NOT TRUE\n",
                " OR @SCHEMA@.mineagent_oes_contract('request',s.request) IS NOT TRUE\n",
                " OR @SCHEMA@.mineagent_structured_raw_commitment(s.request_canonical,s.request,s.request_sha256,65536) IS NOT TRUE\n",
                " OR @SCHEMA@.mineagent_structured_raw_commitment(s.config_canonical,c,s.config_sha256,1048576) IS NOT TRUE\n",
            )
        ),
        # Missing spec retains spec_check's error and s.owner_id IS DISTINCT
        # FROM non-null c.owner_id. No fetch or predicate is reordered.
        CHANGED[4]: tuple(
            (line, "")
            for line in (
                " OR @SCHEMA@.mineagent_oes_contract('config',s.config) IS NOT TRUE\n",
                " OR @SCHEMA@.mineagent_structured_raw_commitment(s.config_canonical,s.config,s.config_sha256,1048576) IS NOT TRUE\n",
            )
        ),
        CHANGED[5]: (
            (
                " OR mineagent_oes_contract('config',cfg) IS NOT TRUE OR cfg->>'schema_version'",
                " OR cfg->>'schema_version'",
            ),
            (
                " OR mineagent_oes_contract('request',sp.request) IS NOT TRUE OR sp.request->'workflow_context'",
                " OR sp.request->'workflow_context'",
            ),
        ),
    }


def candidate(name: str) -> str:
    sql = original(name)
    for before, after in edits()[name]:
        sql = replace_once(sql, before, after)
    return sql


def render_upgrade_sql() -> str:
    """Change payload only; callers must use the guarded batch below to install."""
    checks = "\n".join(
        f"ALTER TABLE @SCHEMA@.{table} ADD CONSTRAINT {name} CHECK ({expr});"
        for table, name, expr in CONSTRAINTS
    )
    return checks + "\n" + "\n".join(candidate(name) for name in CHANGED)


def render_downgrade_sql() -> str:
    # Restore every exact obligation BEFORE dropping any new invariant.
    return (
        "\n".join(original(name) for name in CHANGED)
        + "\n"
        + "\n".join(
            f"ALTER TABLE @SCHEMA@.{table} DROP CONSTRAINT {name};"
            for table, name, _ in reversed(CONSTRAINTS)
        )
    )


def maintenance_tables() -> tuple[str, ...]:
    tree = ast.parse((VERSIONS / "032f_shared_validation_cost.py").read_text())
    method = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_maintenance"
    )
    if (
        hashlib.sha256(ast.dump(method).encode()).hexdigest()
        != "d32b3aa8cc95acf20459beffdc24cb5d5cacc849ab419dd1b976ae69d5531e16"
    ):
        raise ValueError("Exact predecessor maintenance required")
    return next(
        ast.literal_eval(n.value) for n in method.body if isinstance(n, ast.Assign)
    )


def render_maintenance_sql() -> str:
    return (
        """DO $rvi$ BEGIN
 IF current_setting('transaction_isolation') <> 'read committed'
 OR NOT pg_try_advisory_xact_lock(1413960260,1129270340)
 THEN RAISE EXCEPTION 'Row invariant candidate requires quiescent maintenance'; END IF;
END $rvi$;
LOCK TABLE """
        + ",".join("@SCHEMA@." + table for table in maintenance_tables())
        + " IN ACCESS EXCLUSIVE MODE NOWAIT;\nSET LOCAL search_path=@SCHEMA@,pg_catalog;"
    )


def function_assertion(sql: str) -> str:
    """Pin body, signature, result, language, volatility, strictness and path.

    Compare pg_proc source, not the server's cosmetic pretty-printed header.
    Lowercase exact schema names let the path comparison ignore quotes/spaces.
    """
    header, body = sql.split("AS $$", 1)
    body = body.removesuffix("$$;")
    match = re.search(
        r"FUNCTION (?:@SCHEMA@\.)?(\w+)\((.*?)\)\s+RETURNS (\w+)", header, re.DOTALL
    )
    if match is None:
        raise ValueError("Unreviewed function signature")
    name, args, result = match.groups()
    types = ",".join(arg.split()[1] for arg in args.split(",")) if args else ""
    path = re.search(r"SET search_path=(\S+) AS", header + "AS").group(1)
    defaults = re.findall(r"\bDEFAULT\s+([^,]+)", args)
    if any(value.strip() != "0" for value in defaults):
        raise ValueError("Unreviewed function argument default")
    default_sql = (
        "'" + ", ".join(value.strip() for value in defaults) + "'"
        if defaults
        else "NULL"
    )
    arguments = [arg.strip().split()[0] for arg in args.split(",")] if args else []
    names_sql = (
        "ARRAY[" + ",".join("'" + name + "'" for name in arguments) + "]::text[]"
        if arguments
        else "NULL::text[]"
    )
    volatility = "i" if "IMMUTABLE" in header else "s" if "STABLE" in header else "v"
    strict = "true" if "STRICT" in header else "false"
    language = re.search(r"LANGUAGE (\w+)", header).group(1)
    # All source is pinned; the tag cannot occur in the embedded source.
    assert "$rvi_body$" not in body
    return f"""IF NOT EXISTS (
 SELECT 1 FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
 WHERE p.oid=to_regprocedure('@SCHEMA@.{name}({types})')
 AND p.prosrc=$rvi_body${body}$rvi_body$
 AND p.prorettype='{result}'::regtype AND p.prokind='f'
 AND p.pronargs={len(arguments)} AND p.pronargdefaults={len(defaults)}
 AND pg_get_expr(p.proargdefaults,0) IS NOT DISTINCT FROM {default_sql}
 AND p.proargnames IS NOT DISTINCT FROM {names_sql}
 AND p.proargmodes IS NULL AND p.provariadic=0 AND NOT p.proretset
 AND p.proparallel='u' AND NOT p.proleakproof AND p.prosupport=0
 AND l.lanname='{language}' AND p.provolatile='{volatility}'
 AND p.proisstrict={strict} AND NOT p.prosecdef
 AND cardinality(p.proconfig)=1
 AND replace(regexp_replace(p.proconfig[1],'[[:space:]]','','g'),'"','')=
     replace('search_path={path}','"','')
) THEN RAISE EXCEPTION 'Row invariant pinned function differs: {name}'; END IF;"""


def render_preflight_sql(direction: str = "upgrade") -> str:
    if direction not in ("upgrade", "downgrade"):
        raise ValueError("Expected upgrade or downgrade")
    checks = [
        f"""IF (SELECT array_agg(version_num ORDER BY version_num) FROM @SCHEMA@.alembic_version)
 IS DISTINCT FROM ARRAY['{PREDECESSOR}']::varchar[]
 THEN RAISE EXCEPTION 'Exact032f candidate schema required'; END IF;
 IF current_setting('server_encoding')<>'UTF8' THEN RAISE EXCEPTION 'UTF8 required'; END IF;"""
    ]
    for name in SOURCES:
        sql = (
            candidate(name)
            if direction == "downgrade" and name in CHANGED
            else original(name)
        )
        checks.append(function_assertion(sql))
    for table, columns in REQUIRED_COLUMNS.items():
        for column in columns:
            checks.append(f"""IF NOT EXISTS(SELECT 1 FROM pg_attribute
 WHERE attrelid='@SCHEMA@.{table}'::regclass AND attname='{column}'
 AND attnotnull AND NOT attisdropped)
 THEN RAISE EXCEPTION 'Row invariant NOT NULL column differs: {table}.{column}'; END IF;""")
    for table, name, _ in CONSTRAINTS:
        if direction == "upgrade":
            checks.append(f"""IF EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='@SCHEMA@.{table}'::regclass AND conname='{name}')
 THEN RAISE EXCEPTION 'Fresh row invariant already exists: {name}'; END IF;""")
        else:
            checks.append(f"""IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='@SCHEMA@.{table}'::regclass AND conname='{name}' AND contype='c' AND convalidated AND NOT condeferrable)
 THEN RAISE EXCEPTION 'Validated row invariant missing: {name}'; END IF;""")
    # OE's earlier rejection is committed-acceptance-equivalent only while its
    # production append-only trigger is enabled. Pin both its body and binding.
    checks.append("""IF NOT EXISTS(SELECT 1 FROM pg_trigger
 WHERE tgrelid='@SCHEMA@.oe_structured_specs'::regclass
 AND tgfoid='@SCHEMA@.mineagent_oes_append()'::regprocedure
 AND tgenabled IN ('O','A') AND tgtype=31 AND NOT tgisinternal
 AND tgnargs=0 AND tgqual IS NULL AND tgattr::text='')
 THEN RAISE EXCEPTION 'OE spec append-only trigger differs'; END IF;""")
    return "DO $rvi$ BEGIN\n" + "\n".join(checks) + "\nEND $rvi$;"


def render_candidate_sql(direction: str = "upgrade") -> str:
    """Run only in an explicit rollback-capable transaction; never autocommit.

    This candidate does not advance alembic_version in either direction. Full
    catalog roundtrip tests verify unchanged legacy constraints and triggers.
    A future migration must additionally pin their reviewed server catalog
    definitions before integration; no production readiness is claimed here.
    """
    payload = (
        render_upgrade_sql()
        if direction == "upgrade"
        else render_downgrade_sql()
        if direction == "downgrade"
        else None
    )
    if payload is None:
        raise ValueError("Expected upgrade or downgrade")
    return "\n".join(
        (render_maintenance_sql(), render_preflight_sql(direction), payload)
    )

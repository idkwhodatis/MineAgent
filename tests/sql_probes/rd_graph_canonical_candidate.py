"""One function-local ASCII reuse experiment; never imported by runtime/migrations."""

import hashlib
import re

from sqlalchemy import text

from backend.app.row_validation_oe_032g_base_sql import original, replace_once

GRAPH = "mineagent_rd_provider_graph"
ENCODER = "mineagent_rd_canonical_json"
GRAPH_SHA = "4603958bd678d1add47a006519e85c1f28f6c4037c132b36d75a046f4fabd8b3"
ENCODER_SHA = "3b511de1b313f8f043ad2b1d7b651486e78224e6cccca17f2d160766681e1a63"
# The pinned 032d encoder rejects non-UTF8 before any non-NULL result. The
# installed request payload validation also precedes this exact edit site.
BASELINE = """    request_utf8:=mineagent_rd_canonical_json(op.request,false);
    request_ascii:=mineagent_rd_canonical_json(op.request,true);
"""
CANDIDATE = """    request_utf8:=mineagent_rd_canonical_json(op.request,false);
    IF octet_length(request_utf8)=char_length(request_utf8)
    AND position(chr(127) in request_utf8 COLLATE "C")=0 THEN
     request_ascii:=request_utf8;
    ELSE
     request_ascii:=mineagent_rd_canonical_json(op.request,true);
    END IF;
"""

_SCHEMA = re.compile(r"tradegrad_test_[0-9a-f]{32}")
_DATABASE = re.compile(r"tradegrad_backup_test_[0-9a-f]{32}")


def pinned_sources():
    sources = {name: original(name) for name in (GRAPH, ENCODER)}
    for name, digest in ((GRAPH, GRAPH_SHA), (ENCODER, ENCODER_SHA)):
        if hashlib.sha256(sources[name].encode()).hexdigest() != digest:
            raise ValueError("Exact reviewed graph and UTF8 encoder required")
    return sources


def candidate_source():
    return replace_once(pinned_sources()[GRAPH], BASELINE, CANDIDATE)


def checked_schema(connection):
    """Require exact current chain, held inside an owned test namespace only."""
    database, schema, version = connection.exec_driver_sql(
        "SELECT current_database(), current_schema(), current_setting('server_version_num')"
    ).one()
    if not (
        _SCHEMA.fullmatch(schema or "")
        or (schema == "public" and _DATABASE.fullmatch(database or ""))
    ):
        raise ValueError("Disposable test database or schema required")
    if not 180000 <= int(version) < 190000:
        raise ValueError("Exact PostgreSQL18 major required")
    if connection.exec_driver_sql(
        "SELECT version_num FROM alembic_version"
    ).scalars().all() != ["032g_schema_keyset_cost"]:
        raise ValueError("Exact installed032g migration chain required")
    for name, source in pinned_sources().items():
        rows = connection.execute(
            text(
                """SELECT p.prosrc, p.proconfig, p.prosecdef,
                          p.provolatile, p.proisstrict, p.pronargdefaults,
                          p.proargtypes::text AS argument_types,
                          pg_get_function_result(p.oid) AS result_type, l.lanname
                FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid=p.pronamespace
                JOIN pg_language AS l ON l.oid=p.prolang
                WHERE n.nspname=:schema AND p.proname=:name"""
            ),
            {"schema": schema, "name": name},
        ).all()
        expected = source.split("$$")[1].replace("@SCHEMA@", f'"{schema}"')
        if len(rows) != 1:
            raise ValueError("Exact installed function identity required")
        row = rows[0]
        # Built-in UUID (2950), JSONB (3802), bool (16), int4 (23).
        expected_signature = (
            ("v", False, 0, "2950", "void")
            if name == GRAPH
            else ("i", True, 1, "3802 16 23", "text")
        )
        actual_signature = (
            row.provolatile,
            row.proisstrict,
            row.pronargdefaults,
            row.argument_types,
            row.result_type,
        )
        if (
            row.prosrc != expected
            or row.prosecdef
            or row.lanname != "plpgsql"
            or actual_signature != expected_signature
        ):
            raise ValueError("Installed provider graph or canonical encoder drift")
        # PostgreSQL normalizes SET search_path to catalog spelling.
        path = row.proconfig
        if not path or len(path) != 1 or path[0].split("=", 1)[0] != "search_path":
            raise ValueError("Exact installed search_path required")
        actual = path[0].split("=", 1)[1].replace('"', "").replace(" ", "")
        if actual != (f"{schema},pg_catalog" if name == GRAPH else "pg_catalog"):
            raise ValueError("Installed provider graph or encoder search_path drift")
    return schema


def temp_graph_source(schema):
    if not (_SCHEMA.fullmatch(schema) or schema == "public"):
        raise ValueError("Exact disposable schema argument required")
    return replace_once(
        candidate_source(),
        f"CREATE OR REPLACE FUNCTION @SCHEMA@.{GRAPH}(",
        "CREATE FUNCTION pg_temp.rd_graph_ascii_reuse(",
    ).replace("@SCHEMA@", f'"{schema}"')


def temp_bytes_source(schema, *, candidate):
    if not (_SCHEMA.fullmatch(schema) or schema == "public"):
        raise ValueError("Exact disposable schema argument required")
    name = "rd_bytes_reuse" if candidate else "rd_bytes_original"
    fragment = CANDIDATE if candidate else BASELINE
    if fragment not in (candidate_source() if candidate else pinned_sources()[GRAPH]):
        raise ValueError("Fingerprint fragment drift")
    return f"""CREATE FUNCTION pg_temp.{name}(v jsonb) RETURNS text[]
LANGUAGE plpgsql SET search_path="{schema}",pg_catalog AS $$
DECLARE op record; request_utf8 text; request_ascii text;
BEGIN
    SELECT v AS request INTO op;
{fragment}    RETURN ARRAY[request_utf8,request_ascii];
END $$;"""


def project_diagnostic_cases(root):
    """Return only closed outcomes, phase names and bounded case ordinals."""
    phases = {
        "test_installed032g_request_encoding_bytes_and_errors": {
            "startup",
            "connect",
            "installed_preflight",
            "temp_scope",
            "temp_baseline",
            "temp_candidate",
            "corpus",
            "complete",
        },
        "test_default_off_populated_rd_proposal_adoption_graph_matches_candidate": {
            "startup",
            "original_workflow",
            "connect",
            "installed_preflight",
            "temp_scope",
            "temp_candidate",
            "persisted_evidence",
            "installed_graph",
            "candidate_graph",
            "graph_parity",
            "complete",
        },
    }
    results = {}
    for case in root.iter("testcase"):
        name = case.get("name")
        if name not in phases or name in results:
            raise ValueError("Unexpected diagnostic testcase inventory")
        status = next(
            (
                tag
                for tag in ("failure", "error", "skipped")
                if case.find(tag) is not None
            ),
            "passed",
        )
        properties = {}
        for prop in case.findall("./properties/property"):
            key = prop.get("name")
            if key in ("canonical_phase", "canonical_ordinal"):
                if key in properties:
                    raise ValueError("Duplicate canonical diagnostic property")
                properties[key] = prop.get("value")
        phase = properties.get("canonical_phase", "fixture")
        ordinal_string = properties.get("canonical_ordinal")
        if not (
            phase in phases[name]
            or (phase == "fixture" and "canonical_phase" not in properties)
        ):
            raise ValueError("Unexpected canonical diagnostic phase")
        if phase == "corpus":
            # The exact finite corpus contains 25 cases enumerated from zero.
            if ordinal_string not in {str(i) for i in range(25)}:
                raise ValueError("Unexpected canonical diagnostic case ordinal")
            ordinal = int(ordinal_string)
        else:
            if "canonical_ordinal" in properties:
                raise ValueError("Ordinal is only allowed for the canonical corpus")
            ordinal = None
        if status == "passed" and phase != "complete":
            raise ValueError("Missing canonical diagnostic completion")
        results[name] = (status, phase, ordinal)
    if results.keys() != phases.keys():
        raise ValueError("Missing exact canonical diagnostic testcase inventory")
    return dict(sorted(results.items()))

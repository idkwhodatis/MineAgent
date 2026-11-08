"""Read-only pristine 032f metadata export for the isolated candidate CI proof.

Never used by a migration or production preflight. No application table data,
function bodies, credentials, or arbitrary server settings are exported.
"""

import hashlib
import json
import re
from pathlib import Path

TABLES = (
    "research_runs",
    "research_model_operations",
    "oe_structured_specs",
    "oe_structured_control_states",
    "oe_structured_candidates",
    "oe_structured_executions",
    "oe_structured_engine_outcomes",
    "oe_structured_epochs",
)
# Every removed numeric OID has a named/signature/definition counterpart in the
# same raw record. All non-OID metadata stays, including ordering and deferral.
OID_FIELDS = {
    "relation_oid",
    "constraint_oid",
    "referenced_relation_oid",
    "column_type_oid",
    "collation_oid",
    "trigger_oid",
    "function_oid",
    "support_oid",
    "namespace_oid",
    "operator_oid",
    "left_type_oid",
    "right_type_oid",
    "result_type_oid",
    "implementation_oid",
    "type_oid",
    "base_type_oid",
    "element_type_oid",
    "array_type_oid",
    "input_function_oid",
    "output_function_oid",
}
NORMALIZATION = (
    "Replace only the exact generated schema identifier with @SCHEMA@ in text; "
    "remove the documented numeric OID fields, whose named identities remain. "
    "Preserve names, signatures, defaults, support functions, predicates, "
    "collations, validation/enforcement, deferral, order and all other metadata. "
    "Function body SHA-256s use that same schema substitution before hashing. "
    "This observed snapshot is review evidence, not a production allowlist."
)


def normalized(value, schema):
    assert re.fullmatch(r"tradegrad_test_[a-f0-9]{32}", schema)
    if isinstance(value, dict):
        return {
            key: normalized(item, schema)
            for key, item in value.items()
            if key not in OID_FIELDS
        }
    if isinstance(value, list):
        return [normalized(item, schema) for item in value]
    if isinstance(value, str):
        return value.replace(schema, "@SCHEMA@")
    return value


def capture(connection):
    schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
    assert re.fullmatch(r"tradegrad_test_[a-f0-9]{32}", schema)
    assert (
        connection.exec_driver_sql("SHOW server_version_num").scalar_one() == "180006"
    )
    revision = (
        connection.exec_driver_sql("SELECT version_num FROM alembic_version")
        .scalars()
        .all()
    )
    assert revision == ["032f_shared_validation_cost"]
    assert not connection.exec_driver_sql(
        "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint c JOIN pg_catalog.pg_namespace n ON n.oid=c.connamespace WHERE n.nspname=current_schema() AND c.conname LIKE 'ck_rvi_%%')"
    ).scalar_one()
    table_list = ",".join("'" + table + "'" for table in TABLES)
    scope = f"n.nspname=current_schema() AND r.relname IN ({table_list})"
    queries = {
        "relations": f"""SELECT r.oid AS relation_oid,r.relname,r.relkind,r.relpersistence,
          r.relispartition,r.relrowsecurity,r.relforcerowsecurity,
          (SELECT count(*) FROM pg_catalog.pg_inherits i WHERE i.inhrelid=r.oid OR i.inhparent=r.oid) AS inheritance_links
          FROM pg_catalog.pg_class r JOIN pg_catalog.pg_namespace n ON n.oid=r.relnamespace
          WHERE {scope} ORDER BY r.relname""",
        "columns": f"""SELECT r.relname,a.attnum,a.attname,a.atttypid AS column_type_oid,
          pg_catalog.format_type(a.atttypid,a.atttypmod) AS type_identity,
          a.attnotnull,a.attidentity,a.attgenerated,a.attisdropped,
          a.attcollation AS collation_oid,cn.nspname AS collation_schema,co.collname,
          co.collprovider,co.collisdeterministic,
          pg_catalog.pg_get_expr(d.adbin,d.adrelid) AS default_expression
          FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class r ON r.oid=a.attrelid
          JOIN pg_catalog.pg_namespace n ON n.oid=r.relnamespace
          LEFT JOIN pg_catalog.pg_collation co ON co.oid=a.attcollation
          LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid=co.collnamespace
          LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=r.oid AND d.adnum=a.attnum
          WHERE {scope} AND a.attnum>0 ORDER BY r.relname,a.attnum""",
        "constraints": f"""SELECT r.relname,c.oid AS constraint_oid,c.conname,c.contype,
          pg_catalog.pg_get_constraintdef(c.oid) AS definition,
          c.convalidated,c.conenforced,c.condeferrable,c.condeferred,c.connoinherit,
          c.conislocal,c.coninhcount,c.conkey,c.confkey,c.confmatchtype,c.confupdtype,c.confdeltype,
          c.confrelid AS referenced_relation_oid,c.confrelid::pg_catalog.regclass::text AS referenced_relation
          FROM pg_catalog.pg_constraint c JOIN pg_catalog.pg_class r ON r.oid=c.conrelid
          JOIN pg_catalog.pg_namespace n ON n.oid=r.relnamespace
          WHERE {scope} ORDER BY r.relname,c.conname""",
        "triggers": f"""SELECT r.relname,t.oid AS trigger_oid,t.tgname,t.tgenabled,t.tgisinternal,
          t.tgtype,t.tgattr::text AS update_columns,t.tgdeferrable,t.tginitdeferred,
          t.tgfoid AS function_oid,t.tgfoid::pg_catalog.regprocedure::text AS function_identity,
          pg_catalog.pg_get_triggerdef(t.oid) AS definition,
          pg_catalog.encode(t.tgargs,'hex') AS arguments_hex
          FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class r ON r.oid=t.tgrelid
          JOIN pg_catalog.pg_namespace n ON n.oid=r.relnamespace
          WHERE {scope} ORDER BY r.relname,t.tgname""",
        "functions": """SELECT p.oid AS function_oid,n.oid AS namespace_oid,n.nspname,p.proname,
          p.oid::pg_catalog.regprocedure::text AS identity,
          pg_catalog.pg_get_function_identity_arguments(p.oid) AS identity_arguments,
          pg_catalog.pg_get_function_arguments(p.oid) AS arguments,
          pg_catalog.pg_get_function_result(p.oid) AS result,
          l.lanname,p.prokind,p.provolatile,p.proisstrict,p.prosecdef,p.proleakproof,
          p.proparallel,p.proretset,p.pronargdefaults,p.proargnames,p.proargmodes,
          p.provariadic::pg_catalog.regtype::text AS variadic_type,
          p.prosupport::oid AS support_oid,p.prosupport::pg_catalog.regprocedure::text AS support_function,
          pg_catalog.pg_get_expr(p.proargdefaults,0) AS defaults,p.proconfig,
          p.proacl::text AS privileges,pg_catalog.pg_get_userbyid(p.proowner) AS owner,
          p.prosrc AS body_for_hash_only
          FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
          JOIN pg_catalog.pg_language l ON l.oid=p.prolang
          WHERE n.nspname=current_schema() ORDER BY p.proname,p.oid::pg_catalog.regprocedure::text""",
        "operators": """SELECT o.oid AS operator_oid,n.nspname,o.oprname,o.oprkind,
          o.oprcanmerge,o.oprcanhash,o.oprleft AS left_type_oid,
          o.oprleft::pg_catalog.regtype::text AS left_type,o.oprright AS right_type_oid,
          o.oprright::pg_catalog.regtype::text AS right_type,o.oprresult AS result_type_oid,
          o.oprresult::pg_catalog.regtype::text AS result_type,o.oprcode::oid AS implementation_oid,
          o.oprcode::pg_catalog.regprocedure::text AS implementation
          FROM pg_catalog.pg_operator o JOIN pg_catalog.pg_namespace n ON n.oid=o.oprnamespace
          WHERE n.nspname=current_schema() ORDER BY o.oprname,left_type,right_type""",
        "types": """SELECT t.oid AS type_oid,n.nspname,t.typname,t.typtype,t.typcategory,
          t.typisdefined,t.typnotnull,t.typlen,t.typbyval,t.typalign,t.typstorage,
          t.typbasetype AS base_type_oid,t.typbasetype::pg_catalog.regtype::text AS base_type,
          t.typelem AS element_type_oid,t.typelem::pg_catalog.regtype::text AS element_type,
          t.typarray AS array_type_oid,t.typarray::pg_catalog.regtype::text AS array_type,
          t.typinput::oid AS input_function_oid,t.typinput::pg_catalog.regprocedure::text AS input_function,
          t.typoutput::oid AS output_function_oid,t.typoutput::pg_catalog.regprocedure::text AS output_function,
          t.typcollation AS collation_oid,t.typcollation::pg_catalog.regcollation::text AS collation,
          t.typdefault AS default_expression
          FROM pg_catalog.pg_type t JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace
          WHERE n.nspname=current_schema() ORDER BY t.typname""",
    }
    result = {
        "schema": schema,
        "revision": revision[0],
        "server_version_num": 180006,
        "normalization": NORMALIZATION,
    }
    for name, query in queries.items():
        rows = [dict(row) for row in connection.exec_driver_sql(query).mappings()]
        assert len(rows) <= 4096, "Metadata inventory exceeded bounded scope"
        if name == "functions":
            for row in rows:
                body = row.pop("body_for_hash_only")
                row["body_sha256"] = hashlib.sha256(body.encode()).hexdigest()
                row["schema_normalized_body_sha256"] = hashlib.sha256(
                    body.replace(schema, "@SCHEMA@").encode()
                ).hexdigest()
        result[name] = rows
    assert {row["relname"] for row in result["relations"]} == set(TABLES)
    assert all(row["relkind"] == "r" for row in result["relations"])
    assert result["constraints"] and result["triggers"] and result["functions"]
    return result


def export(connection, directory):
    directory = Path(directory)
    assert directory.resolve() == (Path.cwd() / "var/sql-candidate-proof").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    assert not directory.is_symlink()
    raw = capture(connection)
    normal = normalized(raw, raw["schema"])
    # Raw body hashes contain the disposable schema. Keep them in raw evidence
    # only; the normalized body's separately labeled digest is stable by rule.
    for row in normal["functions"]:
        del row["body_sha256"]
    hashes = {}
    for label, value in (("raw", raw), ("normalized", normal)):
        data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
        assert len(data) <= 4_000_000, "Metadata artifact exceeds four MB"
        name = f"predecessor-032f-{label}.json"
        with (directory / name).open("xb") as stream:
            stream.write(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    with (directory / "predecessor-032f-sha256.json").open("x") as stream:
        json.dump(hashes, stream, sort_keys=True, indent=2)
        stream.write("\n")
    return raw

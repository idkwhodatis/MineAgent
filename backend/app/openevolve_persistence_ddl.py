"""Offline deterministic 024 domain DDL; never imported by model registration.

A successor migration embeds reviewed output together with strict shared identity,
process, object, budget and complete epoch semantics. This fragment alone admits
nothing. All FKs are deferred so closed multi-row receipts can commit atomically.
"""

import hashlib
import re

from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable

from backend.app import (
    openevolve_event_models,
    openevolve_models,
    openevolve_operational_models,
    openevolve_worker_models,
)

MODULES = (
    openevolve_models,
    openevolve_event_models,
    openevolve_worker_models,
    openevolve_operational_models,
)
EXTERNAL_UNIQUES = {
    "strategy_versions": ("owner_id", "id", "content_sha256"),
    "dataset_versions": ("owner_id", "id", "content_sha256"),
    "research_process_receipts": (
        "owner_id",
        "run_id",
        "attempt_id",
        "generation",
        "admission_id",
        "id",
        "kind",
    ),
}


def tables():
    result = {
        value.__table__.name: value.__table__
        for module in MODULES
        for value in vars(module).values()
        if isinstance(value, type)
        and value.__module__ == module.__name__
        and hasattr(value, "__table__")
    }
    if len(result) != 32:
        raise ValueError("reviewed OE domain changed")
    return result


def domain_sql(schema):
    if type(schema) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema) is None:
        raise ValueError("exact installation schema")
    out = []
    for name, columns in EXTERNAL_UNIQUES.items():
        column_list = ",".join(columns)
        # New named unique is deliberately independently reversible. Existing
        # historical equivalent keys remain untouched.
        out.append(
            f"ALTER TABLE {schema}.{name} ADD CONSTRAINT oe024_uq_{name} UNIQUE ({column_list});"
        )
    for table in tables().values():
        sql = str(
            CreateTable(table, include_foreign_key_constraints=[]).compile(
                # Frozen DDL is sent with no_parameters=True; DBAPI pyformat
                # escaping would persist %% as a PostgreSQL operator.
                dialect=dialect(paramstyle="named")
            )
        ).strip()
        sql = sql.replace(
            f"CREATE TABLE {table.name} (", f"CREATE TABLE {schema}.{table.name} (", 1
        )
        out.append(sql + ";")
    for name, table in sorted(tables().items()):
        for fk in sorted(
            table.foreign_key_constraints,
            key=lambda f: tuple((e.parent.name, e.target_fullname) for e in f.elements),
        ):
            local = ",".join(element.parent.name for element in fk.elements)
            target_table = fk.elements[0].column.table.name
            remote = ",".join(element.column.name for element in fk.elements)
            identity = f"{name}:{local}:{target_table}:{remote}"
            key = "oe024_fk_" + hashlib.sha256(identity.encode()).hexdigest()[:20]
            out.append(
                f"ALTER TABLE {schema}.{name} ADD CONSTRAINT {key} FOREIGN KEY ({local}) REFERENCES {schema}.{target_table} ({remote}) DEFERRABLE INITIALLY DEFERRED;"
            )
    out.append(f"""CREATE FUNCTION {schema}.mineagent_oe_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path={schema},pg_catalog AS $$ BEGIN
 RAISE EXCEPTION 'OE evidence is append-only'; END $$;
CREATE FUNCTION {schema}.mineagent_oe_run_lock() RETURNS trigger
LANGUAGE plpgsql SET search_path={schema},pg_catalog AS $$
DECLARE r research_runs%ROWTYPE;
BEGIN
 SELECT * INTO r FROM research_runs WHERE id=NEW.run_id FOR UPDATE NOWAIT;
 IF NOT FOUND OR r.owner_id IS DISTINCT FROM NEW.owner_id OR (r.purpose,r.engine) IS DISTINCT FROM ('strategy_search','openevolve') THEN
 RAISE EXCEPTION 'Exact serialized search run required'; END IF;
 RETURN NEW;
END $$;""")
    for name in sorted(tables()):
        out.append(
            f"CREATE TRIGGER oe024_immutable BEFORE UPDATE OR DELETE ON {schema}.{name} FOR EACH ROW EXECUTE FUNCTION {schema}.mineagent_oe_immutable();"
        )
        out.append(
            f"CREATE TRIGGER oe024_run_lock BEFORE INSERT ON {schema}.{name} FOR EACH ROW EXECUTE FUNCTION {schema}.mineagent_oe_run_lock();"
        )
    return "\n".join(out)

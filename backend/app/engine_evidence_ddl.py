"""Offline, INCOMPLETE engine-evidence SQL materializer. Never imported by startup.

This emits review inputs only, not an Alembic revision or activation capability.
No connection, execute, create_all, model registry or service hook exists here.
The caller must separately close every obligation before any migration is used.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import sys
from pathlib import Path

from sqlalchemy import MetaData, UniqueConstraint
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable

from script.check_engine_domain_catalog import (
    KNOWN_MISSING,
    catalog,
    require_valid_catalog,
)

MODULES = (
    "backend.app.rdagent_models",
    "backend.app.openevolve_models",
    "backend.app.openevolve_event_models",
    "backend.app.openevolve_worker_models",
)
OBLIGATIONS = (
    "Closed purpose/engine/config grammar and complete immutable run/spec agreement",
    "Versioned object/context/request/profile codecs; no revision-015 widening",
    "RD generation/call/member/assembly/source/lineage/evaluation/cutoff closure",
    "RD adopted/continuation source and authenticated boundary/checkpoint closure",
    "OE grant/proposal/objective membership and eligibility/zero/error semantics",
    "OE sampling/control/frontier/prefetched-tail/checkpoint complete closure",
    "Exact live controller/worker assignments, phase authority and no stale IPC grants",
    "Typed stopped-process receipts, input/image/bootstrap and physical outcome binding",
    "Reservation purpose/vector/logical-key/consumer scope and settlement conservation",
    "Version-specific backup/export/restore inventory including raw HDF and Parquet",
    "Real PostgreSQL concurrency/isolation/replay/fencing/upgrade/cold-restore tests",
    "Owner-reviewed registration, runtime containment and end-to-end browser acceptance",
)

APPEND_ONLY_SQL = """CREATE FUNCTION public.mineagent_engine_evidence_immutable()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'Engine evidence is append-only';
END $$;"""

# A deferred SELECT/count alone has a write-skew hole under repeatable-read.
# Proposed immediate unique arbitration avoids relying solely on snapshot reads.
# At READ COMMITTED a duplicate claim can observe the winner; at REPEATABLE READ
# an invisible winner must fail closed and retry the transaction. SERIALIZABLE
# additionally requires serialization-failure retries. None is PostgreSQL-proven
# here: real concurrent upgrade/claim/replay tests at all levels remain mandatory.
CONSUMER_SQL = """-- Execute the entire reviewed future migration in one transaction.
-- Block consumer writes during legacy backfill and trigger installation.
LOCK TABLE public.research_object_publications, public.factor_source_publications IN SHARE ROW EXCLUSIVE MODE;
CREATE TABLE public.engine_object_reservation_consumers (
 reservation_id uuid PRIMARY KEY,
 owner_id uuid NOT NULL, run_id uuid NOT NULL, attempt_id uuid NOT NULL, generation integer NOT NULL CHECK (generation > 0),
 FOREIGN KEY (owner_id, run_id, attempt_id, generation, reservation_id) REFERENCES public.research_budget_reservations(owner_id, run_id, attempt_id, generation, id),
 consumer_kind text NOT NULL CHECK (consumer_kind IN ('research_object','factor_source')),
 consumer_id uuid NOT NULL,
 UNIQUE (consumer_kind, consumer_id)
);
INSERT INTO public.engine_object_reservation_consumers
 SELECT budget_reservation_id, owner_id, run_id, attempt_id, generation, 'research_object', id FROM public.research_object_publications
 UNION ALL
 SELECT budget_reservation_id, owner_id, run_id, attempt_id, generation, 'factor_source', id FROM public.factor_source_publications;

CREATE FUNCTION public.mineagent_engine_object_claim() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE kind text; previous public.engine_object_reservation_consumers%ROWTYPE;
BEGIN
 IF TG_TABLE_NAME = 'research_object_publications' THEN kind := 'research_object';
 ELSIF TG_TABLE_NAME = 'factor_source_publications' THEN kind := 'factor_source';
 ELSE RAISE EXCEPTION 'Unknown reservation consumer'; END IF;
 BEGIN
  INSERT INTO public.engine_object_reservation_consumers
  VALUES (NEW.budget_reservation_id, NEW.owner_id, NEW.run_id, NEW.attempt_id, NEW.generation, kind, NEW.id);
 EXCEPTION WHEN unique_violation THEN
  SELECT * INTO previous FROM public.engine_object_reservation_consumers WHERE reservation_id = NEW.budget_reservation_id;
  IF NOT FOUND OR (previous.owner_id, previous.run_id, previous.attempt_id, previous.generation, previous.consumer_kind, previous.consumer_id)
    IS DISTINCT FROM (NEW.owner_id, NEW.run_id, NEW.attempt_id, NEW.generation, kind, NEW.id) THEN
   RAISE EXCEPTION 'Reservation already belongs to another consumer';
  END IF;
 END;
 RETURN NEW;
END $$;

CREATE FUNCTION public.mineagent_engine_object_consistent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE rid uuid; claim public.engine_object_reservation_consumers%ROWTYPE;
        actual_count bigint; matching_count bigint;
BEGIN
 IF TG_TABLE_NAME = 'engine_object_reservation_consumers' THEN rid := NEW.reservation_id;
 ELSIF TG_TABLE_NAME IN ('research_object_publications','factor_source_publications') THEN rid := NEW.budget_reservation_id;
 ELSE RAISE EXCEPTION 'Unknown reservation consumer'; END IF;
 SELECT * INTO claim FROM public.engine_object_reservation_consumers WHERE reservation_id = rid;
 IF NOT FOUND THEN RAISE EXCEPTION 'Reservation consumer claim missing'; END IF;
 SELECT count(*), count(*) FILTER (WHERE kind = claim.consumer_kind AND id = claim.consumer_id AND owner_id = claim.owner_id AND run_id = claim.run_id AND attempt_id = claim.attempt_id AND generation = claim.generation)
 INTO actual_count, matching_count FROM (
  SELECT 'research_object' AS kind, id, owner_id, run_id, attempt_id, generation FROM public.research_object_publications WHERE budget_reservation_id = rid
  UNION ALL
  SELECT 'factor_source' AS kind, id, owner_id, run_id, attempt_id, generation FROM public.factor_source_publications WHERE budget_reservation_id = rid
 ) consumers;
 IF actual_count <> 1 OR matching_count <> 1 THEN
  RAISE EXCEPTION 'Reservation requires exactly one matching physical consumer';
 END IF;
 RETURN NEW;
END $$;

CREATE TRIGGER engine_object_claim BEFORE INSERT ON public.research_object_publications
 FOR EACH ROW EXECUTE FUNCTION public.mineagent_engine_object_claim();
CREATE TRIGGER engine_source_claim BEFORE INSERT ON public.factor_source_publications
 FOR EACH ROW EXECUTE FUNCTION public.mineagent_engine_object_claim();
CREATE CONSTRAINT TRIGGER engine_object_consistent AFTER INSERT ON public.research_object_publications
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.mineagent_engine_object_consistent();
CREATE CONSTRAINT TRIGGER engine_source_consistent AFTER INSERT ON public.factor_source_publications
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.mineagent_engine_object_consistent();
CREATE CONSTRAINT TRIGGER engine_claim_consistent AFTER INSERT ON public.engine_object_reservation_consumers
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.mineagent_engine_object_consistent();
CREATE TRIGGER engine_claim_immutable BEFORE UPDATE OR DELETE ON public.engine_object_reservation_consumers
 FOR EACH ROW EXECUTE FUNCTION public.mineagent_engine_evidence_immutable();
"""


def sha(value):
    return hashlib.sha256(value).hexdigest()


def identifier(value):
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value) is None
    ):
        raise ValueError("Invalid closed metadata identifier")
    return '"' + value + '"'


def _name(prefix, table, detail):
    return prefix + "_" + sha((table + "\0" + detail).encode())[:24]


def materialize(metadata, domain_tables):
    """Pure metadata-to-text transform; never mutates the supplied metadata."""
    names = frozenset(domain_tables)
    # Public entry is repository_snapshot; this helper accepts only already
    # imported metadata, never arbitrary names supplied through CLI/request.
    for name in names:
        identifier(name)
    report = catalog(metadata, names)
    require_valid_catalog(report, allow_known_missing=True)
    if report["mutual_cycles"]:
        raise ValueError(
            "Cross-table cycles require a separately reviewed staged FK plan"
        )
    clone = MetaData()
    for table in sorted(metadata.tables.values(), key=lambda t: t.fullname):
        table.to_metadata(clone)
    external = []
    added = []
    for item in report["missing_unique_targets"]:
        target, columns = item["table"], tuple(item["columns"])
        if (target, columns) not in KNOWN_MISSING:
            raise ValueError("Unreviewed missing FK target")
        unique = UniqueConstraint(
            *columns, name=_name("uq_engine", target, ",".join(columns))
        )
        clone.tables[target].append_constraint(unique)
        added.append({"table": target, "columns": list(columns), "name": unique.name})
        if target not in names:
            external.append(
                str(AddConstraint(unique).compile(dialect=dialect())).strip() + ";"
            )
    # All constraints get stable names, making subsequent review/upgrade exact.
    for name in sorted(names):
        table = clone.tables[name]
        for constraint in table.constraints:
            if constraint.name is None:
                detail = (
                    type(constraint).__name__
                    + ":"
                    + ",".join(c.name for c in constraint.columns)
                )
                if hasattr(constraint, "sqltext"):
                    detail += ":" + str(constraint.sqltext)
                if hasattr(constraint, "elements"):
                    detail += ":" + ",".join(
                        e.target_fullname for e in constraint.elements
                    )
                constraint.name = _name("ec", name, detail)
        stable = sorted(table.constraints, key=lambda c: str(c.name))
        if len({c.name for c in stable}) != len(stable):
            raise ValueError("Duplicate deterministic constraint identity")
        # SQLAlchemy clones constraints from a set; their new creation counters
        # otherwise vary with PYTHONHASHSEED. Normalize only the private clone's
        # compiler ordering, never global application metadata.
        for ordinal, constraint in enumerate(stable):
            constraint._creation_order = ordinal
    verified = catalog(clone, names)
    require_valid_catalog(verified)
    ordered = [group[0] for group in report["dependency_groups"]]
    statements = [
        "-- INCOMPLETE OFFLINE DRAFT: NOT AN ACTIVATION MIGRATION.",
        "SET LOCAL search_path = public, pg_catalog;",
        *external,
    ]
    for name in ordered:
        table = clone.tables[name]
        statements.append(
            str(CreateTable(table).compile(dialect=dialect())).strip() + ";"
        )
        statements.extend(
            str(CreateIndex(index).compile(dialect=dialect())).strip() + ";"
            for index in sorted(table.indexes, key=lambda i: i.name or "")
        )
    statements.append(APPEND_ONLY_SQL)
    for name in sorted(names):
        statements.append(
            f"CREATE TRIGGER engine_evidence_immutable BEFORE UPDATE OR DELETE ON public.{identifier(name)} FOR EACH ROW EXECUTE FUNCTION public.mineagent_engine_evidence_immutable();"
        )
    if "factor_source_publications" not in names:
        raise ValueError("Closed source publication table required")
    statements.append(CONSUMER_SQL)
    sql = "\n\n".join(statements) + "\n"
    return {
        "schema_version": "mineagent.engine-evidence-ddl-draft.v1",
        "activation_ready": False,
        "sql": sql,
        "sql_sha256": sha(sql.encode()),
        "domain_tables": ordered,
        "required_unique_additions": added,
        "remaining_obligations": list(OBLIGATIONS),
        "reservation_registry": "engine_object_reservation_consumers",
        "claim": "Offline SQL text only; no PostgreSQL execution or semantic closure acceptance",
    }


def repository_snapshot(root):
    if any(name in sys.modules for name in MODULES):
        raise RuntimeError("Fresh interpreter required for source-pinned draft")
    root = Path(root).resolve()
    paths = sorted((root / "backend/app").glob("*models.py")) + [
        root / "backend/app/engine_evidence_ddl.py",
        root / "script/check_engine_domain_catalog.py",
    ]
    before = {p.relative_to(root).as_posix(): sha(p.read_bytes()) for p in paths}
    modules = [importlib.import_module(name) for name in MODULES]
    names = {
        obj.__table__.fullname
        for module in modules
        for obj in vars(module).values()
        if isinstance(obj, type)
        and obj.__module__ == module.__name__
        and hasattr(obj, "__table__")
    }
    result = materialize(modules[0].Base.metadata, names)
    after = {p.relative_to(root).as_posix(): sha(p.read_bytes()) for p in paths}
    if before != after:
        raise RuntimeError("Model source drift while materializing draft")
    result["source_sha256"] = before
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = repository_snapshot(Path(__file__).resolve().parents[2])
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    main()

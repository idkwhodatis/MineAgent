"""Offline draft-schema catalog. No registration, DDL execution or DB connection.

Compiling CREATE TABLE does not resolve PostgreSQL FK uniqueness. This checker
enumerates the referenced column tuples, checks full nonpartial unique keys,
and reports known activation prerequisites separately from unexpected defects.
Passing --allow-known-missing is a *draft audit*, never activation acceptance.
Named CHECK coverage is inventory, not proof that a predicate is sufficient.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from pathlib import Path
from typing import get_args

from sqlalchemy import CheckConstraint, PrimaryKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.schema import CreateTable

DOMAIN_MODULES = (
    "backend.app.rdagent_models",
    "backend.app.openevolve_models",
    "backend.app.openevolve_event_models",
)
KNOWN_MISSING = frozenset(
    {
        ("factor_objects", ("owner_id", "id", "object_kind", "sha256")),
        ("factor_versions", ("owner_id", "id", "code_sha256")),
        ("strategy_versions", ("owner_id", "id", "content_sha256")),
        ("dataset_versions", ("owner_id", "id", "content_sha256")),
        (
            "strategy_search_epochs",
            ("owner_id", "run_id", "attempt_id", "generation", "id", "admission_id"),
        ),
        (
            "research_model_operations",
            ("owner_id", "run_id", "attempt_id", "generation", "id", "context_id"),
        ),
        (
            "research_process_receipts",
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "admission_id",
                "id",
                "kind",
            ),
        ),
    }
)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def unique_keys(table):
    keys = []
    for constraint in table.constraints:
        if isinstance(constraint, (PrimaryKeyConstraint, UniqueConstraint)):
            columns = tuple(c.name for c in constraint.columns)
            if columns and not constraint.deferrable:
                keys.append(columns)
    for index in table.indexes:
        if (
            index.unique
            and index.dialect_options["postgresql"].get("where") is None
            and all(getattr(e, "table", None) is table for e in index.expressions)
        ):
            keys.append(tuple(e.name for e in index.expressions))
    return sorted(set(keys))


def dependency_groups(graph):
    """Dependency-first SCCs; mutual cycles need staged FK creation.

    A self-reference is reported but can be created inline on its own table.
    """
    visited, stack, active, low, serial, groups = {}, [], set(), {}, [0], []

    def visit(node):
        visited[node] = low[node] = serial[0]
        serial[0] += 1
        stack.append(node)
        active.add(node)
        for target in sorted(graph[node]):
            if target not in visited:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in active:
                low[node] = min(low[node], visited[target])
        if low[node] == visited[node]:
            members = []
            while True:
                member = stack.pop()
                active.remove(member)
                members.append(member)
                if member == node:
                    break
            groups.append(sorted(members))

    for node in sorted(graph):
        if node not in visited:
            visit(node)
    return groups


def catalog(metadata, domain_tables, *, expected_missing=KNOWN_MISSING):
    names = set(domain_tables)
    errors, tables, missing = [], {}, {}
    graph = {name: set() for name in names}
    for name in sorted(names):
        if name not in metadata.tables:
            errors.append(f"Missing domain table: {name}")
            continue
        table = metadata.tables[name]
        checks = sorted(
            (
                {"name": c.name, "sql": str(c.sqltext)}
                for c in table.constraints
                if isinstance(c, CheckConstraint)
            ),
            key=lambda c: (c["name"] or "", c["sql"]),
        )
        check_names = [c["name"] for c in checks if c["name"]]
        if len(set(check_names)) != len(check_names):
            errors.append(f"Duplicate CHECK names: {name}")
        references = []
        for fk in table.foreign_key_constraints:
            try:
                target = fk.referred_table
                remote = tuple(e.column.name for e in fk.elements)
                keys = unique_keys(target)
                exact = remote in keys
                reordered = [
                    k for k in keys if len(k) == len(remote) and set(k) == set(remote)
                ]
                match = "exact" if exact else "permuted" if reordered else "missing"
                reference = {
                    "name": fk.name,
                    "local_columns": [c.name for c in fk.columns],
                    "target_table": target.fullname,
                    "target_columns": list(remote),
                    "unique_match": match,
                    "matching_unique_columns": list(remote if exact else reordered[0])
                    if exact or reordered
                    else None,
                    "deferrable": bool(fk.deferrable),
                    "use_alter": bool(fk.use_alter),
                }
                if target.fullname in names:
                    graph[name].add(target.fullname)
                if match == "missing":
                    key = (target.fullname, remote)
                    missing.setdefault(key, set()).add(name)
                references.append(reference)
            except SQLAlchemyError as exc:
                # NoSuchTable/Column and malformed reference errors are hard
                # failures; do not downgrade them to an expected missing unique.
                errors.append(f"Unresolved FK in {name}: {type(exc).__name__}: {exc}")
        try:
            ddl_sha = _sha(str(CreateTable(table).compile(dialect=dialect())).encode())
        except SQLAlchemyError as exc:
            ddl_sha = None
            errors.append(f"DDL compile failed for {name}: {type(exc).__name__}: {exc}")
        tables[name] = {
            "ddl_sha256": ddl_sha,
            "unique_keys": [list(k) for k in unique_keys(table)],
            "references": sorted(
                references, key=lambda r: json.dumps(r, sort_keys=True)
            ),
            "checks": checks,
            "check_count": len(checks),
            "named_check_count": len(check_names),
            "all_checks_named": len(checks) == len(check_names),
        }
    missing_report = [
        {
            "table": target,
            "columns": list(columns),
            "referenced_by": sorted(users),
            "known_activation_prerequisite": (target, columns) in expected_missing,
        }
        for (target, columns), users in sorted(missing.items())
    ]
    groups = dependency_groups(graph)
    cycles = [g for g in groups if len(g) > 1 or g[0] in graph[g[0]]]
    return {
        "schema_version": "mineagent.engine-schema-catalog.v1",
        "claim": "offline metadata audit only; no PostgreSQL, trigger or migration acceptance",
        "tables": tables,
        "missing_unique_targets": missing_report,
        "errors": sorted(errors),
        "dependency_groups": groups,
        "cycles": cycles,
        "mutual_cycles": [group for group in cycles if len(group) > 1],
        "external_dependencies": sorted(
            {
                r["target_table"]
                for t in tables.values()
                for r in t["references"]
                if r["target_table"] not in names
            }
        ),
        "activation_ready": False,
    }


def require_valid_catalog(report, *, allow_known_missing=False):
    problems = list(report["errors"])
    for missing in report["missing_unique_targets"]:
        if not allow_known_missing or not missing["known_activation_prerequisite"]:
            problems.append(
                f"Missing unique target: {missing['table']} {missing['columns']}"
            )
    if problems:
        raise ValueError("; ".join(problems))


def repository_catalog(root):
    if any(name in sys.modules for name in ("backend.app.models", *DOMAIN_MODULES)):
        raise RuntimeError(
            "Repository catalog requires a fresh process, not cached models"
        )
    root = Path(root).resolve()
    # Hash before imports and after catalog generation: a worker changing a
    # model midway must not produce a falsely source-pinned catalog.
    sources = sorted((root / "backend/app").glob("*models.py"))
    sources.append(root / "packages/rdagent_adapter/prefix.py")
    sources.append(root / "script/check_engine_domain_catalog.py")
    before = {p.relative_to(root).as_posix(): _sha(p.read_bytes()) for p in sources}
    base = importlib.import_module("backend.app.models").Base
    modules = [importlib.import_module(name) for name in DOMAIN_MODULES]
    names = {
        obj.__table__.fullname
        for module in modules
        for obj in vars(module).values()
        if isinstance(obj, type)
        and obj.__module__ == module.__name__
        and hasattr(obj, "__table__")
    }
    report = catalog(base.metadata, names)
    prefix = importlib.import_module("packages.rdagent_adapter.prefix").PrefixManifest
    expected = get_args(prefix.model_fields["schema_version"].annotation)
    checks = report["tables"]["factor_cutoff_executions"]["checks"]
    actual = [c["sql"] for c in checks if "projection->>'schema_version'" in c["sql"]]
    prefix_ok = (
        len(expected) == 1 and len(actual) == 1 and f"'{expected[0]}'" in actual[0]
    )
    report["semantic_checks"] = {"cutoff_prefix_schema_matches_contract": prefix_ok}
    if not prefix_ok:
        report["errors"].append("RD cutoff CHECK and PrefixManifest schema differ")
    after = {p.relative_to(root).as_posix(): _sha(p.read_bytes()) for p in sources}
    report["source_sha256"] = before
    report["source_drift"] = sorted(k for k in before if before[k] != after[k])
    if report["source_drift"]:
        report["errors"].append("Source changed while catalog was built")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-known-missing", action="store_true")
    args = parser.parse_args()
    report = repository_catalog(Path(__file__).resolve().parents[1])
    encoded = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded)
    else:
        print(encoded, end="")
    try:
        require_valid_catalog(report, allow_known_missing=args.allow_known_missing)
    except ValueError as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    main()

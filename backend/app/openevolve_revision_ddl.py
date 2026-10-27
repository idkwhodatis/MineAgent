"""Offline 024 assembler. Generated literals, not this module, enter Alembic.

Prior predicates are copied byte-for-byte from reviewed historical migration
constants. No runtime reflection, best-effort dropping or CASCADE restoration.
"""

import ast
import re
from pathlib import Path

from backend.app.openevolve_identity_sql import identity_sql
from backend.app.openevolve_operational_ddl import render_sql as operational_sql
from backend.app.openevolve_persistence_ddl import EXTERNAL_UNIQUES, domain_sql, tables
from backend.app.openevolve_process_ddl import render_sql as process_sql
from backend.app.openevolve_semantic_ddl import fragment
from backend.app.openevolve_shared_ddl import render_sql as shared_sql
from backend.app.openevolve_worker_ddl import render_sql as worker_sql

ROOT = Path(__file__).resolve().parents[1] / "alembic/versions"
SENTINEL = "oe024_installation_schema"


def historical(revision, name):
    paths = list(ROOT.glob(revision + "_*.py"))
    if len(paths) != 1:
        raise ValueError("Unique reviewed migration source required")
    node = ast.parse(paths[0].read_text())
    values = [
        n.value.value
        for n in node.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)
        and isinstance(n.value, ast.Constant)
        and isinstance(n.value.value, str)
    ]
    if len(values) != 1:
        raise ValueError("Exact historical SQL literal required")
    return values[0]


def _check(sql, name):
    match = re.search(r"ADD CONSTRAINT " + re.escape(name) + r" CHECK\s*\(", sql)
    if not match:
        raise ValueError("Historical CHECK absent: " + name)
    start = match.end() - 1
    depth, quote, i = 0, False, start
    while i < len(sql):
        c = sql[i]
        if c == "'":
            if quote and i + 1 < len(sql) and sql[i + 1] == "'":
                i += 2
                continue
            quote = not quote
        elif not quote:
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return sql[start : i + 1]
        i += 1
    raise ValueError("Unclosed reviewed CHECK")


def _function(sql, name):
    match = re.search(
        r"CREATE (?:OR REPLACE )?FUNCTION (?:@SCHEMA@\.)?" + name + r"\(", sql
    )
    if not match:
        raise ValueError("Historical function absent: " + name)
    end = sql.index("END $$;", match.start()) + len("END $$;")
    value = sql[match.start() : end]
    return re.sub(
        r"CREATE (?:OR REPLACE )?FUNCTION (?:@SCHEMA@\.)?",
        "CREATE OR REPLACE FUNCTION @SCHEMA@.",
        value,
        count=1,
    )


def upgrade_sql():
    semantic = fragment()
    # Old fragment is intentionally installation-neutral; freeze qualified names
    # and function-local search_path rather than trusting later caller settings.
    semantic = semantic.replace(
        "CREATE FUNCTION mineagent_oe_", f"CREATE FUNCTION {SENTINEL}.mineagent_oe_"
    )
    semantic = semantic.replace(
        "LANGUAGE plpgsql AS $$",
        f"LANGUAGE plpgsql SET search_path={SENTINEL},pg_catalog AS $$",
    )
    semantic = re.sub(r" ON (search_[a-z_]+)\n", rf" ON {SENTINEL}.\1\n", semantic)
    semantic = semantic.replace(
        "EXECUTE FUNCTION mineagent_oe_", f"EXECUTE FUNCTION {SENTINEL}.mineagent_oe_"
    )
    return "\n".join(
        (
            identity_sql(SENTINEL),
            domain_sql(SENTINEL),
            shared_sql(SENTINEL),
            process_sql(SENTINEL),
            operational_sql(SENTINEL),
            worker_sql(SENTINEL),
            semantic,
        )
    ).replace(SENTINEL, "@SCHEMA@")


def downgrade_sql():
    old18, old20 = historical("018", "UPGRADE_SQL"), historical("020", "UPGRADE_SQL")
    old21, old22 = historical("021", "RD_UPGRADE_SQL"), historical("022", "PROFILE_UP")
    names = tuple(sorted(tables()))
    checks = [
        ("research_runs", "ck_research_run_identity", old18),
        ("research_runs", "ck_research_run_legacy_config", old18),
        ("research_model_operations", "ck_model_versioned_request", old18),
        ("research_operation_contexts", "ck_versioned_operation_context", old18),
        ("research_process_admissions", "ck_process_v2_unregistered", old22),
        *[
            (t, f"ck_{t}_versioned_kind", old21)
            for t in ("research_objects", "research_object_publications")
        ],
    ]
    out = [
        "DO $$ BEGIN IF "
        + " OR ".join(f"EXISTS(SELECT 1 FROM @SCHEMA@.{n})" for n in names)
        + " OR EXISTS(SELECT 1 FROM @SCHEMA@.research_runs WHERE engine='openevolve')"
        + " THEN RAISE EXCEPTION '024 retained evidence cannot be represented before024'; END IF; END $$;"
    ]
    for table, name, source in checks:
        out.append(
            f"ALTER TABLE @SCHEMA@.{table} DROP CONSTRAINT {name}, ADD CONSTRAINT {name} CHECK "
            + _check(source, name)
            + ";"
        )
    for name, source in (
        ("mineagent_rd_shared_consistent", old20),
        ("mineagent_rd_controller_consistent", old20),
        ("mineagent_rd_controller_receipt_order", old20),
        ("mineagent_rd_source_run_lock", old18),
    ):
        out.append(_function(source, name))
    up = upgrade_sql()
    # Remove new deferred triggers on historical tables before dropping functions.
    for trigger, table in re.findall(
        r"CREATE CONSTRAINT TRIGGER (\w+) .*? ON @SCHEMA@\.(\w+) .*?EXECUTE FUNCTION",
        up,
    ):
        if table not in names:
            out.append(f"DROP TRIGGER {trigger} ON @SCHEMA@.{table};")
    out.append("DROP TABLE " + ",".join("@SCHEMA@." + n for n in names) + " RESTRICT;")
    functions = re.findall(
        r"CREATE (?:OR REPLACE )?FUNCTION @SCHEMA@\.(mineagent_oe_\w+)\(([^)]*)\)", up
    )
    for name, args in reversed(functions):
        types = []
        for arg in args.split(","):
            if arg.strip():
                parts = arg.strip().split()
                types.append(parts[1])
        out.append(f"DROP FUNCTION @SCHEMA@.{name}({','.join(types)}) RESTRICT;")
    for table in EXTERNAL_UNIQUES:
        out.append(f"ALTER TABLE @SCHEMA@.{table} DROP CONSTRAINT oe024_uq_{table};")
    return "\n".join(out)

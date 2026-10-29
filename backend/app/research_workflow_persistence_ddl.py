"""Offline exact twelve-table028 compiler, not installation authority.

All foreign keys are emitted separately as deferred constraints so the actual
same-transaction root/stage/revision/child cycles stay intact. No legacy table is
re-created; the literal migration must install all semantic guards atomically.
"""

import hashlib
import re

from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable

from backend.app import (
    rdagent_workflow_models,
    research_input_models,
    research_workflow_models,
)

TABLES = (
    "research_workflows",
    "research_workflow_revisions",
    "research_workflow_stages",
    "research_workflow_checkpoints",
    "research_workflow_commands",
    "research_workflow_model_consumption",
    "research_input_objects",
    "research_input_derivations",
    "research_input_records",
    "research_input_versions",
    "research_input_derivation_events",
    "factor_workflow_submissions",
)
MODULES = (research_workflow_models, research_input_models, rdagent_workflow_models)


def tables():
    result = {
        value.__table__.name: value.__table__
        for module in MODULES
        for value in vars(module).values()
        if isinstance(value, type)
        and value.__module__ == module.__name__
        and hasattr(value, "__table__")
    }
    if set(result) != set(TABLES):
        raise ValueError("Exact reviewed028 table inventory required")
    return dict(sorted(result.items()))


def _schema(schema):
    if type(schema) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema) is None:
        raise ValueError("Exact installation schema required")
    return schema


def foreign_keys():
    result = []
    for name, table in tables().items():
        for fk in sorted(
            table.foreign_key_constraints,
            key=lambda value: tuple(
                (e.parent.name, e.target_fullname) for e in value.elements
            ),
        ):
            local = ",".join(e.parent.name for e in fk.elements)
            target = fk.elements[0].column.table.name
            remote = ",".join(e.column.name for e in fk.elements)
            identity = f"{name}:{local}:{target}:{remote}"
            key = "workflow028_fk_" + hashlib.sha256(identity.encode()).hexdigest()[:20]
            result.append((name, key, local, target, remote))
    return tuple(result)


def render_upgrade_sql(schema="public"):
    schema = _schema(schema)
    result = []
    for name, table in tables().items():
        sql = str(
            CreateTable(table, include_foreign_key_constraints=[]).compile(
                dialect=dialect(paramstyle="named")
            )
        ).strip()
        sql = sql.replace(
            f"CREATE TABLE {name} (", f"CREATE TABLE {schema}.{name} (", 1
        )
        result.append(sql + ";")
    for name, key, local, target, remote in foreign_keys():
        result.append(
            f"ALTER TABLE {schema}.{name} ADD CONSTRAINT {key} FOREIGN KEY ({local}) REFERENCES {schema}.{target} ({remote}) DEFERRABLE INITIALLY DEFERRED;"
        )
    return "\n".join(result)


def render_downgrade_sql(schema="public"):
    schema = _schema(schema)
    result = [
        f"ALTER TABLE {schema}.{name} DROP CONSTRAINT {key};"
        for name, key, *_ in reversed(foreign_keys())
    ]
    result += [f"DROP TABLE {schema}.{name};" for name in reversed(tables())]
    return "\n".join(result)

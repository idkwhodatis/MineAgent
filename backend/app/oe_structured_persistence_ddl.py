"""Offline027 table compiler; not model registration or migration activation.

The eventual literal migration installs all semantic/deferred triggers in the
same transaction. Every FK is emitted separately and deferred, including the
same-transaction epoch/admission/seed cycle. No legacy table is re-created.
"""

import hashlib
import re

from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable

from backend.app import (
    oe_structured_checkpoint_models,
    oe_structured_models,
    oe_structured_operational_models,
    oe_structured_sampling_models,
    oe_structured_terminal_models,
    rdagent_numeric_failure_models,
)
from backend.app.oe_structured_trigger_sql import TABLES

EXTERNAL_UNIQUES = {"dataset_versions": ("owner_id", "id")}

MODULES = (
    oe_structured_models,
    oe_structured_operational_models,
    oe_structured_sampling_models,
    oe_structured_terminal_models,
    oe_structured_checkpoint_models,
    rdagent_numeric_failure_models,
)


def tables():
    result = {
        value.__table__.name: value.__table__
        for module in MODULES
        for value in vars(module).values()
        if isinstance(value, type)
        and value.__module__ == module.__name__
        and hasattr(value, "__table__")
    }
    if set(result) != set(TABLES) | {"factor_numeric_failure_feedbacks"}:
        raise ValueError("Exact reviewed027 table inventory required")
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
            key = "oes027_fk_" + hashlib.sha256(identity.encode()).hexdigest()[:20]
            result.append((name, key, local, target, remote))
    return tuple(result)


def render_upgrade_sql(schema="public"):
    schema = _schema(schema)
    result = [
        f"ALTER TABLE {schema}.{name} ADD CONSTRAINT oes027_uq_{name} UNIQUE ({','.join(columns)});"
        for name, columns in EXTERNAL_UNIQUES.items()
    ]
    for name, table in tables().items():
        sql = str(
            CreateTable(table, include_foreign_key_constraints=[]).compile(
                dialect=dialect(paramstyle="named")
            )
        ).strip()
        sql = sql.replace(
            f"CREATE TABLE {name} (", f"CREATE TABLE {schema}.{name} (", 1
        )
        # Explicit031 origin expansion is not a historical028 DDL rewrite.
        # Project only these reviewed CHECK spellings back to frozen legacy.
        if name == "oe_structured_submissions":
            new = "origin IN ('authenticated_user_submission','workflow_stage')"
            if sql.count(new) != 1:
                raise ValueError("Exact031 submission origin projection required")
            sql = sql.replace(new, "origin='authenticated_user_submission'")
        elif name == "oe_structured_candidates":
            new = "origin IN ('authenticated_user_seed','workflow_seed')"
            if sql.count(new) != 1:
                raise ValueError("Exact031 seed origin projection required")
            sql = sql.replace(new, "origin='authenticated_user_seed'")
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
    result += [
        f"ALTER TABLE {schema}.{name} DROP CONSTRAINT oes027_uq_{name};"
        for name in EXTERNAL_UNIQUES
    ]
    return "\n".join(result)

"""031 additive attachment and explicit origin constraints; no implicit admission."""

import hashlib
import importlib

from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable

from backend.app.oe_workflow_models import OEWorkflowSubmission

OLD_SUBMISSION = "origin='authenticated_user_submission' AND octet_length(idempotency_key) BETWEEN 1 AND 128 AND octet_length(request_canonical) BETWEEN 1 AND 65536 AND request_sha256 ~ '^[a-f0-9]{64}$' AND preflight_sha256 ~ '^[a-f0-9]{64}$' AND config_sha256 ~ '^[a-f0-9]{64}$'"
NEW_SUBMISSION = OLD_SUBMISSION.replace(
    "origin='authenticated_user_submission'",
    "origin IN ('authenticated_user_submission','workflow_stage')",
)
OLD_CANDIDATE = "(ordinal=0 AND origin='authenticated_user_seed' AND parent_candidate_id IS NULL AND provider_exchange_id IS NULL) OR (ordinal>0 AND origin='model_proposal' AND parent_candidate_id IS NOT NULL AND provider_exchange_id IS NOT NULL AND parent_candidate_id<>id)"
NEW_CANDIDATE = OLD_CANDIDATE.replace(
    "origin='authenticated_user_seed'",
    "origin IN ('authenticated_user_seed','workflow_seed')",
)
ORIGINS = (
    (
        "oe_structured_submissions",
        "oe_structured_submissions_check",
        OLD_SUBMISSION,
        NEW_SUBMISSION,
    ),
    (
        "oe_structured_candidates",
        "oe_structured_candidates_check1",
        OLD_CANDIDATE,
        NEW_CANDIDATE,
    ),
)


def origin_sql(*, upgrade):
    return "\n".join(
        f"ALTER TABLE @SCHEMA@.{table} DROP CONSTRAINT {name};\n"
        f"ALTER TABLE @SCHEMA@.{table} ADD CONSTRAINT {name} CHECK ({new if upgrade else old});"
        for table, name, old, new in ORIGINS
    )


def render_table_sql():
    for module in (
        "research_workflow_models",
        "oe_structured_models",
        "execution_snapshot_models",
        "factor_models",
        "factor_analysis_models",
    ):
        importlib.import_module("backend.app." + module)
    table = OEWorkflowSubmission.__table__
    sql = str(
        CreateTable(table, include_foreign_key_constraints=[]).compile(
            dialect=dialect(paramstyle="named")
        )
    ).strip()
    sql = (
        sql.replace(
            "CREATE TABLE oe_workflow_submissions (",
            "CREATE TABLE @SCHEMA@.oe_workflow_submissions (",
            1,
        )
        + ";\n"
    )
    for fk in sorted(
        table.foreign_key_constraints,
        key=lambda fk: tuple(e.parent.name for e in fk.elements),
    ):
        local = ",".join(e.parent.name for e in fk.elements)
        target = fk.referred_table.name
        remote = ",".join(e.column.name for e in fk.elements)
        key = (
            "oe031_fk_"
            + hashlib.sha256(f"{local}:{target}:{remote}".encode()).hexdigest()[:20]
        )
        sql += f"ALTER TABLE @SCHEMA@.oe_workflow_submissions ADD CONSTRAINT {key} FOREIGN KEY ({local}) REFERENCES @SCHEMA@.{target} ({remote}) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;\n"
    return sql

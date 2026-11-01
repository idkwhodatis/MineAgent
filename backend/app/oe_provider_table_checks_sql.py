"""Offline exact032c CHECK repair assembler; Alembic uses frozen literals only."""

from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "alembic/versions"
PREDECESSOR_TREE_SHA256 = (
    "ce18c02a09f20c13d500ed413ad1ee7cbedebdaa27f2530981f28cf44bb7e3c9"
)
OLD_CONTEXT = "(((jsonb_typeof(context)='object' AND context ?& ARRAY['schema_version','scenario','ordinal']\n AND context-ARRAY['schema_version','scenario','ordinal']='{}'::jsonb AND context->>'schema_version'='mineagent.neutral-fixture-context.v1'\n AND context->>'scenario' IN ('chat','embedding','retry','continuation','cancellation') AND jsonb_typeof(context->'ordinal')='number'\n AND context->>'ordinal' ~ '^[0-9]+$' AND (context->>'ordinal')::numeric BETWEEN 0 AND 1000000) IS TRUE\n OR @SCHEMA@.mineagent_rd_source_context(context) IS TRUE OR @SCHEMA@.mineagent_oe_context(context) IS TRUE))"
OLD_REQUEST = "(request_object_kind = 'model_request')"
OLD_OUTCOME = "(((outcome = 'completed' AND response_object_id IS NOT NULL AND response_object_kind = 'model_response' AND response_sha256 ~ '^[0-9a-f]{64}$') OR (outcome IN ('failed','unknown') AND response_object_id IS NULL AND response_object_kind IS NULL AND response_sha256 IS NULL)) IS TRUE)"

TABLE_CHECKS = (
    ("research_operation_contexts", "ck_versioned_operation_context", OLD_CONTEXT),
    (
        "research_model_operations",
        "research_model_operations_request_object_kind_check",
        OLD_REQUEST,
    ),
    ("research_model_outcomes", "research_model_outcomes_check", OLD_OUTCOME),
)
PROTOCOL = "mineagent.oe-structured-provider-chat.v1"


def require_predecessors():
    paths = [
        p
        for p in sorted(ROOT.glob("*"))
        if p.suffix in (".py", ".sql") and p.name.split("_")[0] <= "032b"
    ]
    manifest = "\n".join(
        p.name + ":" + sha256(p.read_bytes()).hexdigest() for p in paths
    )
    if sha256(manifest.encode()).hexdigest() != PREDECESSOR_TREE_SHA256:
        raise ValueError("Exact provider table CHECK predecessors required")


def replacements():
    return (
        f"(({OLD_CONTEXT}) OR @SCHEMA@.mineagent_oes_iteration_contract('context',context) IS TRUE)",
        f"(CASE WHEN protocol='{PROTOCOL}' THEN (purpose='chat' AND ordinal=0 AND request_object_kind='oe_structured_provider_request') ELSE {OLD_REQUEST} END) IS TRUE",
        f"(({OLD_OUTCOME}) OR (outcome='completed' AND response_object_id IS NOT NULL AND response_object_kind='oe_structured_provider_response' AND response_sha256 ~ '^[0-9a-f]{{64}}$') IS TRUE)",
    )


def replace_checks(expressions):
    return "\n".join(
        f"ALTER TABLE @SCHEMA@.{table} DROP CONSTRAINT {name}, ADD CONSTRAINT {name} CHECK ({expr});"
        for (table, name, _), expr in zip(TABLE_CHECKS, expressions, strict=True)
    )


def render_upgrade_sql():
    require_predecessors()
    return replace_checks(replacements())


def render_downgrade_sql():
    require_predecessors()
    checks = " OR ".join(
        f"EXISTS(SELECT 1 FROM @SCHEMA@.{table} WHERE ({expr}) IS NOT TRUE)"
        for table, _, expr in TABLE_CHECKS
    )
    return (
        "DO $$ BEGIN IF "
        + checks
        + " THEN RAISE EXCEPTION 'Structured provider retained rows prevent downgrade'; END IF; END $$;\n"
        + replace_checks([expr for _, _, expr in TABLE_CHECKS])
    )

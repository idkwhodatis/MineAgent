"""Offline027 literal composer. No Alembic revision is activated by this module.

Requires all shared/model/physical/cold consumers before the resulting literals
can be installed. Historical revisions are never imported as executable code.
"""

from backend.app import (
    numeric_rejection_sql,
    oe_structured_checkpoint_sql,
    oe_structured_controller_sql,
    oe_structured_domain_sql,
    oe_structured_epoch_sql,
    oe_structured_graph_sql,
    oe_structured_iteration_sql,
    oe_structured_persistence_ddl,
    oe_structured_process_extension_sql,
    oe_structured_provider_sql,
    oe_structured_shared_process_sql,
    oe_structured_shared_sql,
    oe_structured_terminal_sql,
    oe_structured_trigger_sql,
    rdagent_numeric_failure_sql,
)
from packages.openevolve_structured.object_identity import KINDS

SENTINEL = "oes027_literal_schema"
# Creation order keeps raw contracts and predecessor replacements ahead of
# enforcing deferred triggers. All references resolve before any user writes.
FRAGMENTS = (
    oe_structured_domain_sql,
    oe_structured_epoch_sql,
    oe_structured_controller_sql,
    oe_structured_iteration_sql,
    oe_structured_terminal_sql,
    oe_structured_provider_sql,
    oe_structured_checkpoint_sql,
    oe_structured_graph_sql,
    numeric_rejection_sql,
    rdagent_numeric_failure_sql,
    oe_structured_shared_sql,
    oe_structured_shared_process_sql,
    oe_structured_process_extension_sql,
    oe_structured_trigger_sql,
)


def table_sql():
    return oe_structured_persistence_ddl.render_upgrade_sql(SENTINEL).replace(
        SENTINEL, "@SCHEMA@"
    )


def render_upgrade_sql():
    return "\n".join(fragment.render_upgrade_sql() for fragment in FRAGMENTS)


def downgrade_guard():
    checks = [
        f"EXISTS(SELECT 1 FROM @SCHEMA@.{name})"
        for name in oe_structured_persistence_ddl.tables()
    ]
    kinds = ",".join("'" + kind + "'" for kind in sorted(KINDS))
    checks += [
        "EXISTS(SELECT 1 FROM @SCHEMA@.research_runs WHERE config->>'schema_version'='mineagent.oe-structured-provider-run.v1')",
        "EXISTS(SELECT 1 FROM @SCHEMA@.research_process_receipts WHERE kind IN ('controller_completion','rejection'))",
        "EXISTS(SELECT 1 FROM @SCHEMA@.research_process_admissions WHERE profile='mineagent.oe-structured-orchestrator.v1')",
        "EXISTS(SELECT 1 FROM @SCHEMA@.research_model_operations WHERE protocol='mineagent.oe-structured-provider-chat.v1')",
        f"EXISTS(SELECT 1 FROM @SCHEMA@.research_objects WHERE object_kind IN ({kinds}))",
        f"EXISTS(SELECT 1 FROM @SCHEMA@.research_object_publications WHERE object_kind IN ({kinds}))",
    ]
    return (
        "DO $$ BEGIN IF "
        + "\n OR ".join(checks)
        + "\n THEN RAISE EXCEPTION 'Retained027 evidence prevents downgrade'; END IF; END $$;"
    )


def render_downgrade_sql():
    return "\n".join(
        (
            downgrade_guard(),
            *(fragment.render_downgrade_sql() for fragment in reversed(FRAGMENTS)),
            oe_structured_persistence_ddl.render_downgrade_sql(SENTINEL).replace(
                SENTINEL, "@SCHEMA@"
            ),
        )
    )

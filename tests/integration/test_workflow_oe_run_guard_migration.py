"""Real PostgreSQL CHECK red/green without inventing workflow graph authority."""

from copy import deepcopy
from importlib import import_module
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy.exc import DBAPIError, IntegrityError

from packages.factors.contracts import canonical_bytes
from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig
from tests.integration.test_shared_validation_cost_migration import configuration
from tests.openevolve.test_workflow_run import config

pytestmark = pytest.mark.postgres
TARGET = "032h_workflow_oe_run_guard"
PREVIOUS = "032g_schema_keyset_cost"
NAME = "ck_research_run_legacy_config"
SQL = """
INSERT INTO pg_temp.oe_run_check_projection
(id, owner_id, config, purpose, engine, generation, dataset_version_id,
 research_image_digest, request_sha256, idempotency_key, status,
 next_event_seq, created_at, updated_at)
VALUES (%s, %s, %s::jsonb, 'strategy_search', 'openevolve', 1, %s,
        %s, %s, 'guard-probe', 'queued', 0, now(), now())
"""


def _install_projection(connection, schema):
    # Project only the named table CHECK. The actual full workflow gate proves
    # durable attachment ownership, not this fixture with no workflow rows.
    definition = connection.exec_driver_sql(
        """SELECT pg_get_constraintdef(oid) FROM pg_constraint
           WHERE conrelid=%s::regclass AND conname=%s""",
        (f'"{schema}".research_runs', NAME),
    ).scalar_one()
    connection.exec_driver_sql(
        f'CREATE TEMP TABLE oe_run_check_projection (LIKE "{schema}".research_runs)'
    )
    connection.exec_driver_sql(
        f"ALTER TABLE pg_temp.oe_run_check_projection ADD CONSTRAINT {NAME} {definition}"
    )


def _try_insert(connection, declaration, *, owner=None, run=None, accepted):
    values = (
        run or declaration["search"]["run_id"],
        owner or declaration["search"]["owner_id"],
        canonical_bytes(declaration).decode("ascii"),
        declaration["fixed_inputs"]["dataset_version_id"],
        declaration["runtime"]["controller_image_id"],
        "a" * 64,
    )
    savepoint = connection.begin_nested()
    try:
        if accepted:
            connection.exec_driver_sql(SQL, values)
        else:
            with pytest.raises(IntegrityError) as error:
                connection.exec_driver_sql(SQL, values)
            assert error.value.orig.sqlstate == "23514"
            assert error.value.orig.diag.constraint_name == NAME
    finally:
        savepoint.rollback()


def test_old_workflow_roe_is_red_exact_workflow_green_and_bound(migrated_engine):
    workflow = WorkflowStructuredRunConfig.model_validate_json(
        canonical_bytes(config())
    ).model_dump(mode="json")
    normal = deepcopy(workflow)
    normal["schema_version"] = "mineagent.oe-structured-provider-run.v1"
    normal["authenticated_request_sha256"] = normal.pop("workflow_request_sha256")
    normal.pop("workflow_context")
    malformed = deepcopy(workflow)
    malformed["workflow_context"]["snapshot_manifest_sha256"] = "e" * 64
    with migrated_engine.connect() as connection, connection.begin():
        schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
        cfg = configuration(connection)
        command.downgrade(cfg, PREVIOUS)
        _install_projection(connection, schema)
        _try_insert(connection, normal, accepted=True)
        _try_insert(connection, workflow, accepted=False)
        connection.exec_driver_sql("DROP TABLE pg_temp.oe_run_check_projection")
        command.upgrade(cfg, TARGET)
        _install_projection(connection, schema)
        _try_insert(connection, normal, accepted=True)
        _try_insert(connection, workflow, accepted=True)
        _try_insert(connection, workflow, owner=str(uuid4()), accepted=False)
        _try_insert(connection, workflow, run=str(uuid4()), accepted=False)
        _try_insert(connection, malformed, accepted=False)
        connection.exec_driver_sql("DROP TABLE pg_temp.oe_run_check_projection")
        command.downgrade(cfg, PREVIOUS)
        _install_projection(connection, schema)
        _try_insert(connection, normal, accepted=True)
        _try_insert(connection, workflow, accepted=False)
        connection.exec_driver_sql("DROP TABLE pg_temp.oe_run_check_projection")
        # Roll back revision switches; schema teardown remains the fixture's.
        command.upgrade(cfg, TARGET)


def test_predecessor_check_drift_refuses_before_replacement(migrated_engine):
    module = import_module(f"backend.alembic.versions.{TARGET}")
    with migrated_engine.connect() as connection, connection.begin():
        schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
        cfg = configuration(connection)
        command.downgrade(cfg, PREVIOUS)
        with connection.begin_nested() as savepoint:
            connection.exec_driver_sql(
                f'ALTER TABLE "{schema}".research_runs DROP CONSTRAINT {NAME}, '
                f"ADD CONSTRAINT {NAME} CHECK (true)"
            )
            with pytest.raises(
                DBAPIError, match="Exact workflow OE run guard predecessor CHECK"
            ):
                command.upgrade(cfg, TARGET)
            savepoint.rollback()
        assert (
            connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()
            == module.down_revision
        )
        command.upgrade(cfg, TARGET)

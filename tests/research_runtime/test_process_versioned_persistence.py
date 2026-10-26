"""Pure checks complement, never replace, PostgreSQL and cold-restore gates."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_process_persistence_codec import row

from backend.app.research_process_models import ResearchProcessAdmission
from backend.app.research_process_services import as_admission
from backend.app.schema_capabilities import capabilities_for
from packages.research_runtime.process_contracts import ProcessError, canonical
from packages.research_runtime.process_persistence_codec import (
    ROW_FIELDS,
    V2,
    encode_row,
)


def migration():
    path = (
        Path(__file__).resolve().parents[2]
        / "backend/alembic/versions/016_process_versioning.py"
    )
    spec = importlib.util.spec_from_file_location("process_versioning", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_service_decodes_explicit_v1_without_changing_commitment():
    value = row()
    admission = as_admission(SimpleNamespace(**value))
    assert encode_row(admission) == value
    assert "schema_version" not in admission.public()
    assert "ipc_policy" not in admission.public()
    assert canonical(admission.public()) == canonical(
        as_admission(SimpleNamespace(**encode_row(admission))).public()
    )


def test_service_cannot_decode_unregistered_v2_as_live_admission():
    value = row(V2, "factor_cutoff_candidate")
    value.update(ipc_policy="none", ticket_sha256=None)
    with pytest.raises(ProcessError, match="not_registered"):
        as_admission(SimpleNamespace(**value))


@pytest.mark.parametrize("missing", ["schema_version", "ipc_policy"])
def test_service_never_infers_missing_discriminator(missing):
    value = row()
    value.pop(missing)
    with pytest.raises(AttributeError):
        as_admission(SimpleNamespace(**value))


def test_model_requires_explicit_columns_and_closed_source_persistence_catalog():
    columns = ResearchProcessAdmission.__table__.columns
    for name in ("schema_version", "ipc_policy"):
        assert not columns[name].nullable
        assert columns[name].default is None and columns[name].server_default is None
    assert columns["ticket_sha256"].nullable
    constraints = {
        item.name: str(item.sqltext)
        for item in ResearchProcessAdmission.__table__.constraints
        if hasattr(item, "sqltext")
    }
    from backend.app.rdagent_persistence_ddl import CONTROLLER_PROFILE_GATE

    assert constraints["ck_process_v2_unregistered"] == CONTROLLER_PROFILE_GATE
    assert ROW_FIELDS <= set(columns.keys())


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_versioned_migration_requires_maintenance_before_all_ddl(direction):
    module = migration()
    statements = []
    with patch.object(module.op, "execute", side_effect=statements.append):
        getattr(module, direction)()
    assert "pg_try_advisory_xact_lock" in statements[0]
    assert "read committed" in statements[0]
    assert (
        statements[1]
        == "LOCK TABLE research_process_admissions IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    if direction == "upgrade":
        assert "DROP DEFAULT" in statements[2]
        assert "UPDATE research_process_admissions" not in statements[2]
        assert "ck_process_v2_unregistered" in statements[2]
    else:
        assert "Versioned process evidence prevents downgrade" in statements[2]


def test_new_capability_versions_only_process_closure_and_retains_old_reader():
    old = capabilities_for("015_neutral_primitives")
    new = capabilities_for("016_process_versioning")
    assert old.research_closure_schema == "mineagent.research-restore-closure.v1"
    assert new.research_closure_schema == "mineagent.research-restore-closure.v2"
    assert {
        k: v for k, v in old.__dict__.items() if k != "research_closure_schema"
    } == {k: v for k, v in new.__dict__.items() if k != "research_closure_schema"}

"""Explicit028 inventory/dependency dispatch; not complete restore or PG proof."""


# ruff: noqa: F811 -- imported pytest fixture is intentionally used by name.

import copy
from uuid import UUID

import pytest
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint
from test_rdagent_source_backup import retained  # noqa: F401

from backend.app import rdagent_backup as backup
from backend.app.rdagent_numeric_failure_backup import FIELDS
from backend.app.rdagent_numeric_failure_models import FactorNumericFailureFeedback


def upgrade(data):
    data.closure["schema_version"] = backup.SCHEMA_V8
    data.closure["tables"]["rd_provider_transport_receipts"] = []
    data.closure["tables"]["factor_numeric_failure_feedbacks"] = []
    data.research["schema_version"] = "mineagent.research-restore-closure.v13"
    return data


def validate(data, **kwargs):
    options = {
        "seed_context": {},
        "configured_inputs_pending": True,
        "failure_links_pending": True,
    }
    options.update(kwargs)
    return backup.validate_closure(
        data.closure, data.research, data.factors, data.reader, 1000, **options
    )


def test_v6_exact_inventory_matches_actual_failure_model():
    name = "factor_numeric_failure_feedbacks"
    table = FactorNumericFailureFeedback.__table__
    assert backup.FIELDS_V7[name] == FIELDS
    assert set(FIELDS.split()) == {c.name for c in table.columns}
    assert name not in backup.FIELDS_V5
    assert set(backup.UNIQUE_KEYS_V7[name]) == {
        tuple(c.name for c in key.columns)
        for key in table.constraints
        if isinstance(key, UniqueConstraint)
    }
    assert set(backup.FOREIGN_KEYS_V7[name]) == {
        (
            tuple(e.parent.name for e in key.elements),
            key.elements[0].target_fullname.rsplit(".", 1)[0],
            tuple(e.target_fullname.rsplit(".", 1)[1] for e in key.elements),
        )
        for key in table.constraints
        if isinstance(key, ForeignKeyConstraint)
    }


def test_v6_source_retained_bytes_require_explicit_later_failure_check(retained):
    data = upgrade(retained)
    with pytest.raises(ValueError, match="numeric failure links must follow"):
        validate(data, failure_links_pending=False)
    with pytest.raises(ValueError, match="numeric failure links must follow"):
        validate(data, failure_links_pending=1)
    assert validate(data)
    assert set(data.reads) == set(data.files)


@pytest.mark.parametrize("schema", ["v10", "v11", "v12", "v14"])
def test_v6_does_not_relabel_other_research_closures(retained, schema):
    data = upgrade(retained)
    data.research["schema_version"] = "mineagent.research-restore-closure." + schema
    with pytest.raises(ValueError, match="research revision"):
        validate(data)
    assert not data.reads


def test_old_closure_cannot_acquire_failure_dependency(retained):
    with pytest.raises(ValueError, match="historical failure links unsupported"):
        validate(retained)


def test_v6_metadata_is_not_failure_validation_and_v5_stays_closed(retained):
    data = upgrade(retained)
    name = "factor_evaluation_outcomes"
    row = dict.fromkeys(backup.FIELDS_V7[name].split())
    row.update(
        id=str(UUID(int=9000)),
        owner_id=str(UUID(int=1)),
        generation=1,
        created_at="2026-10-26T00:00:00+00:00",
    )
    data.closure["tables"][name] = [row]
    assert backup._indexes(data.closure, 1000)[name]
    with pytest.raises(ValueError, match="numeric failure links must follow"):
        validate(data, failure_links_pending=False)
    old = copy.deepcopy(data.closure)
    old["schema_version"] = backup.SCHEMA_V5
    del old["tables"]["factor_numeric_failure_feedbacks"]
    with pytest.raises(ValueError, match="unsupported outcomes"):
        backup._indexes(old, 1000)


def test_v6_missing_membership_inventory_rejected(retained):
    data = upgrade(retained)
    del data.closure["tables"]["factor_numeric_failure_feedbacks"]
    with pytest.raises(ValueError, match="table inventory"):
        backup._indexes(data.closure, 1000)


def test_v6_orphan_failure_membership_cannot_bypass_scoped_references(retained):
    data = upgrade(retained)
    name = "factor_numeric_failure_feedbacks"
    row = {field: str(UUID(int=9001)) for field in backup.FIELDS_V7[name].split()}
    row.update(
        generation=1,
        created_at="2026-10-26T00:00:00+00:00",
        rejection_kind="rejection",
        stop_kind="stop",
        feedback_object_kind="factor_generation_feedback",
        rejection_sha256="a" * 64,
        stop_sha256="b" * 64,
        feedback_sha256="c" * 64,
    )
    data.closure["tables"][name] = [row]
    with pytest.raises(
        ValueError, match="foreign tuple factor_numeric_failure_feedbacks"
    ):
        validate(data)
    assert not data.reads


def test_v6_failure_membership_generation_is_not_coerced(retained):
    data = upgrade(retained)
    name = "factor_numeric_failure_feedbacks"
    row = dict.fromkeys(backup.FIELDS_V7[name].split())
    row.update(
        id=str(UUID(int=9000)),
        owner_id=str(UUID(int=1)),
        generation=True,
        created_at="2026-10-26T00:00:00+00:00",
    )
    data.closure["tables"][name] = [row]
    with pytest.raises(ValueError, match="generation"):
        backup._indexes(data.closure, 1000)

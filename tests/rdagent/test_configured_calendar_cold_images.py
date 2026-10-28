"""Real retained source/calendar and nonempty snapshot codecs; no PG/launch claim."""

import copy
import json
from datetime import UTC, datetime

import pytest
from test_rdagent_configured_backup import row
from test_rdagent_configured_source import retained  # noqa: F401
from test_rdagent_configured_source_service import setup_source  # noqa: F401

from backend.app import execution_snapshot_backup as execution
from backend.app import factor_analysis_backup as analysis
from backend.app.factor_analysis_calendar import cold_calendar
from tests.factors.test_execution_snapshot_retained_graph import retained_execution  # noqa: F401


@pytest.fixture
def nonempty_analysis(setup_source):  # noqa: F811
    s = setup_source
    s.plan.qlib_image_id = s.config.runtime.analysis_image
    s.plan.qlib_installed_parent_image_id = "sha256:" + "b" * 64
    s.plan.analysis_dependency_lock_sha256 = "c" * 64
    s.plan.created_at = datetime.now(UTC)
    tables = {name: [] for name in analysis.FIELDS}
    for name, model in [
        ("factor_analysis_plans", s.plan),
        ("dataset_versions", s.dataset),
    ]:
        tables[name] = [{key: row(model)[key] for key in analysis.FIELDS[name].split()}]
    return s, {"schema_version": analysis.SCHEMA, "tables": tables}


def test_actual_configured_calendar_rebuilds_from_retained_bytes(nonempty_analysis):
    s, closure = nonempty_analysis
    result = cold_calendar(
        s.config,
        {"owner_id": str(s.fence.owner_id), "run_id": str(s.fence.run_id)},
        closure,
        lambda path: json.loads((s.settings.storage_root / path).read_bytes()),
    )
    assert result == s.source.calendar


@pytest.mark.parametrize("field", ["qlib_image_id", "qlib_installed_parent_image_id"])
@pytest.mark.parametrize(
    "bad",
    [
        "a" * 64,
        "sha256:" + "A" * 64,
        "sha256:" + "a" * 63,
        "00000000-0000-0000-0000-000000000000",
        None,
        1,
    ],
)
def test_image_id_is_not_uuid_or_unchecked_string(nonempty_analysis, field, bad):
    _, closure = nonempty_analysis
    closure["tables"]["factor_analysis_plans"][0][field] = bad
    with pytest.raises(ValueError, match="image identity grammar"):
        analysis.indexes(closure, 1000)


@pytest.mark.parametrize(
    "field", ["id", "owner_id", "run_id", "spec_id", "label_source_dataset_id"]
)
def test_real_uuid_columns_remain_strict(nonempty_analysis, field):
    _, closure = nonempty_analysis
    closure["tables"]["factor_analysis_plans"][0][field] = "sha256:" + "a" * 64
    with pytest.raises(ValueError):
        analysis.indexes(closure, 1000)


@pytest.mark.parametrize("name", ["manifest.json", "preview.json", "sessions.json"])
def test_calendar_retained_byte_drift_still_rejected(nonempty_analysis, name):
    s, closure = nonempty_analysis

    def read(path):
        value = json.loads((s.settings.storage_root / path).read_bytes())
        if path.endswith("/" + name):
            return [] if isinstance(value, list) else dict(value, unexpected=True)
        return value

    with pytest.raises(ValueError):
        cold_calendar(
            s.config,
            {"owner_id": str(s.fence.owner_id), "run_id": str(s.fence.run_id)},
            closure,
            read,
        )


def test_complete_nonempty_snapshot_with_nonempty_analysis_inventory(
    retained_execution,  # noqa: F811
    nonempty_analysis,
    monkeypatch,
):
    """Real snapshot/normalizer codecs plus another run's real retained plan.

    This proves the whole execution closure accepts mixed inventory, while the
    configured calendar test above exercises the matching plan's source bytes.
    Neither is a real process/PG restore assertion.
    """
    closure, _, validate = retained_execution
    _, direct = nonempty_analysis
    original = execution.validate_closure

    def with_analysis(*args, **kwargs):
        if closure["schema_version"] == execution.SCHEMA_V2:
            kwargs["direct_analysis_closure"] = copy.deepcopy(direct)
        return original(*args, **kwargs)

    monkeypatch.setattr(execution, "validate_closure", with_analysis)
    assert validate()["factor_execution_snapshot_publications"]

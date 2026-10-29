"""Real native fence around fixed feedback readers; ordinary refs unchanged."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import research_workflow_input_reads as service
from backend.app.factor_native_io import (
    native_storage_guard,
    require_native_storage_guard,
)
from packages.research_inputs.contracts import InputVersionRef


@pytest.mark.parametrize(
    "historical,nested", [(False, False), (True, False), (False, True), (True, True)]
)
def test_feedback_fixed_reader_has_actual_retained_guard(
    tmp_path, monkeypatch, historical, nested
):
    (tmp_path / "factors").mkdir()
    settings = NS(storage_root=tmp_path)
    ref = InputVersionRef(
        origin="experiment_feedback_proposal",
        record_id=uuid4(),
        version_id=uuid4(),
        content_sha256="a" * 64,
    )

    def actual(*a):
        return require_native_storage_guard(tmp_path)

    monkeypatch.setattr(
        service,
        "verify_model_input_versions" if historical else "resolve_model_input_versions",
        actual,
    )
    reader = (
        service.verify_workflow_inputs
        if historical
        else service.resolve_workflow_inputs
    )
    if nested:
        with native_storage_guard(tmp_path) as fd:
            assert reader(None, uuid4(), (ref,), settings) == fd
    else:
        assert isinstance(reader(None, uuid4(), (ref,), settings), int)

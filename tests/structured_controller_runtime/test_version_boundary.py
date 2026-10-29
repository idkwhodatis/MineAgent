"""New JSON controller grammar never aliases024 Python runtime receipts."""

import pytest

from runtimes.openevolve_structured_host.process_journal import ControllerIntent
from tests.openevolve.operational_fixtures import records as python_records
from tests.structured_controller_runtime.fixtures import records


def test_024_python_admission_and_intent_are_not_027_json_admission(tmp_path):
    old = python_records(tmp_path)[0].intent
    with pytest.raises(ValueError):
        ControllerIntent.model_validate_json(old.model_dump_json())
    current = records(tmp_path)[0].intent
    assert current.admission.profile == "mineagent.oe-structured-orchestrator.v1"
    assert current.input_layout == "bootstrap_json_and_seed_json_only"
    assert current.admission.input_sha256 != old.admission.input_sha256

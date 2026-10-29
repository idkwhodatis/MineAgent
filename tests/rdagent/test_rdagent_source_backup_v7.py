"""Exact029 RD dispatch; central full physical and workflow closure still required."""
# ruff: noqa: F811

from uuid import uuid4

import pytest
from test_rdagent_source_backup import retained  # noqa: F401
from test_rdagent_source_backup_v6 import upgrade

from backend.app import rdagent_backup as backup


def validate(data, **kwargs):
    options = {
        "seed_context": {},
        "workflow_context": {},
        "configured_inputs_pending": True,
        "failure_links_pending": True,
    }
    options.update(kwargs)
    return backup.validate_closure(
        data.closure, data.research, data.factors, data.reader, 1000, **options
    )


def current(data):
    upgrade(data)
    data.closure["schema_version"] = backup.SCHEMA_V9
    data.research["schema_version"] = "mineagent.research-restore-closure.v14"
    return data


def test_v7_preserves_actual_old_source_bytes_and_failure_obligation(retained):
    data = current(retained)
    with pytest.raises(ValueError, match="numeric failure links"):
        validate(data, failure_links_pending=False)
    assert validate(data)
    assert set(data.reads) == set(data.files)


@pytest.mark.parametrize("context", [None, {str(uuid4()): object()}])
def test_v7_context_required_and_exact_even_without_workflow_children(
    retained, context
):
    with pytest.raises(ValueError, match="workflow.*context|context.*run"):
        validate(current(retained), workflow_context=context)
    assert not retained.reads


def test_v6_cannot_gain_workflow_context(retained):
    upgrade(retained)
    with pytest.raises(ValueError, match="Historical RD closure"):
        validate(retained)


def test_v7_does_not_alias_v11_research(retained):
    data = current(retained)
    data.research["schema_version"] = "mineagent.research-restore-closure.v13"
    with pytest.raises(ValueError, match="research revision"):
        validate(data)
    assert not data.reads

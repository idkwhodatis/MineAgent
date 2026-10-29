"""The new note grammar never grants installed or disclosure authority by itself."""

from types import SimpleNamespace

import pytest

from backend.app.normal_oe_note_capability import REVISION, require_normal_oe_notes
from backend.app.services import DomainError


@pytest.mark.parametrize(
    "revisions",
    [
        [],
        ["031_workflow_structured_stage"],
        [REVISION, REVISION],
        ["033_future"],
        [None],
    ],
)
def test_unknown_or_non_single_head_is_rejected_before_pin_query(revisions):
    calls = []

    def scalars(statement):
        calls.append(str(statement))
        return SimpleNamespace(all=lambda: revisions)

    with pytest.raises(DomainError) as error:
        require_normal_oe_notes(SimpleNamespace(scalars=scalars))
    assert error.value.code == "normal_oe_notes_not_installed"
    assert calls == ["SELECT version_num FROM alembic_version"]


def test_exact_installed_head_does_not_query_private_pins_or_release():
    calls = []

    def scalars(statement):
        calls.append(str(statement))
        return SimpleNamespace(all=lambda: [REVISION])

    assert require_normal_oe_notes(SimpleNamespace(scalars=scalars)) is None
    assert calls == ["SELECT version_num FROM alembic_version"]


@pytest.mark.parametrize("revision", ["031_workflow_structured_stage", REVISION])
def test_notes_head_inherits_exact_existing_workflow_and_snapshot_grammars(revision):
    from backend.app.rdagent_snapshot_config import (
        composed_controller_in_session,
        provider_config_in_session,
        workflow_config_in_session,
    )
    from backend.app.research_workflow_structured_public import (
        require_structured_workflow_schema,
    )

    session = SimpleNamespace(
        scalars=lambda statement: SimpleNamespace(all=lambda: [revision])
    )
    assert require_structured_workflow_schema(session) is None
    assert provider_config_in_session(session) is True
    assert workflow_config_in_session(session) is True
    assert composed_controller_in_session(session) is True


@pytest.mark.parametrize(
    "revisions",
    [
        [],
        ["033_future"],
        [REVISION, REVISION],
        ["031_workflow_structured_stage", REVISION],
    ],
)
def test_inheritance_never_accepts_unknown_or_multiple_heads(revisions):
    from backend.app.rdagent_snapshot_config import (
        composed_controller_in_session,
        provider_config_in_session,
        workflow_config_in_session,
    )
    from backend.app.research_workflow_structured_public import (
        require_structured_workflow_schema,
    )

    session = SimpleNamespace(
        scalars=lambda statement: SimpleNamespace(all=lambda: revisions)
    )
    with pytest.raises(DomainError):
        require_structured_workflow_schema(session)
    for reader in (
        provider_config_in_session,
        workflow_config_in_session,
        composed_controller_in_session,
    ):
        with pytest.raises(ValueError):
            reader(session)

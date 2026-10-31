"""Exact maintenance head inherits032, without accepting unknown installations."""

from types import SimpleNamespace

import pytest

from backend.app.normal_oe_note_capability import require_normal_oe_notes
from backend.app.schema_capabilities import capabilities_for
from backend.app.services import DomainError

OLD = "032_normal_oe_note_pins"
CURRENT = "032a_rd_terminal_results"


def test_maintenance_preserves_every_existing_capability():
    assert capabilities_for(CURRENT) == capabilities_for(OLD)
    assert (
        capabilities_for(CURRENT).research_input_schema
        == "mineagent.research-inputs.v1"
    )
    assert (
        capabilities_for(CURRENT).workflow_schema
        == "mineagent.research-workflow-closure.v2"
    )


@pytest.mark.parametrize("revision", [OLD, CURRENT])
def test_normal_note_gate_accepts_exact_known_maintenance(revision):
    session = SimpleNamespace(scalars=lambda _: SimpleNamespace(all=lambda: [revision]))
    assert require_normal_oe_notes(session) is None


@pytest.mark.parametrize(
    "revisions",
    [
        [],
        ["032b_future"],
        ["033_safe_workflow_branches"],
        [OLD, CURRENT],
        [CURRENT, CURRENT],
    ],
)
def test_normal_note_gate_rejects_unknown_and_multiple_heads(revisions):
    session = SimpleNamespace(scalars=lambda _: SimpleNamespace(all=lambda: revisions))
    with pytest.raises(DomainError):
        require_normal_oe_notes(session)

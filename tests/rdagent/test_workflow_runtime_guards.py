"""Explicit028 runtime successor checks; no claim of PG or native execution."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import factor_analysis_dispatch as analysis
from backend.app import rdagent_provider_quota as quota
from packages.research_runtime.process_contracts import ProcessError
from workers.research.rdagent_numeric import require_configured_revision


@pytest.mark.parametrize(
    "revision,allowed", [("029_research_workflow", True), ("029_unknown", False)]
)
def test_numeric_successor_is_exact_not_arbitrary_future(revision, allowed):
    session = NS(scalars=lambda q: NS(all=lambda: [revision]))
    if allowed:
        require_configured_revision(session)
    else:
        with pytest.raises(ProcessError):
            require_configured_revision(session)


def test_workflow_sdk_holds_are_not_silently_omitted():
    called = []
    session = NS(
        scalars=lambda q: NS(all=lambda: ["029_research_workflow"]),
        execute=lambda q: called.append(q) or NS(all=list),
    )
    assert quota.provider_process_holds(session) == ()
    assert len(called) == 1


@pytest.mark.parametrize("present", [False, True])
def test_workflow_analysis_requires_actual_same_owner_run_plan(monkeypatch, present):
    monkeypatch.setattr(analysis, "require_neutral_transaction", lambda *a, **kw: None)
    fence = NS(owner_id=uuid4(), run_id=uuid4())
    queries = []
    session = NS(
        scalars=lambda q: NS(all=lambda: ["029_research_workflow"]),
        scalar=lambda q: queries.append(q) or (uuid4() if present else None),
    )
    assert analysis.configured_analysis_run(session, fence) is present
    assert set(queries[0].compile().params.values()) == {fence.owner_id, fence.run_id}

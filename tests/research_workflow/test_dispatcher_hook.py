"""Dispatcher hook only: no new scheduler or synthetic ResearchRun."""

from types import SimpleNamespace as NS

import pytest

from workers.research.__main__ import dispatch_workflow_tick


@pytest.mark.parametrize(
    "revisions",
    [
        [],
        ["026_rd_seeded_submissions"],
        ["028_oe_structured_search"],
        ["029_future"],
        ["029_research_workflow", "028_oe_structured_search"],
        ["029_research_workflow"],
    ],
)
def test_exact_successor_only_and_no_outer_transaction(monkeypatch, revisions):
    from workers.research import workflow_tick

    entered = []
    calls = []

    class Session:
        def __enter__(self):
            entered.append(True)
            return self

        def __exit__(self, *args):
            entered.pop()

        def scalars(self, statement):
            assert str(statement) == "SELECT version_num FROM alembic_version"
            return NS(all=lambda: revisions)

    def actual_tick(factory, settings, *, limit):
        assert not entered
        assert limit == 4
        calls.append((factory, settings))
        return ("observed",)

    monkeypatch.setattr(workflow_tick, "tick_workflows", actual_tick)
    settings = NS()
    result = dispatch_workflow_tick(Session, settings)
    if revisions == ["029_research_workflow"]:
        assert calls == [(Session, settings)] and result == ("observed",)
    else:
        assert not calls and result == ()

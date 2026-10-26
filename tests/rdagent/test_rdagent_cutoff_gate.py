"""Disabled cutoff transition tests; no database/process acceptance claim."""

from types import SimpleNamespace

import pytest

from backend.app import rdagent_cutoff_services as service
from packages.research_runtime.process_contracts import ProcessError


class NoWritesSession:
    def __getattr__(self, name):
        raise AssertionError(f"Unexpected database access: {name}")


def test_candidate_declaration_never_issues_process_or_writes(monkeypatch):
    session, fence = NoWritesSession(), object()
    seen = []
    monkeypatch.setattr(
        service,
        "require_neutral_transaction",
        lambda *args, **kwargs: seen.append((args, kwargs)),
    )
    with pytest.raises(ProcessError, match="process_v2_not_activated"):
        service.admit_candidate_cutoff(
            session, fence, SimpleNamespace(declaration=object())
        )
    assert seen == [((session, fence), {"resource_scope": "processes"})]


def test_cutoff_requires_current_process_transaction_before_declaration(monkeypatch):
    def reject(*args, **kwargs):
        raise RuntimeError("no current process scope")

    monkeypatch.setattr(service, "require_neutral_transaction", reject)
    # No declaration attribute: rejection must precede even reading the proposal.
    with pytest.raises(RuntimeError, match="no current process scope"):
        service.admit_candidate_cutoff(NoWritesSession(), object(), object())

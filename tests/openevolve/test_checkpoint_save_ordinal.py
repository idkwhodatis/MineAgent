"""Real operational adapter sequencing with inert transport; not host admission."""

from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from backend.app import openevolve_models as m
from backend.app.openevolve_services import prepare_checkpoint
from backend.app.services import DomainError
from packages.openevolve_adapter.host_contracts import CheckpointIntentCommand
from packages.openevolve_adapter.observer import SearchControlAbort
from runtimes.openevolve_operational import transport


def test_each_actual_save_has_distinct_ordinal_even_same_final_cursor(monkeypatch):
    monkeypatch.setattr(transport, "_SAVE_ORDINAL", 0)
    monkeypatch.setattr(transport, "_bootstrap", lambda: NS(remaining=lambda now: 10))
    observed = []
    monkeypatch.setattr(
        transport, "_send", lambda kind, payload, **kw: observed.append(payload)
    )
    for frontier in [0, 1, 2, 2]:
        transport.host_event(
            "checkpoint_prepare", {"frontier": frontier, "state_sha256": "a" * 64}
        )
    assert [p["save_ordinal"] for p in observed] == [1, 2, 3, 4]
    assert observed[-1] != observed[-2]
    with pytest.raises(SearchControlAbort):
        transport.host_event(
            "checkpoint_prepare", {"frontier": 2, "state_sha256": "a" * 64}
        )
    assert len(observed) == 4


@pytest.mark.parametrize(
    "existing_ordinals,new_ordinal", [([], 2), ([1], 1), ([1, 3], 4)]
)
def test_new_save_cannot_reuse_or_skip_ordinal(existing_ordinals, new_ordinal):
    session = Mock()
    session.scalar.return_value = None
    session.scalars.return_value.all.return_value = [
        NS(save_ordinal=n) for n in existing_ordinals
    ]
    command = CheckpointIntentCommand(
        epoch_id=uuid4(),
        terminal_id=uuid4(),
        frontier=0,
        state_sha256="a" * 64,
        request_sha256="b" * 64,
        save_ordinal=new_ordinal,
    )
    with (
        patch("backend.app.openevolve_services._locked_epoch"),
        pytest.raises(DomainError),
    ):
        prepare_checkpoint(session, NS(), command)
    session.add.assert_not_called()


def test_exact_replay_includes_save_ordinal():
    session = Mock()
    command = CheckpointIntentCommand(
        epoch_id=uuid4(),
        terminal_id=uuid4(),
        frontier=2,
        state_sha256="a" * 64,
        request_sha256="b" * 64,
        save_ordinal=3,
    )
    existing = m.SearchCheckpointIntent(**command.model_dump())
    session.scalar.return_value = existing
    with patch("backend.app.openevolve_services._locked_epoch"):
        assert prepare_checkpoint(session, NS(), command) is existing
        with pytest.raises(DomainError):
            prepare_checkpoint(
                session, NS(), command.model_copy(update={"save_ordinal": 4})
            )
    session.add.assert_not_called()

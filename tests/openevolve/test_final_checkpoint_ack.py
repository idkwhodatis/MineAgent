"""Fresh durable final-ACK predicate with explicit session doubles, not PG."""

from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import openevolve_checkpoint_publication as publication


def setup(monkeypatch, ordinal):
    scope = {
        "owner_id": uuid4(),
        "run_id": uuid4(),
        "attempt_id": uuid4(),
        "generation": 1,
    }
    epoch = NS(id=uuid4())
    inventory = NS(id=uuid4(), sha256="a" * 64)
    pairs = []
    for n in range(1, ordinal + 1):
        intent = NS(
            **scope,
            id=uuid4(),
            epoch_id=epoch.id,
            save_ordinal=n,
            frontier=min(n - 1, 2),
            state_sha256="b" * 64,
        )
        saved = NS(
            **scope,
            id=uuid4(),
            epoch_id=epoch.id,
            intent_id=intent.id,
            frontier=intent.frontier,
            state_sha256=intent.state_sha256,
            inventory_object_id=inventory.id,
            inventory_sha256=inventory.sha256,
        )
        pairs.append((intent, saved))
    session = NS(execute=lambda _: NS(all=lambda: pairs))

    @contextmanager
    def transaction(*a, **k):
        yield session

    monkeypatch.setattr(publication, "neutral_transaction", transaction)
    monkeypatch.setattr(
        publication, "_resolve", lambda *a: (epoch, pairs[-1][0], inventory)
    )
    monkeypatch.setattr(publication, "_owned_row", lambda *a: pairs[-1][1])
    monkeypatch.setattr(publication, "_scope", lambda _: scope)
    event = NS(payload={"token": str(pairs[-1][0].id)})

    def check():
        return publication.final_checkpoint_committed(
            None, None, None, event=event, receipt_id=pairs[-1][1].id, frontier=2
        )

    return check, pairs


def test_last_terminal_is_not_final_and_all_four_actual_saves_are_required(monkeypatch):
    check, _ = setup(monkeypatch, 3)
    assert check() is False
    check, pairs = setup(monkeypatch, 4)
    assert check() is True
    pairs.pop(0)
    with pytest.raises(ValueError, match="every actual published save"):
        check()

    check, _ = setup(monkeypatch, 4)
    transaction = publication.neutral_transaction

    @contextmanager
    def refused_commit(*args, **kwargs):
        with transaction(*args, **kwargs) as session:
            yield session
            raise ValueError("deferred final-read transaction rejected")

    monkeypatch.setattr(publication, "neutral_transaction", refused_commit)
    with pytest.raises(ValueError, match="deferred final-read transaction rejected"):
        check()


def test_final_save_rejects_same_owner_epoch_and_prefix_drift(monkeypatch):
    check, pairs = setup(monkeypatch, 4)
    for attribute, value in (("epoch_id", uuid4()), ("generation", 2)):
        original = getattr(pairs[0][1], attribute)
        setattr(pairs[0][1], attribute, value)
        with pytest.raises(ValueError, match="prefix drift"):
            check()
        setattr(pairs[0][1], attribute, original)
    pairs[0][0].save_ordinal = 2
    with pytest.raises(ValueError, match="prefix drift"):
        check()
    pairs[0][0].save_ordinal = 1
    pairs[-1][1].inventory_sha256 = "c" * 64
    with pytest.raises(ValueError, match="publication identity drift"):
        check()

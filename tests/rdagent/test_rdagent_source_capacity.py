from types import SimpleNamespace
from uuid import UUID

import pytest

from backend.app.rdagent_source_capacity import (
    pending_source_usage,
    pending_source_usage_for_schema,
)
from backend.app.services import DomainError


class Session:
    def __init__(self, rows, revision="018_rd_source_persistence"):
        self.rows = rows
        self.revision = revision

    def scalars(self, query):
        rows = (
            [self.revision]
            if str(query).startswith("SELECT version_num")
            else self.rows
        )
        return SimpleNamespace(all=lambda: rows)


def test_unfinished_source_peak_remains_global_and_owner_scoped():
    rows = [
        SimpleNamespace(owner_id=UUID(int=1), peak_bytes=100),
        SimpleNamespace(owner_id=UUID(int=2), peak_bytes=200),
    ]
    assert pending_source_usage(Session(rows), UUID(int=1)) == (300, 100, 10)
    assert pending_source_usage_for_schema(Session(rows), UUID(int=1)) == (300, 100, 10)


@pytest.mark.parametrize("peak", [True, -1, 0])
def test_invalid_pending_byte_accounting_fails_closed(peak):
    with pytest.raises(DomainError):
        pending_source_usage(
            Session([SimpleNamespace(owner_id=UUID(int=1), peak_bytes=peak)]),
            UUID(int=1),
        )


def test_only_exact_historical_revision_has_zero_source_obligation():
    assert pending_source_usage_for_schema(
        Session([], "015_neutral_primitives"), UUID(int=1)
    ) == (0, 0, 0)
    with pytest.raises(ValueError):
        pending_source_usage_for_schema(Session([], "unknown"), UUID(int=1))

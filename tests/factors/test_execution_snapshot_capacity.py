"""Accounting doubles only; real fenced PostgreSQL arbitration is separate."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.execution_snapshot_capacity import pending_snapshot_usage_for_schema
from backend.app.services import DomainError


class Session:
    def __init__(self, revision, rows):
        self.revision = revision
        self.rows = rows
        self.queries = []

    def scalars(self, statement):
        sql = str(statement)
        self.queries.append(sql)
        return SimpleNamespace(
            all=lambda: (
                [self.revision]
                if sql == "SELECT version_num FROM alembic_version"
                else self.rows
            )
        )


@pytest.mark.parametrize(
    "revision",
    [
        "015_neutral_primitives",
        "016_process_versioning",
        "017_numeric_process_boundary",
        "018_rd_source_persistence",
        "019_native_hdf_storage",
    ],
)
def test_historical_revision_does_not_query_future_snapshot_tables(revision):
    session = Session(revision, [])
    assert pending_snapshot_usage_for_schema(session, uuid4()) == (0, 0, 0)
    assert len(session.queries) == 1


def test_new_snapshot_pending_peak_counts_all_owners_and_lost_attempts():
    owner = uuid4()
    other = uuid4()
    rows = [
        SimpleNamespace(owner_id=owner, peak_bytes=100, peak_entries=11),
        SimpleNamespace(owner_id=other, peak_bytes=200, peak_entries=13),
    ]
    session = Session("021_execution_snapshots", rows)
    assert pending_snapshot_usage_for_schema(session, owner) == (300, 100, 24)
    assert (
        "factor_execution_snapshot_outcomes.publication_id IS NULL"
        in session.queries[1]
    )
    assert "owner_id =" not in session.queries[1]


def test_unknown_revision_never_means_zero_pending_bytes():
    with pytest.raises(ValueError):
        pending_snapshot_usage_for_schema(Session("future", []), uuid4())


def test_snapshot_pending_inventory_bound_fails_closed():
    rows = [SimpleNamespace(owner_id=uuid4(), peak_bytes=1, peak_entries=1)] * 10001
    with pytest.raises(DomainError):
        pending_snapshot_usage_for_schema(
            Session("021_execution_snapshots", rows), uuid4()
        )


@pytest.mark.parametrize(
    "peak,entries", [(-1, 11), (True, 11), (1, 0), (1, True), (1, 257)]
)
def test_pending_peak_never_credits_malformed_or_negative_obligations(peak, entries):
    row = SimpleNamespace(owner_id=uuid4(), peak_bytes=peak, peak_entries=entries)
    with pytest.raises(DomainError):
        pending_snapshot_usage_for_schema(
            Session("021_execution_snapshots", [row]), uuid4()
        )

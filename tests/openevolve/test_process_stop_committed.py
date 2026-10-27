"""Persisted typed stop barrier doubles; no new physical containment authority."""

from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import UUID

import pytest

from backend.app.openevolve_process_stop import stop_committed
from tests.openevolve.test_process_backup import fixture


def setup(tmp_path, profile):
    raw, indexes, _, _ = fixture(tmp_path, profile)

    def convert(row):
        return NS(
            **{
                k: UUID(v)
                if k
                in {
                    "id",
                    "admission_id",
                    "owner_id",
                    "run_id",
                    "attempt_id",
                    "logical_operation_id",
                    "reservation_id",
                }
                else v
                for k, v in row.items()
            }
        )

    row = convert(raw)
    session = Mock()
    session.get.return_value = convert(indexes["research_process_states"][raw["id"]])
    receipts = [convert(r) for r in indexes["research_process_receipts"].values()]
    session.scalars.return_value.all.return_value = receipts
    return row, session, receipts


@pytest.mark.parametrize("profile", ["controller", "native"])
def test_exact_committed_oe_unit_stopped(tmp_path, profile):
    row, session, _ = setup(tmp_path, profile)
    assert stop_committed(session, row)


@pytest.mark.parametrize("profile", ["controller", "native"])
@pytest.mark.parametrize("fault", ["state", "digest", "missing", "scope"])
def test_incomplete_or_substituted_unit_never_reconciles(tmp_path, profile, fault):
    row, session, receipts = setup(tmp_path, profile)
    if fault == "state":
        session.get.return_value.status = "uncertain"
    elif fault == "digest":
        receipts[-1].payload_sha256 = "a" * 64
    elif fault == "missing":
        receipts.pop()
    else:
        receipts[-1].generation += 1
    assert not stop_committed(session, row)

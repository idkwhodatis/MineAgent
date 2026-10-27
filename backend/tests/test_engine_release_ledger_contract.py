"""Existing ledger semantics for the planned engine release; not route/PG acceptance.

Only persistence and advisory locking are inert. These tests intentionally reuse
record_exposure/classify_exposures, and introduce no second ledger or release API.
"""

from copy import deepcopy
from datetime import date
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import experiment_services as ledger
from backend.app.services import DomainError
from packages.factors.contracts import DateWindow


@pytest.fixture
def admission(monkeypatch):
    identities = [{"market": "US", "symbol": "AAA"}]
    monkeypatch.setattr(ledger, "advisory_lock", lambda *args: None)
    monkeypatch.setattr(ledger, "_identities", lambda *args: identities)

    class Persistence:
        row = None
        additions = 0

        def scalar(self, statement):
            return self.row

        def add(self, row):
            self.row = row
            self.additions += 1

        def flush(self):
            pass

    owner = uuid4()
    run = NS(id=uuid4(), owner_id=owner, experiment_id=None, split_version_id=None)
    dataset = NS(id=uuid4(), owner_id=owner)
    resource = uuid4()
    args = {
        "owner_id": owner,
        "dataset": dataset,
        "run": run,
        "window": DateWindow(start=date(2024, 1, 1), end=date(2024, 3, 1)),
        "purpose": "engine_direct_analysis_release",
        "source_key": f"engine-result:direct_analysis:{resource}:label:v1",
        "provenance": {
            "run_id": str(run.id),
            "analysis_id": str(resource),
            "snapshot_id": str(uuid4()),
            "result_sha256": "a" * 64,
            "extent": "conservative_full_retained_dataset",
        },
    }
    return Persistence(), args, identities


def test_lost_reply_retry_reuses_exact_existing_ledger_row(admission):
    session, args, _ = admission
    first = ledger.record_exposure(session, **args)
    assert ledger.record_exposure(session, **args) is first
    assert session.additions == 1


@pytest.mark.parametrize("change", ["run", "hash", "window", "dataset", "purpose"])
def test_same_release_identity_cannot_change_evidence(admission, change):
    session, args, _ = admission
    ledger.record_exposure(session, **args)
    changed = deepcopy(args)
    if change == "run":
        changed["run"].id = uuid4()
        # Existing helper does not compare run_id itself: bind it in provenance.
        changed["provenance"]["run_id"] = str(changed["run"].id)
    elif change == "hash":
        changed["provenance"]["result_sha256"] = "b" * 64
    elif change == "window":
        changed["window"] = DateWindow(start=date(2024, 1, 1), end=date(2024, 2, 1))
    elif change == "dataset":
        changed["dataset"].id = uuid4()
    else:
        changed["purpose"] = "engine_execution_snapshot_release"
    with pytest.raises(DomainError) as caught:
        ledger.record_exposure(session, **changed)
    assert caught.value.code == "exposure_conflict"
    assert session.additions == 1


@pytest.mark.parametrize("resource", ["dataset", "run"])
def test_other_owner_cannot_record_release(admission, resource):
    session, args, _ = admission
    args[resource].owner_id = uuid4()
    with pytest.raises(DomainError) as caught:
        ledger.record_exposure(session, **args)
    assert caught.value.code == "not_found"
    assert session.additions == 0


def test_full_extent_includes_future_label_holdout_without_inventing_unlock(admission):
    session, args, identities = admission
    exposure = ledger.record_exposure(session, **args)
    # Factor computation stops Feb 1, but retained future labels continue to Mar 1.
    future_holdout = DateWindow(start=date(2024, 2, 1), end=date(2024, 3, 1))
    result = ledger.classify_exposures(identities, future_holdout, [exposure])
    assert result["classification"] == "previously_exposed"
    assert result["matching_count"] == 1
    assert result["matching_exposure_ids"] == [str(exposure.id)]
    nonoverlap = DateWindow(start=date(2024, 3, 1), end=date(2024, 4, 1))
    assert (
        ledger.classify_exposures(identities, nonoverlap, [exposure])["classification"]
        == "no_tracked_overlap"
    )

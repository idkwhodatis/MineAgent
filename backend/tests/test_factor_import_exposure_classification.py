"""Unknown imported history cannot be represented as no tracked overlap."""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from backend.app import experiment_services as services
from backend.app.services import DomainError

IDENTITIES = [{"listing_id": "frozen-listing", "market": "US", "symbol": "AAA"}]
WINDOW = SimpleNamespace(start=date(2024, 1, 1), end=date(2024, 2, 1))
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_legacy_nonimport_output_remains_byte_identical():
    old = services.classify_exposures(IDENTITIES, WINDOW, [], as_of=NOW)
    explicit = services.classify_exposures(
        IDENTITIES, WINDOW, [], as_of=NOW, unknown_import_history=False
    )
    assert old == explicit
    assert old["classification"] == "no_tracked_overlap"
    assert old["schema_version"] == "tradegrad.exposure.v1"
    assert old["independent_unseen_holdout_established"] is False


def test_unknown_import_does_not_invent_exposure_rows_or_override_actual_overlap():
    unknown = services.classify_exposures(
        IDENTITIES, WINDOW, [], as_of=NOW, unknown_import_history=True
    )
    assert unknown["classification"] == "unknown_import_history"
    assert unknown["matching_exposure_ids"] == [] and unknown["matching_count"] == 0
    evidence = SimpleNamespace(
        id=uuid4(),
        start_date=WINDOW.start,
        end_date=WINDOW.end,
        identities=IDENTITIES,
        purpose="imported_factor_research_exposure",
    )
    tracked = services.classify_exposures(
        IDENTITIES, WINDOW, [evidence], as_of=NOW, unknown_import_history=True
    )
    assert tracked["classification"] == "previously_exposed"
    assert tracked["matching_exposure_ids"] == [str(evidence.id)]
    assert (
        services.classify_exposures(
            [], WINDOW, [], as_of=NOW, unknown_import_history=True
        )["classification"]
        == "unknown_identity"
    )


@pytest.mark.parametrize(
    "revision", ["001_m1", "011_note_pin_trigger", "012_factor_library"]
)
def test_preimport_revision_never_queries_absent_import_tables(revision):
    session = Mock()
    session.scalar.return_value = revision
    assert services.has_unknown_import_history(session, uuid4()) is False
    session.execute.assert_not_called()
    assert session.scalar.call_count == 1


def test_declared_import_schema_missing_tables_fails_closed():
    session = Mock()
    session.scalar.return_value = "013_factor_import"
    session.execute.return_value.one.return_value = (None, None)
    with pytest.raises(DomainError, match="Import history metadata"):
        services.has_unknown_import_history(session, uuid4())


def test_get_exposure_snapshot_applies_owner_unknown_history_only_to_no_overlap(
    monkeypatch,
):
    owner = uuid4()
    split = SimpleNamespace(owner_id=owner)
    session = Mock()
    session.scalars.return_value.all.return_value = []
    monkeypatch.setattr(
        services,
        "split_definition",
        lambda _: SimpleNamespace(windows=SimpleNamespace(holdout=WINDOW)),
    )
    monkeypatch.setattr(
        services, "validate_split_definition", lambda *_: (object(), object())
    )
    monkeypatch.setattr(services, "_identities", lambda *_: IDENTITIES)
    monkeypatch.setattr(
        services, "has_unknown_import_history", lambda _, owner_id: owner_id == owner
    )
    result = services.get_exposure_snapshot(session, owner, split)
    assert result["classification"] == "unknown_import_history"
    assert result["matching_exposure_ids"] == []

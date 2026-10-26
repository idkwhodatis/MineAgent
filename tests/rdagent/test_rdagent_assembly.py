from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from packages.factors.fixtures import demo_fixture, demo_inputs
from packages.rdagent_adapter.assembly import CutoffRowAssembly, assemble_cutoff_rows
from packages.rdagent_adapter.prefix import project_prefix


def fixture():
    snapshot = demo_fixture(
        factor_version_id=UUID(int=1), dataset_version_id=UUID(int=2)
    ).manifest.input
    calendar, _ = demo_inputs()
    event = calendar.sessions[3]
    projection, _ = project_prefix(
        snapshot,
        host_cutoff=event.close_time,
        output_event_time=event.close_time,
        output_session=event.session_date,
        calendar_sessions=tuple(s.session_date for s in calendar.sessions),
        observations=(),
        candidate_image="sha256:" + "a" * 64,
        normalizer_image="sha256:" + "b" * 64,
        lag_available_at=event.close_time + timedelta(days=1),
    )
    ns = (
        (event.close_time - datetime(1970, 1, 1, tzinfo=UTC))
        // timedelta(microseconds=1)
    ) * 1000
    return snapshot, projection, ns


def assemble(snapshot, projection, rows):
    return assemble_cutoff_rows(
        snapshot,
        projection,
        rows,
        hdf_sha256="a" * 64,
        normalized_values_sha256="b" * 64,
    )


def test_host_assembly_retains_universe_and_host_lag_readiness():
    snapshot, projection, ns = fixture()
    rows, receipt = assemble(snapshot, projection, ((ns, 0, 1.5, 0), (ns, 1, None, 1)))
    assert len(rows) == 3 and receipt.valid_count == 1
    assert rows[1].missing_reason == "candidate_reported_missing"
    assert rows[2].missing_reason == "candidate_omitted_output"
    assert all(row.available_at == projection.lag_available_at for row in rows)
    assert receipt.dependency_claim == "provided_not_proven_read"
    assert CutoffRowAssembly.model_validate_json(receipt.model_dump_json()) == receipt


@pytest.mark.parametrize(
    "change", ["future", "duplicate", "unknown", "bool", "missing_numeric", "nan"]
)
def test_assembly_revalidates_normalized_primitives(change):
    snapshot, projection, ns = fixture()
    bad = {
        "future": ((ns + 1, 0, 1.0, 0),),
        "duplicate": ((ns, 0, 1.0, 0), (ns, 0, 2.0, 0)),
        "unknown": ((ns, 99, 1.0, 0),),
        "bool": ((ns, 0, True, 0),),
        "missing_numeric": ((ns, 0, 1.0, 1),),
        "nan": ((ns, 0, float("nan"), 0),),
    }[change]
    with pytest.raises(ValueError):
        assemble(snapshot, projection, bad)


def test_projection_cannot_claim_another_snapshot():
    snapshot, projection, ns = fixture()
    changed = projection.model_copy(update={"snapshot_input_sha256": "0" * 64})
    with pytest.raises(ValueError):
        assemble(snapshot, changed, ((ns, 0, 1.0, 0),))

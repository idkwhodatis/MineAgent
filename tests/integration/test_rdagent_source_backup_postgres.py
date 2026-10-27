"""Real PG snapshot + retained-byte RD validation, not whole-backup stop proof.

The fixture controller is only admitted; full shared process backup must continue
rejecting it. This test exercises RD's own source closure with actual SQL rows and
files; no fake stopped controller or bypass of the shared backup gate is used.
"""

import copy

# pytest fixture import is deliberately re-used as a test parameter.
# ruff: noqa: F811
import pytest
from test_rd_source_persistence import persist, source_setup  # noqa: F401

from backend.app import factor_backup, rdagent_backup, research_backup
from backend.app.storage import read_verified
from tests.integration.rd_source_related_rows import read_admitted_source_rows

pytestmark = pytest.mark.postgres
REVISION = "018_rd_source_persistence"


def snapshots(setup):
    factory, _, _, settings = setup
    saved = persist(setup)
    with factory.kw["bind"].connect() as connection:
        rd = rdagent_backup.read_closure(connection, 10000, revision=REVISION)
        with pytest.raises(
            ValueError, match="Active runs cannot enter neutral cold closure"
        ):
            research_backup.read_closure(connection, 10000, revision=REVISION)
        research = read_admitted_source_rows(connection, saved.fence)
        with pytest.raises(
            ValueError, match="Active runs cannot enter neutral cold closure"
        ):
            research_backup._indexes(research, 10000)
        factors = factor_backup.read_closure(connection, 10000, revision=REVISION)

    def read_bytes(key, sha, maximum):
        return read_verified(settings.storage_root, key, sha, maximum)

    return saved, rd, research, factors, read_bytes


def test_real_rd_source_snapshot_reads_and_reparses_original_files(source_setup):
    saved, rd, research, factors, reader = snapshots(source_setup)
    assert rdagent_backup.validate_closure(rd, research, factors, reader, 10000)
    assert rd["tables"]["factor_generation_sources"][0]["id"] == str(saved.source.id)
    assert len(rd["tables"]) == 24
    assert not rd["tables"]["factor_cutoff_outcomes"]
    # This is explicitly not whole-backup acceptance of the admitted precursor.
    assert any(
        r["status"] == "admitted" for r in research["tables"]["research_process_states"]
    )


@pytest.mark.parametrize(
    "case", ["witness", "source_owner", "consumer", "response_hash"]
)
def test_real_rd_snapshot_metadata_drift_rejected(source_setup, case):
    _, rd, research, factors, reader = snapshots(source_setup)
    rd = copy.deepcopy(rd)
    if case == "witness":
        rd["tables"]["factor_source_read_witnesses"][0]["witness_sha256"] = "0" * 64
    elif case == "source_owner":
        rd["tables"]["factor_generation_sources"][0]["owner_id"] = (
            "00000000-0000-0000-0000-000000000000"
        )
    elif case == "consumer":
        rd["consumer_claims"].pop()
    else:
        rd["tables"]["factor_source_read_parts"][0]["response_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        rdagent_backup.validate_closure(rd, research, factors, reader, 10000)


def test_real_rd_snapshot_row_and_byte_caps(source_setup):
    factory, _, _, _ = source_setup
    persist(source_setup)
    with factory.kw["bind"].connect() as connection:
        with pytest.raises(
            ValueError, match="Active runs cannot enter neutral cold closure"
        ):
            research_backup.read_closure(connection, 10000, revision=REVISION)
        with pytest.raises(ValueError):
            rdagent_backup.read_closure(connection, 1, revision=REVISION)
        with pytest.raises(ValueError):
            rdagent_backup.read_closure(
                connection, 10000, revision=REVISION, maximum_bytes=100
            )

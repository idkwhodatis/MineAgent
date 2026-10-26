"""Real PG snapshot + retained-byte RD validation, not whole-backup stop proof.

The fixture controller is only admitted; full shared process backup must continue
rejecting it. This test exercises RD's own source closure with actual SQL rows and
files; no fake stopped controller or bypass of the shared backup gate is used.
The RD subvalidator receives actual active-run references solely to test source
retention; this does not satisfy its production caller's independently checked
shared-closure prerequisite. Full production cold restore remains separately
required by test_neutral_restore and the completed-evidence acceptance gate.
"""

import copy

# pytest fixture import is deliberately re-used as a test parameter.
# ruff: noqa: F811
import pytest
from sqlalchemy import text
from test_rd_source_persistence import persist, source_setup  # noqa: F401

from backend.app import factor_backup, rdagent_backup, research_backup
from backend.app.storage import read_verified

pytestmark = pytest.mark.postgres
REVISION = "018_rd_source_persistence"


def active_source_references(connection, maximum_rows=10000, maximum_bytes=32_000_000):
    """Test-only SQL references, NOT a validated or restorable research closure.

    The source fixture deliberately has an active run/admitted controller. Read
    its actual immutable references without forging terminal/stop evidence or
    changing the production cold-closure reader's admission rules.
    """
    count = size = 0

    def rows(query):
        nonlocal count, size
        stream = connection.execute(
            text(query).execution_options(stream_results=True, yield_per=1),
            {"limit": maximum_rows + 1},
        ).scalars()
        result = []
        try:
            for row in stream:
                count += 1
                size += len(research_backup.canonical(row))
                if count > maximum_rows or size > maximum_bytes:
                    raise ValueError("Active source reference snapshot exceeds bounds")
                result.append(row)
        finally:
            stream.close()
        return result

    return {
        "schema_version": research_backup.SCHEMA_V4,
        "tables": {
            name: rows(
                f"SELECT to_jsonb(t) FROM {name} t ORDER BY {research_backup.PRIMARY_KEYS.get(name, 'id')} LIMIT :limit"
            )
            for name in research_backup.FIELDS_V4
        },
        "external_bindings": {
            "runs": rows(
                "SELECT to_jsonb(t) FROM (SELECT id,owner_id,generation,purpose,engine,status FROM research_runs ORDER BY id) t LIMIT :limit"
            ),
            "attempts": rows(
                "SELECT to_jsonb(t) FROM (SELECT a.id,r.owner_id,a.run_id,a.generation,a.status,a.sandbox_stopped,a.finished_at FROM execution_attempts a JOIN research_runs r ON r.id=a.run_id ORDER BY a.id) t LIMIT :limit"
            ),
        },
    }


def snapshots(setup):
    factory, _, _, settings = setup
    saved = persist(setup)
    with factory.kw["bind"].connect() as connection:
        rd = rdagent_backup.read_closure(connection, 10000, revision=REVISION)
        with pytest.raises(
            ValueError, match="Active runs cannot enter neutral cold closure"
        ):
            research_backup.read_closure(connection, 10000, revision=REVISION)
        research = active_source_references(connection)
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
        with pytest.raises(ValueError, match="snapshot exceeds bounds"):
            active_source_references(connection, maximum_rows=1)
        with pytest.raises(ValueError, match="snapshot exceeds bounds"):
            active_source_references(connection, maximum_bytes=100)
        with pytest.raises(ValueError):
            rdagent_backup.read_closure(connection, 1, revision=REVISION)
        with pytest.raises(ValueError):
            rdagent_backup.read_closure(
                connection, 10000, revision=REVISION, maximum_bytes=100
            )

"""Finite ORM membership construction only, not shared PostgreSQL acceptance."""

from sqlalchemy import CheckConstraint, UniqueConstraint

from backend.app.oe_structured_checkpoint_models import (
    OEStructuredCheckpointChunk,
    OEStructuredCheckpointCompletion,
    OEStructuredCheckpointIntent,
    OEStructuredCheckpointPublication,
    OEStructuredCheckpointUpload,
)
from backend.app.oe_structured_models import SCOPE


def test_repeated_native_save_cursor_has_distinct_bounded_ordinal():
    table = OEStructuredCheckpointIntent.__table__
    unique = {
        tuple(x.columns.keys())
        for x in table.constraints
        if isinstance(x, UniqueConstraint)
    }
    assert ("epoch_id", "save_ordinal") in unique
    assert ("control_state_id",) not in unique
    assert "save_ordinal BETWEEN 1 AND 4" in " ".join(
        str(x.sqltext) for x in table.constraints if isinstance(x, CheckConstraint)
    )


def test_complete_transfer_graph_is_scoped_and_no_raw_checkpoint_db_bytes():
    for cls in (
        OEStructuredCheckpointUpload,
        OEStructuredCheckpointChunk,
        OEStructuredCheckpointCompletion,
        OEStructuredCheckpointPublication,
    ):
        table = cls.__table__
        assert set(SCOPE) <= set(table.columns.keys())
        assert not {"data_base64", "file_bytes", "program_code"} & set(
            table.columns.keys()
        )
        assert any(
            tuple(x.column_keys) == (*SCOPE, "epoch_id")
            for x in table.foreign_key_constraints
        )
    chunk = OEStructuredCheckpointChunk.__table__
    assert any(
        tuple(x.column_keys)
        == ("owner_id", "object_id", "object_kind", "object_sha256")
        for x in chunk.foreign_key_constraints
    )
    assert any(
        tuple(x.columns.keys()) == ("upload_id", "sequence")
        for x in chunk.constraints
        if isinstance(x, UniqueConstraint)
    )


def test_fixed_offset_and_counts_match_actual_transfer_abi():
    from runtimes.openevolve_structured.checkpoint_transfer import (
        CHUNK_BYTES,
        MAX_FILE_BYTES,
        MAX_FILES,
    )

    assert (CHUNK_BYTES, MAX_FILE_BYTES, MAX_FILES) == (65536, 524288, 11)
    assert MAX_FILE_BYTES // CHUNK_BYTES * MAX_FILES == 88
    checks = " ".join(
        str(x.sqltext)
        for x in OEStructuredCheckpointChunk.__table__.constraints
        if isinstance(x, CheckConstraint)
    )
    assert "sequence BETWEEN 0 AND 87" in checks
    assert (
        "offset_bytes IN (0,65536,131072,196608,262144,327680,393216,458752)" in checks
    )

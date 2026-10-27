"""Pure declarations only, never proof of execution or publication authority."""

from uuid import uuid4

import pytest

from packages.factors.execution_snapshot import (
    ExecutionObject,
    ExecutionSnapshotManifest,
    NormalizedPartition,
)
from tests.rdagent.test_rdagent_numeric_profile import projected


def manifest():
    _, snapshot, prefix, _, _, _ = projected()
    owner = uuid4()

    def obj(kind, sha):
        suffix = "parquet" if kind == "normalized_values" else "json"
        return ExecutionObject(
            object_id=uuid4(),
            owner_id=owner,
            kind=kind,
            sha256=sha * 64,
            size_bytes=10,
            storage_key=f"factors/{owner}/objects/{sha * 64}.{suffix}",
        )

    partition = NormalizedPartition(
        cutoff_id=uuid4(),
        ordinal=0,
        prefix_sha256=prefix.sha256,
        native_publication_id=uuid4(),
        candidate_admission_id=uuid4(),
        normalizer_admission_id=uuid4(),
        normalizer_input_sha256="a" * 64,
        stop_receipt_id=uuid4(),
        stop_receipt_sha256="b" * 64,
        extraction_receipt_id=uuid4(),
        extraction_receipt_sha256="c" * 64,
        primitive_report_sha256="d" * 64,
        primitive_report_size_bytes=10,
        values=obj("normalized_values", "e"),
        assembly_sha256="f" * 64,
        output_rows_sha256="1" * 64,
        row_count=3,
        valid_count=2,
    )
    return ExecutionSnapshotManifest(
        schema_version="mineagent.factor-execution-snapshot.v1",
        snapshot_id=uuid4(),
        owner_id=owner,
        run_id=uuid4(),
        attempt_id=uuid4(),
        generation=1,
        generation_start_id=uuid4(),
        source_id=uuid4(),
        evaluation_id=uuid4(),
        source_read_witness_id=uuid4(),
        source_read_witness_sha256="2" * 64,
        input=snapshot,
        input_sha256=snapshot.sha256,
        expected_cutoffs=1,
        partitions=(partition,),
        assembled_rows=obj("assembled_rows", "3"),
        output_rows_sha256="4" * 64,
        row_count=3,
        valid_count=2,
    )


def test_execution_identity_preserves_full_input_commitment():
    value = manifest()
    assert (
        value.partitions[0].normalizer_input_sha256 != value.partitions[0].values.sha256
    )
    assert value.dependency_claim == "provided_not_proven_read"
    assert "local_verification" not in value.model_dump()
    assert "generation_outcome_id" not in value.model_dump()


@pytest.mark.parametrize(
    "change",
    [
        {"expected_cutoffs": 2},
        {"row_count": 4},
        {"valid_count": 3},
        {"input_sha256": "f" * 64},
        {"owner_id": str(uuid4())},
        {"local_verification": {"kind": "verified_source"}},
    ],
)
def test_execution_manifest_refuses_incomplete_or_detached_claims(change):
    value = manifest().model_dump(mode="json")
    value.update(change)
    with pytest.raises(ValueError):
        ExecutionSnapshotManifest.model_validate(value)


def test_execution_partition_repetition_is_not_complete_evaluation():
    value = manifest().model_dump(mode="json")
    value["partitions"].append(dict(value["partitions"][0], ordinal=1))
    value.update(expected_cutoffs=2, row_count=6, valid_count=4)
    with pytest.raises(ValueError, match="repeats"):
        ExecutionSnapshotManifest.model_validate(value)

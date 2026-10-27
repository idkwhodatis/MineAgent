"""Host assembly with explicit authority doubles, not a successful execution claim.

The complete inventory and stopped-report services are mocked here. Genuine
source/process/whole-window authority must pass the separate PG/Docker fixture.
"""

import hashlib
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import execution_snapshot_assembly as assembly
from backend.app import (
    normalized_cutoff_services,
    rdagent_complete_evaluation,
    rdagent_source_resolution,
)
from backend.app.services import DomainError
from tests.rdagent.test_rdagent_assembly import fixture


@pytest.fixture
def material(monkeypatch):
    from backend.app import factor_analysis_dispatch

    # Historical021 authority double; configured022 dispatch has separate tests.
    monkeypatch.setattr(
        factor_analysis_dispatch, "configured_analysis_run", lambda *args: False
    )
    snapshot, prefix, ns = fixture()
    fence = NS(owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1)
    cutoff = NS(
        execution_id=uuid4(),
        candidate_admission_id=uuid4(),
        normalizer_admission_id=uuid4(),
        native_publication_id=uuid4(),
        ordinal=0,
        prefix=prefix,
    )
    inventory = NS(
        evaluation_id=uuid4(),
        generation_start_id=uuid4(),
        source_id=uuid4(),
        factor_version_id=snapshot.version.factor_version_id,
        source_object_id=uuid4(),
        snapshot_input=snapshot,
        cutoffs=(cutoff,),
    )
    source = NS(read_witness_id=uuid4(), read_witness_sha256="a" * 64)
    raw = b"opaque-parquet-test-double-not-native-data"
    result = NS(
        **vars(fence),
        source_id=inventory.source_id,
        evaluation_id=inventory.evaluation_id,
        cutoff_id=cutoff.execution_id,
        cutoff_ordinal=0,
        factor_version_id=inventory.factor_version_id,
        code_object_id=inventory.source_object_id,
        source_sha256=snapshot.version.code_sha256,
        prefix_sha256=prefix.sha256,
        snapshot_input_sha256=snapshot.sha256,
        admission_id=cutoff.normalizer_admission_id,
        native_publication_id=cutoff.native_publication_id,
        normalizer_input_sha256="b" * 64,
        listings=tuple((v.code, v.listing_id) for v in prefix.listings),
        parquet_bytes=raw,
        parquet_sha256=hashlib.sha256(raw).hexdigest(),
        parquet_size_bytes=len(raw),
        rows=((ns, 0, 1.5, 0), (ns, 1, None, 1)),
        stop_receipt_id=uuid4(),
        stop_receipt_sha256="c" * 64,
        extraction_receipt_id=uuid4(),
        extraction_receipt_sha256="d" * 64,
        report_sha256="e" * 64,
        report_size_bytes=100,
    )
    native = NS(admission_id=cutoff.candidate_admission_id, sha256="f" * 64)
    session = NS(
        get=lambda model, key: native if key == cutoff.native_publication_id else None
    )
    calls = []
    monkeypatch.setattr(
        assembly,
        "require_neutral_transaction",
        lambda *a, **kw: calls.append(("scope", kw["resource_scope"])),
    )
    monkeypatch.setattr(
        assembly, "require_native_storage_guard", lambda *a: calls.append(("guard",))
    )
    monkeypatch.setattr(
        rdagent_complete_evaluation, "resolve_complete_evaluation", lambda *a: inventory
    )
    monkeypatch.setattr(
        rdagent_source_resolution, "require_source_code", lambda *a, **kw: source
    )
    monkeypatch.setattr(
        normalized_cutoff_services, "read_normalized_cutoff", lambda *a: result
    )
    return NS(
        session=session,
        fence=fence,
        settings=NS(storage_root="/unused-test-double"),
        inventory=inventory,
        result=result,
        native=native,
        calls=calls,
        snapshot_id=uuid4(),
    )


def resolve(f, existing=None):
    return assembly.resolve_snapshot_materialization(
        f.session,
        f.fence,
        f.inventory.evaluation_id,
        f.settings,
        snapshot_id=f.snapshot_id,
        existing=existing,
    )


def test_assembly_replay_retains_all_object_ids_and_host_missingness(material):
    f = material
    manifest, physical, rows = resolve(f)
    assert len(rows) == 3 and manifest.valid_count == 1
    assert rows[1].missing_reason == "candidate_reported_missing"
    assert rows[2].missing_reason == "candidate_omitted_output"
    assert resolve(f, manifest) == (manifest, physical, rows)
    assert f.calls == [("scope", "factor_numeric"), ("guard",)] * 2


@pytest.mark.parametrize(
    "field",
    [
        "owner_id",
        "evaluation_id",
        "cutoff_id",
        "code_object_id",
        "native_publication_id",
        "admission_id",
    ],
)
def test_assembly_rejects_rebound_report_context(material, field):
    setattr(material.result, field, uuid4())
    with pytest.raises(DomainError):
        resolve(material)


@pytest.mark.parametrize("change", ["parquet", "listings", "candidate", "primitive"])
def test_assembly_rejects_bytes_inventory_or_native_chain_drift(material, change):
    if change == "parquet":
        material.result.parquet_bytes = b"drift"
    elif change == "listings":
        material.result.listings = ()
    elif change == "candidate":
        material.native.admission_id = uuid4()
    else:
        material.result.rows = ((0, 0, 1.0, 0),)
    with pytest.raises((DomainError, ValueError)):
        resolve(material)


def test_existing_partition_count_drift_is_domain_error_not_index_error(material):
    manifest, _, _ = resolve(material)
    material.inventory.cutoffs = material.inventory.cutoffs * 2
    with pytest.raises(DomainError):
        resolve(material, manifest)

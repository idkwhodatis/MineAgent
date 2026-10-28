"""Nonempty retained execution closure using real codecs and file commitments.

Dependency rows are synthetic, as in the native graph fixture; no process or
PostgreSQL execution is claimed. No execution validator is stubbed.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.app import execution_snapshot_backup as backup
from backend.app import factor_native_backup as native_backup
from backend.app.execution_snapshot_io import inventory, journal_address
from backend.app.execution_snapshot_services import _physical_receipt
from backend.app.factor_analysis_backup import FIELDS as ANALYSIS_FIELDS
from packages.factors.contracts import canonical_bytes, digest, rows_digest
from packages.factors.execution_snapshot import (
    ExecutionObject,
    ExecutionPublicationJournal,
    ExecutionSnapshotManifest,
    NormalizedPartition,
)
from packages.factors.native_normalizer_binding import primitive_report_context
from packages.factors.native_publication import NativePublicationIntent
from packages.rdagent_adapter.assembly import assemble_cutoff_rows
from packages.research_runtime.budget_contracts import BudgetVector
from tests.factors.test_native_backup_graph import graph
from tests.rdagent.test_rdagent_numeric_profile import projected


@pytest.fixture(params=[(6, 3), (10, 5), (11, 6)])
def retained_execution(tmp_path, monkeypatch, request):
    # Capture only the dependency fixture's already-built inputs. The real
    # execution closure and every parser/hash/relationship check run below.
    from tests.rdagent import test_rdagent_numeric_profile as profile_fixture

    _, _, last_prefix, _, _, _ = projected()
    original_config = profile_fixture.research_dict()
    original_config["input"]["window"] = {
        "start": last_prefix.output_session.isoformat(),
        "end": (last_prefix.output_session + timedelta(days=1)).isoformat(),
    }
    monkeypatch.setattr(profile_fixture, "research_dict", lambda: original_config)
    dependencies = {}
    native, native_validate = graph(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(
            native_backup, "validate_closure", lambda *a, **kw: dependencies.update(kw)
        )
        native_validate(native)
    research = dependencies["research_closure"]
    rd = dependencies["rd_source_closure"]
    rv, sv = request.param
    research["schema_version"] = f"mineagent.research-restore-closure.v{rv}"
    rd["schema_version"] = f"mineagent.rd-source-restore-closure.v{sv}"
    native["schema_version"] = (
        f"mineagent.native-hdf-restore-closure.v{2 if rv == 6 else 3}"
    )
    nt, rt, dt = native["tables"], research["tables"], rd["tables"]
    pub = nt["factor_native_publications"][0]
    intent = NativePublicationIntent.model_validate_json(canonical_bytes(pub["intent"]))
    _config, snapshot, prefix, _, _, _ = projected()
    scope = {k: pub[k] for k in backup.SCOPE}
    evaluation = dt["factor_evaluation_starts"][0]
    evaluation.update(
        start_id=str(intent.generation_start_id),
        evaluated_source_id=pub["source_id"],
        factor_version_id=str(snapshot.version.factor_version_id),
    )
    dt["factor_generation_starts"] = [
        {
            "id": str(intent.generation_start_id),
            "spec_id": dt["factor_research_specs"][0]["id"],
        }
    ]
    witness_id = str(uuid4())
    dt["factor_source_read_witnesses"] = [
        {"id": witness_id, "source_id": pub["source_id"], "witness_sha256": "a" * 64}
    ]
    normalizer, process_reservation, stop_id, ex_id = (str(uuid4()) for _ in range(4))
    rt["research_process_admissions"].append(
        dict(id=normalizer, reservation_id=process_reservation, **scope)
    )
    rt["research_budget_reservations"].append(
        dict(id=process_reservation, state="reserved", **scope)
    )
    nt["factor_native_normalizer_inputs"] = [
        {
            "admission_id": normalizer,
            "publication_id": pub["id"],
            "input_sha256": "b" * 64,
        }
    ]
    physical = {}

    def obj(kind, raw):
        sha = hashlib.sha256(raw).hexdigest()
        suffix = "parquet" if kind == "normalized_values" else "json"
        item = ExecutionObject(
            object_id=uuid4(),
            owner_id=scope["owner_id"],
            kind=kind,
            sha256=sha,
            size_bytes=len(raw),
            storage_key=f"factors/{scope['owner_id']}/objects/{sha}.{suffix}",
        )
        physical[item.storage_key] = raw
        return item

    values = obj("normalized_values", b"opaque parquet: not parsed on host")
    manifest_raw, listings = primitive_report_context(prefix, intent.output)
    ns = (
        (prefix.output_event_time - datetime(1970, 1, 1, tzinfo=UTC))
        // timedelta(microseconds=1)
    ) * 1000
    primitives = tuple((ns, item.code, 1.5, 0) for item in prefix.listings)
    report = {
        "schema_version": "mineagent.factor-normalized-primitives.v1",
        "parquet_sha256": values.sha256,
        "parquet_size_bytes": values.size_bytes,
        "input_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "listings_sha256": hashlib.sha256(listings).hexdigest(),
        "rows": [list(r) for r in primitives],
    }
    report_raw = canonical_bytes(report)
    report_sha = hashlib.sha256(report_raw).hexdigest()
    output_root = f"research-processes/{scope['owner_id']}/{normalizer}/output/"
    physical[output_root + "values.parquet"] = physical[values.storage_key]
    physical[output_root + "rows.json"] = report_raw
    ex_payload = {
        "schema_version": "mineagent.numeric-process-extraction.v3",
        "sha256": values.sha256,
        "size_bytes": values.size_bytes,
        "report_sha256": report_sha,
        "report_size_bytes": len(report_raw),
    }
    rt["research_process_receipts"].extend(
        [
            {
                "id": stop_id,
                "admission_id": normalizer,
                "kind": "stop",
                "payload_sha256": "c" * 64,
            },
            {
                "id": ex_id,
                "admission_id": normalizer,
                "kind": "extraction",
                "payload_sha256": digest(ex_payload),
                "payload": ex_payload,
            },
        ]
    )
    rows, assembly = assemble_cutoff_rows(
        snapshot,
        prefix,
        primitives,
        hdf_sha256=intent.output.object.sha256,
        normalized_values_sha256=values.sha256,
    )
    rows_raw = canonical_bytes(
        {
            "schema_version": "mineagent.execution-feature-rows.v1",
            "input_sha256": snapshot.sha256,
            "rows": [r.model_dump(mode="json") for r in rows],
        }
    )
    part = NormalizedPartition(
        cutoff_id=pub["cutoff_id"],
        ordinal=0,
        prefix_sha256=prefix.sha256,
        native_publication_id=pub["id"],
        candidate_admission_id=intent.admission.id,
        normalizer_admission_id=normalizer,
        normalizer_input_sha256="b" * 64,
        stop_receipt_id=stop_id,
        stop_receipt_sha256="c" * 64,
        extraction_receipt_id=ex_id,
        extraction_receipt_sha256=digest(ex_payload),
        primitive_report_sha256=report_sha,
        primitive_report_size_bytes=len(report_raw),
        values=values,
        assembly_sha256=digest(assembly),
        output_rows_sha256=assembly.output_rows_sha256,
        row_count=assembly.row_count,
        valid_count=assembly.valid_count,
    )
    m = ExecutionSnapshotManifest(
        schema_version="mineagent.factor-execution-snapshot.v1",
        snapshot_id=uuid4(),
        **scope,
        generation_start_id=intent.generation_start_id,
        source_id=pub["source_id"],
        evaluation_id=evaluation["id"],
        source_read_witness_id=witness_id,
        source_read_witness_sha256="a" * 64,
        input=snapshot,
        input_sha256=snapshot.sha256,
        expected_cutoffs=1,
        partitions=(part,),
        assembled_rows=obj("assembled_rows", rows_raw),
        output_rows_sha256=rows_digest(rows),
        row_count=len(rows),
        valid_count=assembly.valid_count,
    )
    manifest_obj = obj("execution_manifest", canonical_bytes(m))
    total = sum(o.size_bytes for o in (manifest_obj, m.assembled_rows, values))
    journal = ExecutionPublicationJournal(
        schema_version="mineagent.execution-snapshot-publication.v1",
        publication_id=m.snapshot_id,
        reservation_id=uuid4(),
        manifest=m,
        manifest_object=manifest_obj,
        peak_bytes=2 * total + 4194304,
        peak_entries=11,
    )
    key, sha, raw = journal_address(journal)
    physical[key] = raw
    closure = {
        "schema_version": backup.SCHEMA if rv == 6 else backup.SCHEMA_V2,
        "tables": {k: [] for k in backup.FIELDS},
    }

    def add(table, **values):
        row = dict.fromkeys(backup.FIELDS[table].split())
        row.update(
            {
                k: v
                for k, v in {
                    **scope,
                    "created_at": "2020-01-01T00:00:00Z",
                    **values,
                }.items()
                if k in row
            }
        )
        assert None not in row.values(), (table, row)
        closure["tables"][table].append(row)

    add(
        "factor_execution_snapshot_publications",
        id=str(m.snapshot_id),
        evaluation_id=str(m.evaluation_id),
        source_id=str(m.source_id),
        start_id=str(m.generation_start_id),
        factor_version_id=str(m.input.version.factor_version_id),
        reservation_id=str(journal.reservation_id),
        manifest=m.model_dump(mode="json"),
        manifest_sha256=manifest_obj.sha256,
        manifest_object_id=str(manifest_obj.object_id),
        manifest_size_bytes=manifest_obj.size_bytes,
        manifest_storage_key=manifest_obj.storage_key,
        journal=journal.model_dump(mode="json"),
        journal_sha256=sha,
        journal_key=key,
        peak_bytes=journal.peak_bytes,
        peak_entries=journal.peak_entries,
    )
    add(
        "factor_execution_snapshot_partitions",
        publication_id=str(m.snapshot_id),
        ordinal=0,
        cutoff_id=str(part.cutoff_id),
        native_publication_id=str(part.native_publication_id),
        normalizer_admission_id=normalizer,
        stop_receipt_id=stop_id,
        extraction_receipt_id=ex_id,
        stop_kind="stop",
        extraction_kind="extraction",
        binding=part.model_dump(mode="json"),
        binding_sha256=digest(part),
    )
    amounts = BudgetVector(storage_bytes=journal.peak_bytes).model_dump()
    rt["research_budget_reservations"].append(
        dict(
            id=str(journal.reservation_id),
            **scope,
            purpose="object_storage",
            logical_key="execution-snapshot:" + str(m.snapshot_id),
            amounts=amounts,
            state="settled",
        )
    )
    rt["research_budget_settlements"].append(
        {
            "id": str(uuid4()),
            "reservation_id": str(journal.reservation_id),
            "outcome": "conservative_unknown",
            "charge": amounts,
        }
    )
    add(
        "engine_object_reservation_consumers",
        reservation_id=str(journal.reservation_id),
        consumer_kind="execution_snapshot",
        consumer_id=str(m.snapshot_id),
    )
    add(
        "factor_execution_snapshot_outcomes",
        publication_id=str(m.snapshot_id),
        manifest_sha256=digest(m),
        input_sha256=m.input_sha256,
        output_sha256=m.output_rows_sha256,
        physical_receipt_sha256=_physical_receipt(journal),
    )
    for item in inventory(journal):
        add(
            "factor_execution_snapshot_objects",
            id=str(item.object_id),
            owner_id=str(item.owner_id),
            publication_id=str(m.snapshot_id),
            object_kind=item.kind,
            sha256=item.sha256,
            size_bytes=item.size_bytes,
            storage_key=item.storage_key,
        )
    for path, raw in physical.items():
        dest = tmp_path / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)

    def check(path, sha, size):
        raw = (tmp_path / path).read_bytes()
        assert (hashlib.sha256(raw).hexdigest(), len(raw)) == (sha, size)

    def validate():
        return backup.validate_closure(
            closure,
            check,
            lambda p: json.loads((tmp_path / p).read_bytes()),
            1000,
            inventory_rows=[
                {
                    "path": p,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                }
                for p, raw in physical.items()
            ],
            research_closure=research,
            rd_source_closure=rd,
            native_closure=native,
            direct_analysis_closure={
                "schema_version": "mineagent.direct-analysis-restore-closure.v1",
                "tables": {k: [] for k in ANALYSIS_FIELDS},
            },
        )

    return closure, native, validate


def test_database_json_intent_requires_json_mode(retained_execution):
    _, native, validate = retained_execution
    raw = native["tables"]["factor_native_publications"][0]["intent"]
    with pytest.raises(ValidationError) as error:
        NativePublicationIntent.model_validate(raw)
    assert any(e["loc"][:1] == ("admission",) for e in error.value.errors())
    assert (
        NativePublicationIntent.model_validate_json(canonical_bytes(raw)).model_dump(
            mode="json"
        )
        == raw
    )
    validate()


@pytest.mark.parametrize(
    "field",
    [
        "candidate_admission_id",
        "native_publication_id",
        "stop_receipt_id",
        "extraction_receipt_id",
        "prefix_sha256",
        "binding_sha256",
    ],
)
def test_completed_execution_rejects_rebound_partition(retained_execution, field):
    closure, _, validate = retained_execution
    part = closure["tables"]["factor_execution_snapshot_partitions"][0]
    if field in part:
        part[field] = str(uuid4()) if field.endswith("_id") else "f" * 64
    else:
        part["binding"][field] = str(uuid4()) if field.endswith("_id") else "f" * 64
    with pytest.raises((ValueError, KeyError)):
        validate()


@pytest.mark.parametrize("change", ["bool", "float", "uuid", "extra", "ipc", "role"])
def test_nested_strict_admission_still_rejects_invalid_data(retained_execution, change):
    _, native, validate = retained_execution
    raw = native["tables"]["factor_native_publications"][0]["intent"]
    if change == "bool":
        raw["admission"]["generation"] = True
    elif change == "float":
        raw["admission"]["generation"] = 1.5
    elif change == "uuid":
        raw["admission"]["id"] = "not-a-uuid"
    elif change == "extra":
        raw["admission"]["new_authority"] = True
    elif change == "ipc":
        raw["admission"]["ipc"]["ticket_sha256"] = "a" * 64
    elif change == "role":
        raw["admission"]["role"] = "native_evaluator"
    with pytest.raises(ValidationError):
        validate()

"""Strict stdlib report grammar; no native parsing or runtime acceptance."""

import hashlib
import json

import pytest

from packages.research_runtime.process_contracts import ProcessError
from runtimes.factor_normalizer.primitive_report import (
    build_report,
    canonical,
    validate_report,
)


def fixture_bytes():
    parquet = b"opaque not parsed by host"
    listings = canonical(
        [
            {"code": 0, "listing_id": "fixture:A"},
            {"code": 1, "listing_id": "fixture:B"},
        ]
    )
    manifest = canonical(
        {
            "schema_version": "mineagent.factor-normalization.v2",
            "hdf_sha256": "a" * 64,
            "listings_sha256": hashlib.sha256(listings).hexdigest(),
            "output_event_ns": 1710532800000000000,
            "max_rows": 2,
            "max_bytes": 10000,
        }
    )
    rows = ((1710532800000000000, 0, 0.125, 0), (1710532800000000000, 1, None, 1))
    kwargs = {"parquet": parquet, "manifest": manifest, "listings": listings}
    return rows, kwargs, build_report(rows, **kwargs)


def test_exact_bound_primitive_rows_without_native_parser():
    rows, kwargs, raw = fixture_bytes()
    assert validate_report(raw, **kwargs) == rows
    assert b"NaN" not in raw


@pytest.mark.parametrize("field", ["parquet", "manifest", "listings"])
def test_retained_byte_substitution_rejected(field):
    _, kwargs, raw = fixture_bytes()
    kwargs[field] += b" "
    with pytest.raises(ValueError):
        validate_report(raw, **kwargs)


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "reorder",
        "missing",
        "extra",
        "boolean_event",
        "boolean_code",
        "boolean_state",
        "integer_value",
        "nan",
        "infinity",
        "missing_value",
        "state_value",
        "unknown_state",
        "wrong_time",
        "wrong_listing",
        "extra_column",
        "extra_field",
        "wrong_hash",
        "boolean_size",
        "wrong_schema",
    ],
)
def test_malformed_or_context_drifting_reports_rejected(mutation):
    _, kwargs, raw = fixture_bytes()
    d = json.loads(raw)
    rows = d["rows"]
    if mutation == "duplicate":
        rows[1] = rows[0]
    elif mutation == "reorder":
        rows.reverse()
    elif mutation == "missing":
        rows.pop()
    elif mutation == "extra":
        rows.append(rows[0])
    elif mutation == "boolean_event":
        rows[0][0] = True
    elif mutation == "boolean_code":
        rows[0][1] = False
    elif mutation == "boolean_state":
        rows[0][3] = False
    elif mutation == "integer_value":
        rows[0][2] = 1
    elif mutation == "nan":
        rows[0][2] = float("nan")
    elif mutation == "infinity":
        rows[0][2] = float("inf")
    elif mutation == "missing_value":
        rows[0][2] = None
    elif mutation == "state_value":
        rows[1][2] = 0.0
    elif mutation == "unknown_state":
        rows[0][3] = 2
    elif mutation == "wrong_time":
        rows[0][0] += 1
    elif mutation == "wrong_listing":
        rows[1][1] = 7
    elif mutation == "extra_column":
        rows[0].append("unexpected")
    elif mutation == "extra_field":
        d["claim"] = "approved"
    elif mutation == "wrong_hash":
        d["parquet_sha256"] = "b" * 64
    elif mutation == "boolean_size":
        d["parquet_size_bytes"] = True
    else:
        d["schema_version"] = "mineagent.factor-normalized-primitives.v2"
    with pytest.raises(ValueError):
        validate_report(
            json.dumps(d, sort_keys=True, separators=(",", ":")).encode(), **kwargs
        )


def test_duplicate_json_keys_and_noncanonical_bytes_rejected():
    _, kwargs, raw = fixture_bytes()
    for changed in (raw + b"\n", raw.replace(b'"rows":', b'"rows":[],"rows":')):
        with pytest.raises(ValueError):
            validate_report(changed, **kwargs)


def test_report_frame_explicit_kind_preserves_legacy_export():
    from runtimes.factor_execution.protected_output import (
        frame,
        parse_frame,
        report_export,
        validate_export,
        validate_report_export,
    )

    _, kwargs, report = fixture_bytes()
    payload = report_export(kwargs["parquet"], report, 0)
    assert parse_frame(frame(4, payload)) == (4, payload)
    assert validate_report_export(payload) == (kwargs["parquet"], report)
    with pytest.raises(ValueError):
        validate_export(payload, expected_basename="values.parquet")
    with pytest.raises(ValueError):
        validate_report_export(report_export(kwargs["parquet"], report, 2))


@pytest.mark.parametrize(
    "mutation", ["truncate", "append", "parquet", "report", "metadata"]
)
def test_report_frame_tampering_rejected(mutation):
    from runtimes.factor_execution.protected_output import (
        report_export,
        validate_report_export,
    )

    _, kwargs, report = fixture_bytes()
    payload = report_export(kwargs["parquet"], report, 0)
    if mutation == "truncate":
        payload = payload[:-1]
    elif mutation == "append":
        payload += b" "
    elif mutation == "parquet":
        payload = payload.replace(kwargs["parquet"], b"X" * len(kwargs["parquet"]))
    elif mutation == "report":
        payload = payload[:-1] + b"]"
    else:
        payload = payload.replace(
            b'"descendants_reaped":true', b'"descendants_reaped":null'
        )
    with pytest.raises(ValueError):
        validate_report_export(payload)


def test_versioned_report_receipt_retains_and_rereads_actual_bytes(tmp_path):
    from packages.research_runtime.numeric_process_journal import (
        NumericReportExtraction,
        validate_numeric_journal,
    )
    from runtimes.research_supervisor.numeric_journal import read_primitive_report
    from tests.research_runtime.test_numeric_process_boundary import journal

    admission, launch, identity, stop, extraction, _, path = journal(
        tmp_path, "factor_normalizer"
    )
    report = b'{"synthetic_structure_test_only":true}'
    payload = extraction.model_dump(mode="json")
    payload.update(
        schema_version="mineagent.numeric-process-extraction.v3",
        protocol="MANU2-report-v1",
        report_basename="rows.json",
        report_sha256=hashlib.sha256(report).hexdigest(),
        report_size_bytes=len(report),
        input_manifest_sha256="a" * 64,
        listings_sha256="a" * 64,
    )
    extraction = NumericReportExtraction.model_validate(payload)
    validate_numeric_journal(launch, identity, stop, extraction)
    retained = path / "output" / "rows.json"
    retained.write_bytes(report)
    retained.chmod(0o600)
    assert read_primitive_report(tmp_path, admission, extraction) == report
    retained.write_bytes(b"X" * len(report))
    with pytest.raises(ProcessError):
        read_primitive_report(tmp_path, admission, extraction)


def test_report_receipt_binds_exact_launch_input_inventory(tmp_path):
    from packages.research_runtime.numeric_process_journal import (
        NumericReportExtraction,
        validate_numeric_journal,
    )
    from tests.research_runtime.test_numeric_process_boundary import journal

    _, launch, identity, stop, extraction, _, _ = journal(tmp_path, "factor_normalizer")
    payload = extraction.model_dump(mode="json")
    payload.update(
        schema_version="mineagent.numeric-process-extraction.v3",
        protocol="MANU2-report-v1",
        report_basename="rows.json",
        report_sha256="b" * 64,
        report_size_bytes=10,
        input_manifest_sha256="f" * 64,
        listings_sha256="a" * 64,
    )
    with pytest.raises(ProcessError):
        validate_numeric_journal(
            launch, identity, stop, NumericReportExtraction.model_validate(payload)
        )


def test_report_sql_preserves_v1_literal_and_exact_graph_predicates():
    from backend.app.native_storage_ddl import NORMALIZER_SQL
    from backend.app.normalizer_report_ddl import normalizer_report_sql

    changed = normalizer_report_sql()
    assert "factor-normalization.v2" not in NORMALIZER_SQL
    assert "CREATE OR REPLACE FUNCTION" in changed
    assert "factor-normalization.v1" in changed and "factor-normalization.v2" in changed
    assert "IF li.payload->'inputs'->'files' IS DISTINCT FROM files THEN" in changed
    assert "OR li.payload->'inputs'->'files' IS DISTINCT FROM files" in changed
    assert "mineagent_native_check" in changed
    assert "CREATE TRIGGER" not in changed


@pytest.mark.parametrize(
    "case",
    [
        "held",
        "settled",
        "partial",
        "wrong_scope",
        "wrong_outcome",
        "missing_settlement",
        "held_with_settlement",
    ],
)
def test_completed_report_accounting_requires_held_or_exact_conservative_charge(case):
    from types import SimpleNamespace
    from uuid import uuid4

    from backend.app.native_normalizer_claim import _require_completed_reservation
    from backend.app.services import DomainError
    from packages.research_runtime.budget_contracts import BudgetVector

    scope = {key: uuid4() for key in ("owner_id", "run_id", "attempt_id")}
    scope["generation"] = 1
    fence = SimpleNamespace(**scope)
    minimum = BudgetVector(
        process_slots=1, wall_milliseconds=60000, storage_bytes=67_174_400
    )
    held = case in ("held", "held_with_settlement")
    reservation = SimpleNamespace(
        **scope,
        purpose="process_unit",
        amounts=minimum.model_dump(),
        state="reserved" if held else "settled",
    )
    settlement = (
        None
        if case in ("held", "missing_settlement")
        else SimpleNamespace(
            **scope, charge=minimum.model_dump(), outcome="conservative_unknown"
        )
    )
    if case == "partial":
        settlement.charge = BudgetVector().model_dump()
    if case == "wrong_scope":
        settlement.owner_id = uuid4()
    if case == "wrong_outcome":
        settlement.outcome = "measured"
    session = SimpleNamespace(
        get=lambda *_a, **_kw: reservation, scalar=lambda *_a: settlement
    )
    if case in ("held", "settled"):
        _require_completed_reservation(session, fence, uuid4(), minimum)
    else:
        with pytest.raises(DomainError) as rejected:
            _require_completed_reservation(session, fence, uuid4(), minimum)
        assert rejected.value.code == "native_normalizer_reservation_drift"


def test_resolved_report_api_returns_exact_immutable_context_with_authority_doubles(
    monkeypatch,
):
    """API wiring only; doubles do not prove SQL, stop, native publication or launch."""
    from types import SimpleNamespace as NS
    from uuid import uuid4

    from backend.app import (
        factor_native_services,
        normalizer_report_ddl,
        rdagent_services,
    )
    from backend.app import normalized_cutoff_services as service
    from packages.factors.contracts import canonical_bytes
    from packages.rdagent_adapter.numeric_profile import normalizer_inputs
    from packages.research_runtime.numeric_process_journal import (
        NumericReportExtraction,
    )
    from runtimes.factor_execution.acceptance import frozen_inputs

    prefix, original, event_ns = frozen_inputs(
        "sha256:" + "a" * 64, "sha256:" + "b" * 64
    )
    native, parquet = b"opaque HDF never parsed", b"opaque Parquet never parsed"
    files, inputs, _ = normalizer_inputs(
        prefix,
        hdf=native,
        expected_hdf_sha256=hashlib.sha256(native).hexdigest(),
        listings=original["/input/listings.json"],
        primitive_report=True,
    )
    rows = tuple((event_ns, item.code, 1.0, 0) for item in prefix.listings)
    report = build_report(
        rows,
        parquet=parquet,
        manifest=inputs["/input/manifest.json"],
        listings=inputs["/input/listings.json"],
    )
    extraction = NumericReportExtraction(
        schema_version="mineagent.numeric-process-extraction.v3",
        launch_sha256="a" * 64,
        identity_sha256="b" * 64,
        protocol="MANU2-report-v1",
        basename="values.parquet",
        sha256=hashlib.sha256(parquet).hexdigest(),
        size_bytes=len(parquet),
        protected_frame_complete=True,
        producer_quiesced=True,
        report_basename="rows.json",
        report_sha256=hashlib.sha256(report).hexdigest(),
        report_size_bytes=len(report),
        input_manifest_sha256=hashlib.sha256(
            inputs["/input/manifest.json"]
        ).hexdigest(),
        listings_sha256=hashlib.sha256(inputs["/input/listings.json"]).hexdigest(),
    )
    fence = NS(owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1)
    admission = NS(id=uuid4(), input_sha256="c" * 64)
    publication = NS(id=uuid4(), source_id=uuid4(), cutoff_id=uuid4())
    cutoff = NS(
        id=publication.cutoff_id,
        evaluation_id=uuid4(),
        cutoff_ordinal=0,
        projection=prefix.model_dump(mode="json"),
        factor_version_id=uuid4(),
    )
    evaluation = NS(id=cutoff.evaluation_id, input_sha256=prefix.snapshot_input_sha256)
    candidate = NS(source_object_id=uuid4(), source_sha256="d" * 64)
    receipt = NS(
        admission=admission,
        extraction_receipt_id=uuid4(),
        extraction_receipt_hash="e" * 64,
        stop_receipt_id=uuid4(),
        stop_receipt_hash="f" * 64,
    )
    monkeypatch.setattr(service, "require_neutral_transaction", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        normalizer_report_ddl, "require_report_capability", lambda *_a: None
    )
    monkeypatch.setattr(
        service,
        "resolve_completed_native_normalizer",
        lambda *_a: (
            publication,
            admission,
            NS(inputs=NS(files=files)),
            NS(object=NS(sha256=hashlib.sha256(native).hexdigest())),
            native,
        ),
    )
    monkeypatch.setattr(service, "_resolve", lambda *_a: (receipt, extraction))
    monkeypatch.setattr(service, "read_opaque_output", lambda *_a: parquet)
    monkeypatch.setattr(service, "read_primitive_report", lambda *_a: report)
    monkeypatch.setattr(factor_native_services, "decode_intent", lambda *_a: candidate)
    monkeypatch.setattr(
        rdagent_services,
        "_scope_row",
        lambda _s, _m, identity, *_a, **_kw: (
            cutoff if identity == cutoff.id else evaluation
        ),
    )
    result = service.read_normalized_cutoff(
        object(), fence, admission.id, NS(storage_root="unused authority double")
    )
    assert result.rows == rows and result.parquet_bytes == parquet
    assert result.native_publication_id == publication.id
    assert (
        result.source_id == publication.source_id
        and result.evaluation_id == evaluation.id
    )
    assert result.normalizer_input_sha256 == admission.input_sha256
    assert (
        canonical_bytes(
            [
                {"code": code, "listing_id": identity}
                for code, identity in result.listings
            ]
        )
        == inputs["/input/listings.json"]
    )


@pytest.mark.parametrize(
    "revision",
    [
        "021_execution_snapshots",
        "022_factor_analysis",
        "023_rd_submissions",
        "024_openevolve_initial",
        "025_structured_factor_strategy",
        "026_rd_seeded_submissions",
        "027_rd_controller_v3",
        "027a_contract_repairs",
        "027b_provider_material",
        "027c_seeded_controller_v4",
    ],
)
def test_report_capability_inherits_only_installed_exact_successor_pairs(revision):
    from types import SimpleNamespace

    from backend.app.normalizer_report_ddl import require_report_capability

    session = SimpleNamespace(scalars=lambda _: SimpleNamespace(all=lambda: [revision]))
    require_report_capability(session)


@pytest.mark.parametrize(
    "revisions",
    [
        ["020_rd_controller"],
        ["027_unknown_future"],
        [],
        ["025_structured_factor_strategy", "026_rd_seeded_submissions"],
    ],
)
def test_report_capability_refuses_old_unknown_or_ambiguous_installation(revisions):
    from types import SimpleNamespace

    from backend.app.normalizer_report_ddl import require_report_capability

    session = SimpleNamespace(scalars=lambda _: SimpleNamespace(all=lambda: revisions))
    with pytest.raises((ProcessError, ValueError)):
        require_report_capability(session)


@pytest.mark.parametrize(
    "field,value",
    [
        (
            "execution_snapshot_schema",
            "mineagent.execution-snapshot-restore-closure.v1",
        ),
        ("native_closure_schema", "mineagent.native-hdf-restore-closure.v2"),
        ("research_closure_schema", "mineagent.research-restore-closure.v13"),
    ],
)
def test_report_capability_refuses_mixed_or_future_capability_tuple(
    monkeypatch, field, value
):
    from dataclasses import replace
    from types import SimpleNamespace

    from backend.app import schema_capabilities
    from backend.app.normalizer_report_ddl import require_report_capability

    actual = schema_capabilities.capabilities_for("026_rd_seeded_submissions")
    monkeypatch.setattr(
        schema_capabilities,
        "capabilities_for",
        lambda _: replace(actual, **{field: value}),
    )
    session = SimpleNamespace(
        scalars=lambda _: SimpleNamespace(all=lambda: ["026_rd_seeded_submissions"])
    )
    with pytest.raises(ProcessError, match="numeric_report_schema_unsupported"):
        require_report_capability(session)

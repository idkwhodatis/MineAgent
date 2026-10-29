"""Actual upload/RD foundation for030; requires real DB, SDK and images.

Only the HTTP model endpoint may be doubled by the caller. This helper does not
preseed a dataset, fabricate a snapshot, or compute a score. Disclosure uses
explicit authenticated release requests after actual retained-byte validation.
"""

import json
from dataclasses import dataclass
from uuid import UUID

from rd_composed_profile_fixture import PROFILE
from rd_provider_fixture import SOURCE
from sqlalchemy import select

from backend.app.rdagent_provider_preflight import provider_budget_floor
from backend.app.rdagent_provider_submission_contracts import (
    RDProviderSubmissionRequest,
)

# Delivered as an actual model response through provider_server, not injected
# into host publication. Compute the explicit two-session return from actual retained upload inputs.
UPLOAD_RD_SOURCE = SOURCE.replace(
    '    rows.append((event_ns, listing["code"], float(current[0]["values"][field]), 0))',
    """    history = sorted(
        [v for v in observations if v["listing_code"] == listing["code"]],
        key=lambda v: v["session"],
    )
    if len(history) != 3 or history[-1] != current[0]:
        raise ValueError("Exactly two prior sessions plus current required")
    value = float(current[0]["values"][field]) / float(history[0]["values"][field]) - 1
    rows.append((event_ns, listing["code"], value, 0))""",
)
assert UPLOAD_RD_SOURCE != SOURCE


@dataclass(frozen=True)
class UploadRDFoundation:
    dataset_id: UUID
    source_run_id: UUID
    source_id: UUID
    factor_id: UUID
    factor_version_id: UUID
    snapshot_id: UUID
    analysis_id: UUID
    snapshot_release_receipt_ids: tuple[UUID, ...]
    analysis_release_receipt_ids: tuple[UUID, ...]
    request_canonical: str
    request_sha256: str
    basket_receipt_canonical: str

    @property
    def release_receipt_ids(self):
        """Only snapshot disclosure receipts belong in OE fixed_inputs."""
        return self.snapshot_release_receipt_ids


def request_from_basket(receipt, operator, owner_id):
    return RDProviderSubmissionRequest.model_validate(
        {
            "schema_version": "mineagent.rdagent-provider-submission.v1",
            "dataset_version_id": receipt["dataset"]["id"],
            "model_profile_id": operator.connection.id,
            "selected_steer_version_ids": [],
            "window": {"start": "2024-03-13", "end": "2024-03-14"},
            "input_fields": ["close"],
            "lookback_sessions": 2,
            "lag_sessions": 0,
            "task": {
                "name": "declared-upload-close-return",
                "description": "Compute the two-session close return from original declared upload bytes.",
                "formulation": "close[t]/close[t-2]-1",
                "factor_kind": "time_series",
                "output_unit": "return",
                "interpretation": "unsigned",
            },
            "objective": {
                "window": {"start": "2024-03-13", "end": "2024-03-14"},
                "label_horizon_sessions": 1,
                "metric": "rank_ic",
                "direction": "at_least",
                "threshold": 0.9,
                "minimum_defined_days": 1,
                "minimum_pairs": 2,
            },
            "source_policy": {"acknowledged_unverified": True},
            "max_iterations": 1,
            "budget": provider_budget_floor(
                operator.public_binding(owner_id), profile=PROFILE
            ).model_dump(mode="json"),
        }
    )


def create_upload_rd_snapshot(
    client,
    headers,
    *,
    other_headers,
    factory,
    settings,
    owner_id,
    operator,
    request_key,
    basket_receipt=None,
):
    """Normal authenticated admission then actual worker and retained-byte read.

    A basket receipt created by the real authenticated upload/data worker path
    may be supplied; normal preflight revalidates its owned retained bytes.
    Caller must run provider_server(sources=(UPLOAD_RD_SOURCE, ...)) and configure
    its actual SDK interpreter, original runtime pins and real worker settings.
    """
    from rd_composed_profile_fixture import assert_composed_controller

    from backend.app.execution_snapshot_history import (
        read_historical_execution_snapshot,
    )
    from backend.app.factor_analysis_history import read_historical_direct_analysis
    from backend.app.factor_analysis_models import (
        FactorDirectAnalysisIntent,
        FactorDirectAnalysisOutcome,
    )
    from backend.app.factor_models import FactorVersion
    from backend.app.models import ResearchRun
    from backend.app.rdagent_models import FactorGenerationSource
    from backend.app.rdagent_provider_models import RDProviderTransportReceipt
    from backend.app.research_broker_models import ResearchModelTransport
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
        ResearchProcessState,
    )
    from packages.factors.contracts import canonical_bytes
    from workers.research.dispatcher import dispatch_outbox
    from workers.research.queue import create_queue_app

    basket = basket_receipt or create_upload_basket(
        client, headers, factory=factory, settings=settings, owner_id=owner_id
    )
    request = request_from_basket(basket, operator, owner_id)
    checked = client.post(
        "/api/v1/engine-runs/preflight",
        headers=headers,
        json=request.model_dump(mode="json"),
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["can_submit"] is True, checked.text
    assert checked.json()["cutoff_count"] == 1
    exact = {
        **checked.json()["normalized_request"],
        "preflight_sha256": checked.json()["preflight_sha256"],
    }
    queued = client.post(
        "/api/v1/engine-runs",
        headers={**headers, "Idempotency-Key": request_key},
        json=exact,
    )
    assert queued.status_code == 201, queued.text
    run_id = UUID(queued.json()["run_id"])
    with factory.begin() as session:
        assert dispatch_outbox(session, settings=settings) == 1
    queue = create_queue_app(settings)
    with queue.open():
        queue.run_worker(
            queues=["research"],
            concurrency=1,
            wait=False,
            install_signal_handlers=False,
            delete_jobs="never",
        )
    with factory() as session:
        run = session.get(ResearchRun, run_id)
        assert run.owner_id == owner_id and run.status == "succeeded"
        bound = run.config["input"]["dataset"]
        assert bound["dataset_version_id"] == basket["dataset"]["id"]
        assert bound["content_sha256"] == basket["dataset"]["content_sha256"]
        assert bound["source_quality"] == "declared_source"
        source = session.scalars(
            select(FactorGenerationSource).filter_by(run_id=run_id, owner_id=owner_id)
        ).one()
        version = session.get(FactorVersion, source.factor_version_id)
        assert version.code == UPLOAD_RD_SOURCE
        outcome = session.scalars(
            select(FactorDirectAnalysisOutcome).filter_by(
                run_id=run_id, owner_id=owner_id
            )
        ).one()
        intent = session.get(FactorDirectAnalysisIntent, outcome.analysis_id)
        units = session.scalars(
            select(ResearchProcessAdmission).filter_by(run_id=run_id)
        ).all()
        assert sorted(u.role for u in units) == [
            "factor_cutoff_candidate",
            "factor_normalizer",
            "native_evaluator",
            "rd_orchestrator",
        ]
        assert all(
            session.get(ResearchProcessState, u.id).status == "stopped" for u in units
        )
        assert_composed_controller(session, run_id)
        transports = session.scalars(
            select(ResearchModelTransport).filter_by(run_id=run_id)
        ).all()
        sdk_rows = session.scalars(
            select(RDProviderTransportReceipt).filter_by(run_id=run_id)
        ).all()
        assert 4 < len(transports) <= 32 and all(
            t.state == "completed" for t in transports
        )
        assert len(sdk_rows) == 3 * len(transports)
        result = UploadRDFoundation(
            request.dataset_version_id,
            run_id,
            source.id,
            version.factor_id,
            version.id,
            intent.snapshot_id,
            intent.id,
            (),
            (),
            canonical_bytes(exact).decode(),
            request.sha256,
            canonical_bytes(basket).decode(),
        )
    snapshot = read_historical_execution_snapshot(
        factory, owner_id, result.snapshot_id, settings
    )
    analysis = read_historical_direct_analysis(
        factory, owner_id, result.analysis_id, settings
    )
    assert snapshot.factor_version_id == result.factor_version_id
    assert analysis.snapshot_id == snapshot.snapshot_id
    assert len(snapshot.rows) == 2
    import pytest

    assert sorted(row.value for row in snapshot.rows) == pytest.approx(
        [-0.02, 0.04], abs=1e-12, rel=0
    )
    assert all(row.value_state == "valid" for row in snapshot.rows)
    assert analysis.measurement.status == "passed"
    for private_path in (
        f"/api/v1/engine-runs/{run_id}/execution-snapshots/{result.snapshot_id}/values",
        f"/api/v1/engine-runs/{run_id}/analyses/{result.analysis_id}",
    ):
        denied = client.get(private_path, headers=headers)
        assert denied.status_code == 409, denied.text
        assert denied.json()["code"] == "engine_result_release_required"
        assert client.get(private_path, headers=other_headers).status_code == 404
    for release_path in (
        f"/api/v1/engine-runs/{run_id}/execution-snapshots/{result.snapshot_id}/release",
        f"/api/v1/engine-runs/{run_id}/analyses/{result.analysis_id}/release",
    ):
        assert client.post(release_path, headers=other_headers).status_code == 404
    releases = []
    for path, count in (
        (f"execution-snapshots/{result.snapshot_id}", 1),
        (f"analyses/{result.analysis_id}", 2),
    ):
        released = client.post(
            f"/api/v1/engine-runs/{run_id}/{path}/release", headers=headers
        )
        assert released.status_code == 200, released.text
        ids = tuple(UUID(value) for value in released.json()["exposure_ids"])
        assert len(ids) == count
        releases.extend(ids)
    assert len(set(releases)) == 3
    values = client.get(
        f"/api/v1/engine-runs/{run_id}/execution-snapshots/{result.snapshot_id}/values",
        headers=headers,
    )
    assert values.status_code == 200 and values.json()["count"] == 2, values.text
    assert values.json()["output_sha256"] == snapshot.output_sha256
    from dataclasses import replace

    return replace(
        result,
        snapshot_release_receipt_ids=tuple(releases[:1]),
        analysis_release_receipt_ids=tuple(releases[1:]),
    )


def create_upload_basket(client, headers, *, factory, settings, owner_id):
    """Real authenticated upload and data executor, never preseeded dataset rows."""
    import hashlib

    from backend.app.models import DataImport, DatasetVersion
    from workers.data.executor import execute

    ids, originals = [], {}
    for index, closes in enumerate(((100, 103, 104, 105), (100, 99, 98, 97))):
        symbol = f"UPLOADEVIDENCE{index}"
        raw = b"session_date,open,high,low,close,volume\n" + b"".join(
            f"2024-03-{day},100,{max(100, close) + 1},{min(100, close) - 1},{close},1000000\n".encode()
            for day, close in zip((11, 12, 13, 14), closes, strict=True)
        )
        mapping = {
            "name": f"Artificial uploaded research bytes {index}; not live data",
            "symbol": symbol,
            "market": "US",
            "mic": "XNYS",
            "currency": "USD",
            "timezone": "America/New_York",
            "calendar": "XNYS",
            "start_date": "2024-03-11",
            "end_date": "2024-03-15",
            "columns": {
                k: k for k in ("session_date", "open", "high", "low", "close", "volume")
            },
            "volume_unit": "shares",
            "unknown_flags": [],
            "price_semantics": {
                "native_mode": "raw",
                "split_basis": "unadjusted",
                "dividend_basis": "unadjusted",
            },
            "portable_declaration": {
                "kind": "user_declared_raw_no_actions",
                "acknowledged_unverified": True,
                "symbol": symbol,
                "mic": "XNYS",
                "start": "2024-03-11",
                "end": "2024-03-15",
                "source_reference": "Artificial test CSV posted by authenticated owner, not independently verified market data",
                "evidence_note": "Only an explicit no-actions assumption for this exact uploaded listing/range; no PIT certification.",
            },
        }
        uploaded = client.post(
            "/api/v1/data/uploads",
            headers={**headers, "Idempotency-Key": f"declared-upload-{index}"},
            data={"mapping": json.dumps(mapping)},
            files={"file": ("source.csv", raw, "text/csv")},
        )
        assert uploaded.status_code == 202, uploaded.text
        import_id = UUID(uploaded.json()["id"])
        execute(import_id, 1, settings=settings, factory=factory)
        with factory() as session:
            record = session.get(DataImport, import_id)
            assert record.status == "ready", record.error
            dataset = session.get(DatasetVersion, record.dataset_version_id)
            assert (
                dataset.owner_id == owner_id
                and dataset.source == "upload"
                and dataset.is_synthetic is False
            )
            assert dataset.manifest["schema_version"] == 2
            directory = settings.storage_root / dataset.storage_key
            assert (directory / "source.csv").read_bytes() == raw
            assert (
                dataset.manifest["upload_source"]["sha256"]
                == hashlib.sha256(raw).hexdigest()
            )
            ids.append(dataset.id)
            originals[str(dataset.id)] = raw
    body = {
        "source_dataset_ids": [str(v) for v in sorted(ids, key=str)],
        "acknowledge_unverified": True,
        "idempotency_key": "declared-upload-basket",
    }
    response = client.post(
        "/api/v1/data/declared-upload-baskets", headers=headers, json=body
    )
    assert response.status_code in (200, 201), response.text
    receipt = response.json()
    assert receipt["profile"] == "mineagent.declared-upload-basket.v1"
    assert receipt["data_evidence"] == "user_declaration_unverified"
    assert receipt["is_synthetic"] is receipt["point_in_time_proven"] is False
    assert receipt["research_submitted"] is receipt["result_released"] is False
    repeated = client.post(
        "/api/v1/data/declared-upload-baskets", headers=headers, json=body
    )
    assert repeated.status_code == response.status_code and repeated.json() == receipt
    with factory() as session:
        dataset = session.get(DatasetVersion, UUID(receipt["dataset"]["id"]))
        assert dataset.owner_id == owner_id and dataset.is_synthetic is False
        for ref in dataset.manifest["sources"]:
            retained = (
                settings.storage_root
                / dataset.storage_key
                / ref["artifact_prefix"]
                / "source.csv"
            )
            assert retained.read_bytes() == originals[ref["dataset_version_id"]]
    return receipt

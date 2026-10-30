"""Actual normal-demo/RD foundation for027; requires real DB, SDK and images.

Only the HTTP model endpoint may be doubled by the caller. This helper does not
preseed a dataset, fabricate a snapshot, or compute a score. Disclosure uses
explicit authenticated release requests after actual retained-byte validation.
"""

import json
from dataclasses import dataclass
from uuid import UUID

from normal_rd_fixture_diagnostic import report_normal_rd_state
from rd_composed_profile_fixture import PROFILE, assert_composed_controller
from rd_provider_fixture import SOURCE
from rd_rpc_diagnostic import diagnose_rd_queue
from sqlalchemy import select

from backend.app.rdagent_provider_preflight import provider_budget_floor
from backend.app.rdagent_provider_submission_contracts import (
    RDProviderSubmissionRequest,
)
from backend.app.structured_demo_routes import StructuredDemoReceipt

# Delivered as an actual model response through provider_server, not injected
# into host publication. Compute the exact returned demo preset from real input.
DEMO_RD_SOURCE = SOURCE.replace(
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
assert DEMO_RD_SOURCE != SOURCE


@dataclass(frozen=True)
class DemoRDFoundation:
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
    demo_receipt_canonical: str

    @property
    def release_receipt_ids(self):
        """Only snapshot disclosure receipts belong in OE fixed_inputs."""
        return self.snapshot_release_receipt_ids


def request_from_demo(receipt, operator, owner_id):
    demo = StructuredDemoReceipt.model_validate_json(json.dumps(receipt))
    preset = demo.rd_preset.model_dump(mode="json")
    for key in ("acknowledgement_required", "qualification"):
        preset.pop(key)
    return RDProviderSubmissionRequest.model_validate_json(
        json.dumps(
            {
                **preset,
                "schema_version": "mineagent.rdagent-provider-submission.v1",
                "dataset_version_id": str(demo.dataset.id),
                "model_profile_id": operator.connection.id,
                "selected_steer_version_ids": [],
                "source_policy": {"acknowledged_unverified": True},
                "budget": provider_budget_floor(
                    operator.public_binding(owner_id), profile=PROFILE
                ).model_dump(mode="json"),
            }
        )
    )


def create_demo_rd_snapshot(
    client,
    headers,
    *,
    factory,
    settings,
    owner_id,
    operator,
    request_key,
    demo_receipt=None,
):
    """Normal authenticated admission then actual worker and retained-byte read.

    A browser-created demo receipt may be supplied. The normal idempotent demo
    endpoint must return the identical owned receipt before it is used.
    Caller must run provider_server(sources=(DEMO_RD_SOURCE, ...)) and configure
    its actual SDK interpreter, original runtime pins and real worker settings.
    """
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

    response = client.post(
        "/api/v1/engine-runs/demo-dataset",
        headers=headers,
        json={"acknowledge_synthetic": True},
    )
    assert response.status_code == 200, response.text
    demo = response.json()
    if demo_receipt is not None:
        assert demo == demo_receipt, (
            "Browser demo receipt must match actual owned bytes"
        )
    request = request_from_demo(demo, operator, owner_id)
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
    with diagnose_rd_queue(factory, run_id), queue.open():
        queue.run_worker(
            queues=["research"],
            concurrency=1,
            wait=False,
            install_signal_handlers=False,
            delete_jobs="never",
        )
    with factory() as session:
        run = session.get(ResearchRun, run_id)
        report_normal_rd_state(session, run)
        assert run.owner_id == owner_id and run.status == "succeeded"
        assert_composed_controller(session, run_id)
        source = session.scalars(
            select(FactorGenerationSource).filter_by(run_id=run_id, owner_id=owner_id)
        ).one()
        version = session.get(FactorVersion, source.factor_version_id)
        assert version.code == DEMO_RD_SOURCE
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
        result = DemoRDFoundation(
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
            canonical_bytes(demo).decode(),
        )
    snapshot = read_historical_execution_snapshot(
        factory, owner_id, result.snapshot_id, settings
    )
    analysis = read_historical_direct_analysis(
        factory, owner_id, result.analysis_id, settings
    )
    assert snapshot.factor_version_id == result.factor_version_id
    assert analysis.snapshot_id == snapshot.snapshot_id
    assert len(snapshot.rows) == 3
    assert analysis.measurement.status == "passed"
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
    assert values.status_code == 200 and values.json()["count"] == 3, values.text
    assert values.json()["output_sha256"] == snapshot.output_sha256
    from dataclasses import replace

    return replace(
        result,
        snapshot_release_receipt_ids=tuple(releases[:1]),
        analysis_release_receipt_ids=tuple(releases[1:]),
    )

"""Required027 actual normal entry→generated code→MANU2 rejection→upstream feedback.

Two Docker roles and three actual SDK/localhost calls, no fabricated snapshot or
model metric. Collection alone is not this acceptance evidence.
"""

import json
import os
import secrets
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from normal_rd_fixture_diagnostic import report_normal_rd_state
from rd_composed_profile_fixture import PROFILE
from rd_configured_dataset_fixture import persist_configured_dataset
from rd_provider_fixture import provider_server
from rd_rpc_diagnostic import diagnose_rd_queue
from rd_submission_fixture import body, login, profile
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from test_backup_restore import drill_databases as drill_databases  # noqa: PLC0414

pytestmark = pytest.mark.postgres
REJECTED_SOURCE = 'raise RuntimeError("explicit synthetic candidate rejection")\n'


def test_normal_provider_actual_rejection_preserves_failed_feedback_without_snapshot(
    drill_databases, tmp_path, monkeypatch
):
    if os.environ.get("MINEAGENT_RD_REJECTION_FLOW_REQUIRED") != "1":
        pytest.fail("Required027 real rejection gate was not enabled", pytrace=False)
    import test_backup_restore as legacy

    from backend.app.core.security import get_password_hash
    from backend.app.execution_snapshot_models import ExecutionSnapshotPublication
    from backend.app.factor_analysis_models import FactorDirectAnalysisOutcome
    from backend.app.main import create_app
    from backend.app.models import ExecutionAttempt, ResearchRun, User
    from backend.app.rdagent_boundary_models import FactorGenerationBoundary
    from backend.app.rdagent_models import (
        FactorEvaluationOutcome,
        FactorGenerationOutcome,
        FactorGenerationSource,
        FactorUpstreamCall,
    )
    from backend.app.rdagent_numeric_failure_models import FactorNumericFailureFeedback
    from backend.app.rdagent_provider_models import RDProviderTransportReceipt
    from backend.app.rdagent_provider_preflight import provider_budget_floor
    from backend.app.rdagent_provider_profile import RDProviderServerRuntimeProfile
    from backend.app.research_broker_models import (
        ResearchBudgetReservation,
        ResearchBudgetSettlement,
        ResearchModelTransport,
    )
    from backend.app.research_object_models import ResearchObject
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
        ResearchProcessReceipt,
        ResearchProcessState,
    )
    from packages.rdagent_adapter.numeric_failure_codec import parse_failure_feedback
    from packages.rdagent_provider.contracts import DeclaredTokenPricing
    from packages.rdagent_provider.operator_profiles import RDOperatorModelProfile
    from workers.research.dispatcher import dispatch_outbox
    from workers.research.queue import create_queue_app

    engine = legacy._engine(drill_databases.source)
    try:
        legacy._upgrade(engine, "028_oe_structured_search")
        storage = tmp_path / "actual-rejection"
        storage.mkdir(mode=0o700)
        factory = sessionmaker(engine, expire_on_commit=False)
        password = secrets.token_urlsafe(32)
        user = User(
            id=uuid4(),
            email=uuid4().hex + "@example.com",
            hashed_password=get_password_hash(password),
            full_name="Actual rejected candidate",
        )
        with factory.begin() as session:
            session.add(user)
        base = profile(actual_images=True, controller_profile=PROFILE)
        runtime = RDProviderServerRuntimeProfile(
            controller_profile=PROFILE,
            runtime=base.runtime,
            qlib_installed_parent_image_id=base.qlib_installed_parent_image_id,
        )
        monkeypatch.setenv(
            "MINEAGENT_LOCAL_PROVIDER_TEST_SECRET", "synthetic-local-only"
        )
        sdk = Path(os.environ["MINEAGENT_PROVIDER_SDK_PYTHON"])
        assert sdk.is_absolute() and sdk.is_file()
        with provider_server(sources=(REJECTED_SOURCE,), rejection=True) as (
            endpoint,
            captured,
        ):
            operator = RDOperatorModelProfile(
                connection={
                    "id": "local-rejection-sdk",
                    "label": "Synthetic rejection provider",
                    "model": "local-chat",
                    "base_url": endpoint,
                    "secret_env": "MINEAGENT_LOCAL_PROVIDER_TEST_SECRET",
                    "owner_ids": (user.id,),
                    "temperature": 0,
                    "max_output_tokens": 4096,
                    "request_timeout_seconds": 8,
                    "max_request_bytes": 262144,
                    "max_response_bytes": 262144,
                    "synthetic": True,
                },
                embedding_model="local-embedding",
                embedding_dimensions=2,
                pricing=DeclaredTokenPricing(
                    chat_input_microusd_per_million=100,
                    chat_output_microusd_per_million=100,
                    embedding_microusd_per_million=100,
                ),
            )
            settings = legacy._note_settings(engine, storage).model_copy(
                update={
                    "rdagent_research_enabled": True,
                    "rdagent_provider_runtime_profile": runtime,
                    "rdagent_provider_profiles": (operator,),
                    "rdagent_synthetic_provider_enabled": True,
                    "rdagent_provider_python": sdk,
                    "lease_seconds": 120,
                    "heartbeat_seconds": 1,
                }
            )
            dataset, _, policy = persist_configured_dataset(factory, user.id, settings)
            declaration = body(dataset, policy).model_dump(mode="json")
            declaration.update(
                schema_version="mineagent.rdagent-provider-submission.v1",
                model_profile_id=operator.connection.id,
                selected_steer_version_ids=[],
                budget=provider_budget_floor(
                    operator.public_binding(user.id), profile=PROFILE
                ).model_dump(mode="json"),
            )
            declaration["task"].update(
                name="candidate-rejection-observation",
                description="Research cross-sectional price level; retain actual execution failures honestly.",
                formulation="close[t]",
                factor_kind="cross_sectional",
            )
            with TestClient(create_app(settings=settings, engine=engine)) as client:
                headers = login(client, SimpleNamespace(user=user, password=password))
                checked = client.post(
                    "/api/v1/engine-runs/preflight", headers=headers, json=declaration
                )
                assert checked.status_code == 200, checked.text
                verified = checked.json()
                assert verified["can_submit"], verified
                exact = {
                    **verified["normalized_request"],
                    "preflight_sha256": verified["preflight_sha256"],
                }
                key = uuid4().hex
                accepted = client.post(
                    "/api/v1/engine-runs",
                    headers={**headers, "Idempotency-Key": key},
                    json=exact,
                )
                assert accepted.status_code == 201, accepted.text
                rid = UUID(accepted.json()["run_id"])
                with factory.begin() as session:
                    assert dispatch_outbox(session, settings=settings) == 1
                queue = create_queue_app(settings)
                with diagnose_rd_queue(factory, rid), queue.open():
                    queue.run_worker(
                        queues=["research"],
                        concurrency=1,
                        wait=False,
                        install_signal_handlers=False,
                        delete_jobs="never",
                    )
                with factory() as session:

                    def rows(model):
                        return session.scalars(
                            select(model).filter_by(run_id=rid)
                        ).all()

                    run = session.get(ResearchRun, rid)
                    report_normal_rd_state(session, run)
                    assert run.status == "failed"
                    attempt = session.get(ExecutionAttempt, run.active_attempt_id)
                    assert attempt.sandbox_stopped is True
                    (source,) = rows(FactorGenerationSource)
                    (result,) = rows(FactorEvaluationOutcome)
                    (failure,) = rows(FactorNumericFailureFeedback)
                    assert (
                        result.status == "execution_failed"
                        and result.snapshot_id is None
                    )
                    assert not rows(ExecutionSnapshotPublication) and not rows(
                        FactorDirectAnalysisOutcome
                    )
                    assert not rows(FactorGenerationOutcome) and not rows(
                        FactorGenerationBoundary
                    )
                    assert run.config["controller_profile"] == PROFILE
                    units = rows(ResearchProcessAdmission)
                    assert sorted(u.role for u in units) == [
                        "factor_cutoff_candidate",
                        "rd_orchestrator",
                    ]
                    (controller,) = [u for u in units if u.role == "rd_orchestrator"]
                    assert controller.profile == PROFILE
                    controller_hold = session.get(
                        ResearchBudgetReservation, controller.reservation_id
                    )
                    assert controller_hold.amounts["wall_milliseconds"] == 120000
                    assert all(
                        session.get(ResearchProcessState, u.id).status == "stopped"
                        for u in units
                    )
                    rejection = session.get(
                        ResearchProcessReceipt, failure.rejection_receipt_id
                    )
                    assert (
                        rejection.kind == "rejection"
                        and rejection.admission_id == failure.candidate_admission_id
                    )
                    obj = session.get(ResearchObject, failure.feedback_object_id)
                    document = parse_failure_feedback(
                        (storage / obj.storage_key).read_bytes()
                    )
                    assert document.failure.source_id == source.id
                    assert (
                        document.failure.outcome == "child_nonzero"
                        and document.failure.child_exit_code != 0
                    )
                    assert document.objective_status == "not_evaluated"
                    assert (
                        json.loads(document.upstream_feedback)["final_decision"]
                        is False
                    )
                    assert sorted(
                        (c.phase, c.kind) for c in rows(FactorUpstreamCall)
                    ) == [
                        ("critique", "chat"),
                        ("final_decision", "chat"),
                        ("generation", "chat"),
                    ]
                    transports = rows(ResearchModelTransport)
                    assert len(transports) == 3
                    receipts = rows(RDProviderTransportReceipt)
                    assert len(receipts) == 9
                    for transport in transports:
                        held = session.get(
                            ResearchBudgetReservation, transport.reservation_id
                        )
                        charge = session.scalars(
                            select(ResearchBudgetSettlement).filter_by(
                                reservation_id=held.id
                            )
                        ).one()
                        assert (
                            held.state == "settled"
                            and charge.charge == held.amounts
                            and charge.outcome == "conservative_unknown"
                        )
                    assert any(
                        document.failure.feedback_text in json.dumps(req)
                        for _, req in captured
                    )
                    assert all(path == "/v1/chat/completions" for path, _ in captured)
                replay = client.post(
                    "/api/v1/engine-runs",
                    headers={**headers, "Idempotency-Key": key},
                    json=exact,
                )
                assert replay.status_code == 201 and replay.json()["run_id"] == str(rid)
    finally:
        engine.dispose()

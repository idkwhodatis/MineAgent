"""Required026 normal HTTP→outbox→worker→SDK localhost→real numeric gate.

No local execution claim: this case requires actual PostgreSQL and four pinned
images, an explicit installed SDK interpreter, and the real normal task queue.
"""

import json
import os
import secrets
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from rd_composed_profile_fixture import PROFILE, assert_composed_controller
from rd_configured_dataset_fixture import persist_configured_dataset
from rd_provider_fixture import DERIVED_SOURCE, GUIDANCE, SOURCE, provider_server
from rd_rpc_diagnostic import diagnose_rd_queue
from rd_submission_browser import create_queued_submission_browser
from rd_submission_fixture import body, login, profile
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from test_backup_restore import drill_databases as drill_databases  # noqa: PLC0414

pytestmark = pytest.mark.postgres


def test_normal_provider_generated_price_level_uses_frozen_steer_and_real_units(
    drill_databases, tmp_path, monkeypatch
):
    if os.environ.get("MINEAGENT_RD_PROVIDER_FLOW_REQUIRED") != "1":
        pytest.fail(
            "Explicit026 actual provider runtime gate is required", pytrace=False
        )
    import test_backup_restore as legacy

    from backend.app.core.security import get_password_hash
    from backend.app.execution_snapshot_history import (
        read_historical_execution_snapshot,
    )
    from backend.app.factor_analysis_models import (
        FactorDirectAnalysisIntent,
        FactorDirectAnalysisOutcome,
    )
    from backend.app.factor_models import FactorVersion
    from backend.app.main import create_app
    from backend.app.models import ResearchRun, User
    from backend.app.rdagent_models import FactorGenerationSource
    from backend.app.rdagent_provider_models import RDProviderTransportReceipt
    from backend.app.rdagent_provider_preflight import provider_budget_floor
    from backend.app.rdagent_provider_profile import RDProviderServerRuntimeProfile
    from backend.app.rdagent_provider_submission_contracts import (
        RDProviderSeedRequest,
        RDProviderSubmissionRequest,
    )
    from backend.app.rdagent_submission_models import FactorResearchSubmission
    from backend.app.rdagent_submission_receipts import read_request
    from backend.app.research_broker_models import (
        ResearchModelOperation,
        ResearchModelTransport,
    )
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
        ResearchProcessState,
    )
    from packages.rdagent_provider.contracts import DeclaredTokenPricing
    from packages.rdagent_provider.operator_profiles import RDOperatorModelProfile
    from workers.research.dispatcher import dispatch_outbox
    from workers.research.queue import create_queue_app

    sdk = Path(os.environ["MINEAGENT_PROVIDER_SDK_PYTHON"])
    assert sdk.is_absolute() and sdk.is_file()
    engine = legacy._engine(drill_databases.source)
    try:
        legacy._upgrade(engine, "027a_contract_repairs")
        storage = tmp_path / "provider-user-flow"
        storage.mkdir(mode=0o700)
        factory = sessionmaker(engine, expire_on_commit=False)
        password = secrets.token_urlsafe(32)
        user = User(
            id=uuid4(),
            email=uuid4().hex + "@example.com",
            hashed_password=get_password_hash(password),
            full_name="Actual model-backed RD",
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
        with provider_server(sources=(SOURCE, DERIVED_SOURCE)) as (endpoint, captured):
            operator = RDOperatorModelProfile(
                connection={
                    "id": "local-actual-sdk",
                    "label": "Synthetic HTTP provider",
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
            with TestClient(create_app(settings=settings, engine=engine)) as client:
                headers = login(client, SimpleNamespace(user=user, password=password))
                created = client.post(
                    "/api/v1/rd-steers",
                    headers=headers,
                    json={"kind": "hypothesis", "text": GUIDANCE},
                )
                assert created.status_code == 201, created.text
                steer = created.json()
                declaration = body(
                    dataset, policy, controller_profile=PROFILE
                ).model_dump(mode="json")
                declaration.update(
                    schema_version="mineagent.rdagent-provider-submission.v1",
                    model_profile_id=operator.connection.id,
                    selected_steer_version_ids=[steer["current"]["version_id"]],
                    budget=provider_budget_floor(
                        operator.public_binding(user.id), profile=PROFILE
                    ).model_dump(mode="json"),
                )
                declaration["task"] = {
                    "name": "cross-sectional-price-level",
                    "description": GUIDANCE,
                    "formulation": "close[t]",
                    "factor_kind": "cross_sectional",
                    "output_unit": "price",
                    "interpretation": "higher_preferred",
                    "explanation_origin": "task_declaration",
                }
                request = RDProviderSubmissionRequest.model_validate(declaration)
                create_queued_submission_browser(
                    settings,
                    engine,
                    SimpleNamespace(user=user, password=password),
                    dataset,
                    request,
                )
                with factory() as session:
                    saved = session.scalars(
                        select(FactorResearchSubmission).filter_by(
                            owner_id=user.id, request_sha256=request.sha256
                        )
                    ).one()
                    key, run_id = saved.idempotency_key, str(saved.run_id)
                    exact = {
                        **read_request(saved).model_dump(mode="json"),
                        "preflight_sha256": saved.preflight_sha256,
                    }
                    assert session.get(ResearchRun, saved.run_id).status == "queued"
                # Editing/disabling after acceptance must not invalidate queued input.
                edited = client.patch(
                    "/api/v1/rd-steers/" + steer["id"],
                    headers=headers,
                    json={
                        "kind": "hypothesis",
                        "text": "Future runs only: use a different factor.",
                        "expected_revision": steer["revision"],
                    },
                )
                assert edited.status_code == 200, edited.text
                disabled = client.post(
                    "/api/v1/rd-steers/" + steer["id"] + "/state",
                    headers=headers,
                    json={
                        "disabled": True,
                        "expected_revision": edited.json()["revision"],
                    },
                )
                assert disabled.status_code == 200, disabled.text
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
                from uuid import UUID

                rid = UUID(run_id)
                with factory() as session:
                    run = session.get(ResearchRun, rid)
                    assert run.status == "succeeded"
                    assert_composed_controller(session, rid)
                    saved = session.scalars(
                        select(FactorResearchSubmission).filter_by(run_id=rid)
                    ).one()
                    assert saved.request_sha256 == request.sha256
                    assert (
                        run.config["steer_snapshot"]["versions"][0]["text"] == GUIDANCE
                    )
                    source = session.scalars(
                        select(FactorGenerationSource).filter_by(run_id=rid)
                    ).one()
                    version = session.get(FactorVersion, source.factor_version_id)
                    assert version.code == SOURCE
                    parent_id, parent_factor, parent_ordinal = (
                        version.id,
                        version.factor_id,
                        version.ordinal,
                    )
                    parent_unit_ids = {
                        u.id
                        for u in session.scalars(
                            select(ResearchProcessAdmission).filter_by(run_id=rid)
                        )
                    }
                    measured = session.scalars(
                        select(FactorDirectAnalysisOutcome).filter_by(run_id=rid)
                    ).one()
                    assert measured.result["rank_ic"] == 1.0
                    assert measured.measurement["status"] == "passed"
                    parent_snapshot_id = session.get(
                        FactorDirectAnalysisIntent, measured.analysis_id
                    ).snapshot_id
                    units = session.scalars(
                        select(ResearchProcessAdmission).filter_by(run_id=rid)
                    ).all()
                    assert sorted(u.role for u in units) == [
                        "factor_cutoff_candidate",
                        "factor_normalizer",
                        "native_evaluator",
                        "rd_orchestrator",
                    ]
                    assert all(
                        session.get(ResearchProcessState, u.id).status == "stopped"
                        for u in units
                    )
                    operations = session.scalars(
                        select(ResearchModelOperation).filter_by(run_id=rid)
                    ).all()
                    transports = session.scalars(
                        select(ResearchModelTransport).filter_by(run_id=rid)
                    ).all()
                    sdk_rows = session.scalars(
                        select(RDProviderTransportReceipt).filter_by(run_id=rid)
                    ).all()
                    assert 4 < len(operations) <= 32 and len(transports) == len(
                        operations
                    )
                    assert all(t.state == "completed" for t in transports)
                    assert len(sdk_rows) == 3 * len(transports)
                first = next(
                    request
                    for path, request in captured
                    if path == "/v1/chat/completions"
                )
                prompt = "\n".join(m["content"] for m in first["messages"])
                assert GUIDANCE in prompt and steer["current"]["version_id"] in prompt
                assert "Future runs only" not in prompt
                assert any(path == "/v1/embeddings" for path, _ in captured)
                settings.rdagent_synthetic_provider_enabled = False
                replay = client.post(
                    "/api/v1/engine-runs",
                    headers={**headers, "Idempotency-Key": key},
                    json=exact,
                )
                assert replay.status_code == 201 and replay.json()["replayed"] is True
                assert replay.json()["run_id"] == run_id
                # A second normal browser submission derives from the genuinely
                # computed first version, never a seeded fixture or posthoc edit.
                settings.rdagent_synthetic_provider_enabled = True
                derived_guidance = "Derive from the retained parent: square current close[t]; do not compute momentum."
                selected = client.post(
                    "/api/v1/rd-steers",
                    headers=headers,
                    json={"kind": "hypothesis", "text": derived_guidance},
                )
                assert selected.status_code == 201, selected.text
                child_declaration = {
                    **declaration,
                    "schema_version": "mineagent.rdagent-provider-seeded-submission.v1",
                    "parent_factor_version_id": str(parent_id),
                    "selected_steer_version_ids": [
                        selected.json()["current"]["version_id"]
                    ],
                    "task": {
                        **declaration["task"],
                        "name": "squared-price-level",
                        "description": derived_guidance,
                        "formulation": "close[t] ** 2",
                        "output_unit": "price_squared",
                    },
                }
                child_request = RDProviderSeedRequest.model_validate(child_declaration)
                create_queued_submission_browser(
                    settings,
                    engine,
                    SimpleNamespace(user=user, password=password),
                    dataset,
                    child_request,
                )
                from backend.app.rdagent_seed_models import FactorSeededSubmission

                with factory() as session:
                    child_receipt = session.scalars(
                        select(FactorSeededSubmission).filter_by(
                            owner_id=user.id, request_sha256=child_request.sha256
                        )
                    ).one()
                    child_run_id = child_receipt.run_id
                    assert child_receipt.parent_factor_version_id == parent_id
                    assert child_receipt.parent_factor_id == parent_factor
                    assert session.get(ResearchRun, child_run_id).status == "queued"
                with factory.begin() as session:
                    assert dispatch_outbox(session, settings=settings) == 1
                child_queue = create_queue_app(settings)
                with diagnose_rd_queue(factory, child_run_id), child_queue.open():
                    child_queue.run_worker(
                        queues=["research"],
                        concurrency=1,
                        wait=False,
                        install_signal_handlers=False,
                        delete_jobs="never",
                    )
                with factory() as session:
                    assert session.get(ResearchRun, child_run_id).status == "succeeded"
                    assert_composed_controller(session, child_run_id)
                    child_source = session.scalars(
                        select(FactorGenerationSource).filter_by(run_id=child_run_id)
                    ).one()
                    child_version = session.get(
                        FactorVersion, child_source.factor_version_id
                    )
                    assert child_version.factor_id == parent_factor
                    assert child_version.parent_version_id == parent_id
                    assert child_version.source_provenance["run_id"] == str(
                        child_run_id
                    )
                    assert child_version.source_provenance["start_id"] == str(
                        child_source.start_id
                    )
                    assert child_version.source_provenance["call_id"] == str(
                        child_source.call_id
                    )
                    assert child_version.ordinal == parent_ordinal + 1
                    assert (
                        child_version.code == DERIVED_SOURCE
                        and child_version.code != SOURCE
                    )
                    assert session.get(FactorVersion, parent_id).code == SOURCE
                    child_measured = session.scalars(
                        select(FactorDirectAnalysisOutcome).filter_by(
                            run_id=child_run_id
                        )
                    ).one()
                    assert child_measured.measurement["status"] == "passed"
                    assert child_measured.result["rank_ic"] == 1.0
                    child_snapshot_id = session.get(
                        FactorDirectAnalysisIntent, child_measured.analysis_id
                    ).snapshot_id
                    child_units = session.scalars(
                        select(ResearchProcessAdmission).filter_by(run_id=child_run_id)
                    ).all()
                    assert sorted(u.role for u in child_units) == [
                        "factor_cutoff_candidate",
                        "factor_normalizer",
                        "native_evaluator",
                        "rd_orchestrator",
                    ]
                    assert not parent_unit_ids.intersection(u.id for u in child_units)
                    assert all(
                        session.get(ResearchProcessState, u.id).status == "stopped"
                        for u in child_units
                    )
                    child_transports = session.scalars(
                        select(ResearchModelTransport).filter_by(run_id=child_run_id)
                    ).all()
                    child_sdk = session.scalars(
                        select(RDProviderTransportReceipt).filter_by(
                            run_id=child_run_id
                        )
                    ).all()
                    assert 4 < len(child_transports) <= 32 and all(
                        t.state == "completed" for t in child_transports
                    )
                    assert len(child_sdk) == 3 * len(child_transports)
                parent_snapshot = read_historical_execution_snapshot(
                    factory, user.id, parent_snapshot_id, settings
                )
                child_snapshot = read_historical_execution_snapshot(
                    factory, user.id, child_snapshot_id, settings
                )
                assert child_snapshot.snapshot_id != parent_snapshot.snapshot_id
                actual_parent = {
                    row.listing_id: row.value for row in parent_snapshot.rows
                }
                actual_child = {
                    row.listing_id: row.value for row in child_snapshot.rows
                }
                assert sorted(actual_parent.values()) == [98.0, 102.0, 104.0]
                assert actual_child == {
                    listing: value**2 for listing, value in actual_parent.items()
                }
                chats = [
                    request
                    for path, request in captured
                    if path == "/v1/chat/completions"
                ]
                assert len(chats) == 8
                seeded_prompt = "\n".join(m["content"] for m in chats[4]["messages"])
                assert (
                    str(parent_id) in seeded_prompt
                    and json.dumps(SOURCE) in seeded_prompt
                )
                assert derived_guidance in seeded_prompt
    finally:
        engine.dispose()

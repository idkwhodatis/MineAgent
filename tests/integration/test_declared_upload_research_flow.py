"""Actual schema2 uploads→schema3 basket→RD→released snapshot→normal structured OE.

Requires actual PostgreSQL, pinned runtime images and SDK. Only the local HTTP model endpoint is doubled; raw CSVs are explicitly artificial
test inputs uploaded through normal endpoints, not synthetic market-profile evidence.
No acceptance initializer, preseeded dataset or precomputed score is admitted.
"""

import json
import os
import secrets
from datetime import date
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from test_backup_restore import drill_databases as drill_databases  # noqa: PLC0414

pytestmark = pytest.mark.postgres


@pytest.fixture
def settings(settings):
    """Explicit opt-in for this upload publication acceptance module only."""
    return settings.model_copy(update={"factor_library_enabled": True})


def test_actual_upload_api_and_data_worker_preserve_declared_originals(
    api, owner_headers, owner, migrated_engine, settings
):
    from declared_upload_foundation import create_upload_basket

    factory = sessionmaker(migrated_engine, expire_on_commit=False)
    receipt = create_upload_basket(
        api, owner_headers, factory=factory, settings=settings, owner_id=owner.user.id
    )
    assert receipt["dataset"]["is_synthetic"] is False


def test_actual_declared_upload_rd_release_and_normal_structured_oe(
    drill_databases, tmp_path, monkeypatch
):
    if os.environ.get("MINEAGENT_DECLARED_UPLOAD_FLOW_REQUIRED") != "1":
        pytest.fail(
            "Explicit actual declared upload runtime gate is required", pytrace=False
        )
    import test_backup_restore as legacy
    from oe_structured_restore_replay import assert_normal_restore

    engine = legacy._engine(drill_databases.source)
    try:
        legacy._upgrade(engine, "030_declared_upload_basket")
        storage = tmp_path / "declared-upload-flow"
        storage.mkdir(mode=0o700)
        settings = legacy._note_settings(engine, storage)
        completed = _run_upload_flow(engine, settings, monkeypatch)
        assert_normal_restore(
            drill_databases,
            tmp_path,
            engine,
            completed.settings,
            account=completed.account,
            other=completed.other,
            run_id=completed.run_id,
            execution_ids=completed.execution_ids,
            responses=completed.disclosed.responses,
            receipts=completed.disclosed.receipts,
            expected_revision="030_declared_upload_basket",
        )
    finally:
        engine.dispose()


def _run_upload_flow(migrated_engine, settings, monkeypatch):
    from declared_upload_foundation import UPLOAD_RD_SOURCE, create_upload_rd_snapshot
    from oe_structured_browser import (
        assert_oe_results_in_browser,
        assert_oe_run_in_browser,
        create_oe_submission_in_browser,
    )
    from oe_structured_provider_fixture import provider_server as oe_server
    from rd_composed_profile_fixture import PROFILE
    from rd_provider_fixture import provider_server as rd_server
    from rd_submission_fixture import login, profile

    from backend.app.core.security import get_password_hash
    from backend.app.main import create_app
    from backend.app.models import ResearchRun, User
    from backend.app.oe_structured_models import (
        OEStructuredCandidate,
        OEStructuredEngineOutcome,
        OEStructuredExecution,
        OEStructuredProviderExchange,
    )
    from backend.app.oe_structured_operational_models import (
        OEStructuredProviderReceipt,
        OEStructuredProviderTransport,
    )
    from backend.app.oe_structured_provider_submission import (
        ProviderStructuredSearchRequest,
    )
    from backend.app.oe_structured_server_schemas import OEStructuredServerProfile
    from backend.app.rdagent_provider_profile import RDProviderServerRuntimeProfile
    from backend.app.research_broker_models import ResearchBudgetControl
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
        ResearchProcessState,
    )
    from packages.factors.contracts import canonical_bytes
    from packages.openevolve_structured.provider_operator import OEOperatorChatProfile
    from packages.rdagent_provider.operator_profiles import RDOperatorModelProfile
    from workers.research.dispatcher import dispatch_outbox
    from workers.research.queue import create_queue_app

    factory = sessionmaker(migrated_engine, expire_on_commit=False)
    password = secrets.token_urlsafe(32)
    user = User(
        id=uuid4(),
        email=uuid4().hex + "@example.com",
        hashed_password=get_password_hash(password),
        full_name="Normal structured search",
    )
    other_password = secrets.token_urlsafe(32)
    other_user = User(
        id=uuid4(),
        email=uuid4().hex + "@example.com",
        hashed_password=get_password_hash(other_password),
        full_name="Other normal owner",
    )
    account, other = (
        NS(user=user, password=password),
        NS(user=other_user, password=other_password),
    )
    with factory.begin() as session:
        session.add_all((user, other_user))
    monkeypatch.setenv("MINEAGENT_NORMAL_FLOW_SECRET", "synthetic-local-only")
    rd_pins = profile(actual_images=True, controller_profile=PROFILE)
    oe_runtime = OEStructuredServerProfile.model_validate_json(
        os.environ["MINEAGENT_OE_STRUCTURED_RUNTIME_PROFILE"]
    )
    with (
        rd_server(sources=(UPLOAD_RD_SOURCE,)) as (rd_url, rd_calls),
        oe_server() as (oe_url, oe_calls, reply),
    ):

        def connection(name, url):
            return {
                "id": name,
                "label": "Synthetic HTTP provider",
                "model": "local-chat",
                "base_url": url,
                "secret_env": "MINEAGENT_NORMAL_FLOW_SECRET",
                "owner_ids": (user.id,),
                "temperature": 0,
                "max_output_tokens": 4096,
                "request_timeout_seconds": 8,
                "max_request_bytes": 262144,
                "max_response_bytes": 262144,
                "synthetic": True,
            }

        rd_operator = RDOperatorModelProfile(
            connection=connection("local-rd", rd_url),
            embedding_model="local-embedding",
            embedding_dimensions=2,
            pricing={
                "chat_input_microusd_per_million": 100,
                "chat_output_microusd_per_million": 100,
                "embedding_microusd_per_million": 100,
            },
        )
        oe_operator = OEOperatorChatProfile(
            connection=connection("local-oe", oe_url),
            pricing={
                "status": "operator_declared",
                "input_microusd_per_million": 100,
                "output_microusd_per_million": 100,
            },
        )
        configured = settings.model_copy(
            update={
                "factor_library_enabled": True,
                "rdagent_research_enabled": True,
                "rdagent_provider_runtime_profile": RDProviderServerRuntimeProfile(
                    controller_profile=PROFILE,
                    runtime=rd_pins.runtime,
                    qlib_installed_parent_image_id=rd_pins.qlib_installed_parent_image_id,
                ),
                "rdagent_provider_profiles": (rd_operator,),
                "rdagent_synthetic_provider_enabled": True,
                "rdagent_provider_python": os.environ["MINEAGENT_PROVIDER_SDK_PYTHON"],
                "oe_structured_research_enabled": True,
                "oe_structured_runtime_profile": oe_runtime,
                "oe_structured_provider_profiles": (oe_operator,),
                "oe_structured_synthetic_provider_enabled": True,
                "oe_structured_provider_python": os.environ[
                    "MINEAGENT_PROVIDER_SDK_PYTHON"
                ],
                "lease_seconds": 120,
                "heartbeat_seconds": 1,
            }
        )
        with TestClient(
            create_app(settings=configured, engine=migrated_engine)
        ) as client:
            headers = login(client, NS(user=user, password=password))
            foundation = create_upload_rd_snapshot(
                client,
                headers,
                other_headers=login(client, other),
                factory=factory,
                settings=configured,
                owner_id=user.id,
                operator=rd_operator,
                request_key="upload-normal-rd",
            )
            request = ProviderStructuredSearchRequest(
                snapshot_id=foundation.snapshot_id,
                seed_parameters={
                    "threshold": 0.0,
                    "top_k": 1,
                    "maximum_weight": 0.5,
                    "maximum_gross_weight": 1.0,
                },
                objective={
                    "window": {"start": date(2024, 3, 13), "end": date(2024, 3, 15)},
                    "initial_cash": 10000.0,
                },
                budget={},
                maximum_mutation_slots=1,
                model_profile_id="local-oe",
                model_binding_sha256=oe_operator.public_binding(user.id).sha256,
            )
            path = "/api/v1/engine-runs/structured-search"
            quote = client.post(
                path + "/preflight",
                headers=headers,
                json=request.model_dump(mode="json"),
            )
            assert quote.status_code == 200, quote.text
            assert quote.json()["data_eligible"] is True, quote.text
            request = ProviderStructuredSearchRequest.model_validate_json(
                json.dumps(
                    {
                        **request.model_dump(mode="json"),
                        "budget": quote.json()["required_budget"],
                    }
                )
            )
            checked = client.post(
                path + "/preflight",
                headers=headers,
                json=request.model_dump(mode="json"),
            )
            assert (
                checked.status_code == 200 and checked.json()["can_submit"] is True
            ), checked.text
            observed = create_oe_submission_in_browser(
                migrated_engine, configured, account, request
            )
            body = observed.body.model_dump(mode="json")
            replay = client.post(
                path,
                headers={**headers, "Idempotency-Key": observed.idempotency_key},
                json=body,
            )
            assert (
                replay.status_code == 201
                and replay.json() == observed.receipt.model_dump(mode="json")
            ), replay.text
            rid = observed.receipt.run_id
            with factory() as session:
                run = session.get(ResearchRun, rid)
                fixed = run.config["fixed_inputs"]
                assert (
                    fixed["schema_version"]
                    == "mineagent.oe-structured-retained-inputs.v2"
                )
                assert fixed["market_qualification"] == "user_declaration_unverified"
                basket_receipt = json.loads(foundation.basket_receipt_canonical)
                assert fixed["dataset_version_id"] == basket_receipt["dataset"]["id"]
                assert (
                    fixed["dataset_sha256"]
                    == basket_receipt["dataset"]["content_sha256"]
                )
                seed = run.config["search"]["domain"]["seed_program"]
                child = json.loads(json.dumps(seed))
                child["maximum_weight"] = 0.25
                reply["candidate"] = {
                    "schema_version": "mineagent.oe-structured-candidate.v1",
                    "program": child,
                }
            with factory.begin() as session:
                assert dispatch_outbox(session, settings=configured) == 1
            queue = create_queue_app(configured)
            with queue.open():
                queue.run_worker(
                    queues=["research"],
                    concurrency=1,
                    wait=False,
                    install_signal_handlers=False,
                    delete_jobs="never",
                )
            with factory() as session:
                run = session.get(ResearchRun, rid)
                assert run.status == "succeeded"
                candidates = session.scalars(
                    select(OEStructuredCandidate)
                    .filter_by(run_id=rid)
                    .order_by(OEStructuredCandidate.ordinal)
                ).all()
                assert (
                    len(candidates) == 2
                    and candidates[1].parent_candidate_id == candidates[0].id
                )
                assert candidates[1].candidate_canonical.encode() == canonical_bytes(
                    reply["candidate"]
                )
                assert [c.origin for c in candidates] == [
                    "authenticated_user_seed",
                    "model_proposal",
                ]
                executions = session.scalars(
                    select(OEStructuredExecution).filter_by(run_id=rid)
                ).all()
                outcomes = session.scalars(
                    select(OEStructuredEngineOutcome).filter_by(run_id=rid)
                ).all()
                assert len(executions) == len(outcomes) == 2
                assert {e.snapshot_id for e in executions} == {foundation.snapshot_id}
                assert {o.execution_id for o in outcomes} == {e.id for e in executions}
                units = session.scalars(
                    select(ResearchProcessAdmission).filter_by(run_id=rid)
                ).all()
                assert len(units) == 3
                assert all(
                    session.get(ResearchProcessState, u.id).status == "stopped"
                    for u in units
                )
                assert (
                    len(
                        session.scalars(
                            select(OEStructuredProviderTransport).filter_by(run_id=rid)
                        ).all()
                    )
                    == 1
                )
                assert (
                    len(
                        session.scalars(
                            select(OEStructuredProviderReceipt).filter_by(run_id=rid)
                        ).all()
                    )
                    == 3
                )
                assert (
                    len(
                        session.scalars(
                            select(OEStructuredProviderExchange).filter_by(run_id=rid)
                        ).all()
                    )
                    == 1
                )
                budget = session.scalars(
                    select(ResearchBudgetControl).filter_by(
                        run_id=rid, owner_id=user.id
                    )
                ).one()
                assert all(v == 0 for v in budget.reserved.values())
            assert len(oe_calls) == 1 and rd_calls
            assert (
                canonical_bytes(
                    {
                        "schema_version": "mineagent.oe-structured-candidate.v1",
                        "program": seed,
                    }
                ).decode()
                in oe_calls[0]["messages"][1]["content"]
            )
            visible = client.get(path + "/runs/" + str(rid), headers=headers)
            assert (
                visible.status_code == 200 and visible.json()["status"] == "succeeded"
            )

            assert_oe_run_in_browser(migrated_engine, configured, account, rid, other)
            from oe_structured_release_replay import (
                assert_unreleased,
                capture_released,
            )

            ordered_executions = tuple(
                next(e.id for e in executions if e.candidate_id == candidate.id)
                for candidate in candidates
            )
            other_headers = login(client, other)
            assert_unreleased(client, headers, other_headers, rid, ordered_executions)
            # First disclosures must be actual explicit user browser actions.
            assert_oe_results_in_browser(
                migrated_engine, configured, account, rid, ordered_executions, other
            )
            disclosed = capture_released(
                client, headers, other_headers, rid, ordered_executions
            )
            assert len(oe_calls) == 1  # Disclosure never asks the model again.
            return NS(
                settings=configured,
                account=account,
                other=other,
                run_id=rid,
                execution_ids=ordered_executions,
                disclosed=disclosed,
            )

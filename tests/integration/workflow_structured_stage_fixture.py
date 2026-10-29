"""Actual031 HTTP/workflow/RD/OE bridge fixture; only localhost provider is doubled.

No successful child, checkpoint, consumption, terminal, release or score row is
inserted. Existing synthetic retained marketdata is a declared fixture, not a
real-data or PIT claim. The successful root is created through the actual browser; control-only roots use HTTP.
"""

import json
import os
import secrets
from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker


def drain(factory, settings):
    from workers.research.dispatcher import dispatch_outbox
    from workers.research.queue import create_queue_app

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


def command(client, headers, workflow_id, action, *, revision=None):
    path = f"/api/v1/research-workflows/{workflow_id}"
    current = client.get(path, headers=headers)
    assert current.status_code == 200, current.text
    return client.post(
        path + "/commands",
        headers=headers,
        json={
            "schema_version": "mineagent.workflow-command.v1",
            "workflow_id": str(workflow_id),
            "expected_revision": current.json()["revision"]
            if revision is None
            else revision,
            "idempotency_key": uuid4().hex,
            "action": action,
        },
    )


@contextmanager
def actual_environment(engine, settings, monkeypatch):
    from oe_structured_provider_fixture import provider_server as oe_server
    from rd_composed_profile_fixture import PROFILE
    from rd_configured_dataset_fixture import persist_configured_dataset
    from rd_provider_fixture import SOURCE
    from rd_provider_fixture import provider_server as rd_server
    from rd_submission_fixture import login, profile

    from backend.app.core.security import get_password_hash
    from backend.app.main import create_app
    from backend.app.models import User
    from backend.app.oe_structured_server_schemas import OEStructuredServerProfile
    from backend.app.rdagent_provider_profile import RDProviderServerRuntimeProfile
    from packages.openevolve_structured.provider_operator import OEOperatorChatProfile
    from packages.rdagent_provider.operator_profiles import RDOperatorModelProfile

    factory = sessionmaker(engine, expire_on_commit=False)
    accounts = []
    with factory.begin() as session:
        for label in ("Actual workflow bridge", "Foreign workflow owner"):
            password = secrets.token_urlsafe(32)
            user = User(
                id=uuid4(),
                email=uuid4().hex + "@example.com",
                hashed_password=get_password_hash(password),
                full_name=label,
            )
            session.add(user)
            accounts.append(NS(user=user, password=password))
    account, other = accounts
    monkeypatch.setenv("MINEAGENT_WORKFLOW_OE_SECRET", "synthetic-local-only")
    rd_pins = profile(actual_images=True, controller_profile=PROFILE)
    oe_runtime = OEStructuredServerProfile.model_validate_json(
        os.environ["MINEAGENT_OE_STRUCTURED_RUNTIME_PROFILE"]
    )
    with (
        rd_server(sources=(SOURCE,)) as (rd_url, rd_calls),
        oe_server() as (oe_url, oe_calls, reply),
    ):

        def connection(name, url):
            return {
                "id": name,
                "label": "Synthetic HTTP provider",
                "model": "local-chat",
                "base_url": url,
                "secret_env": "MINEAGENT_WORKFLOW_OE_SECRET",
                "owner_ids": (account.user.id,),
                "temperature": 0,
                "max_output_tokens": 4096,
                "request_timeout_seconds": 8,
                "max_request_bytes": 262144,
                "max_response_bytes": 262144,
                "synthetic": True,
            }

        rd_operator = RDOperatorModelProfile(
            connection=connection("workflow-rd", rd_url),
            embedding_model="local-embedding",
            embedding_dimensions=2,
            pricing={
                "chat_input_microusd_per_million": 100,
                "chat_output_microusd_per_million": 100,
                "embedding_microusd_per_million": 100,
            },
        )
        oe_operator = OEOperatorChatProfile(
            connection=connection("workflow-oe", oe_url),
            pricing={
                "status": "operator_declared",
                "input_microusd_per_million": 100,
                "output_microusd_per_million": 100,
            },
        )
        configured = settings.model_copy(
            update={
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
        dataset, _, policy = persist_configured_dataset(
            factory, account.user.id, configured
        )
        with TestClient(create_app(settings=configured, engine=engine)) as client:
            yield NS(
                factory=factory,
                settings=configured,
                account=account,
                other=other,
                client=client,
                headers=login(client, account),
                foreign=login(client, other),
                dataset=dataset,
                policy=policy,
                rd_operator=rd_operator,
                oe_operator=oe_operator,
                rd_calls=rd_calls,
                oe_calls=oe_calls,
                reply=reply,
            )


def create_workflow(
    env, *, mode="continuous", browser_engine=None, browser_tmp_path=None
):
    from rd_composed_profile_fixture import PROFILE
    from rd_provider_fixture import GUIDANCE
    from rd_submission_fixture import body

    from backend.app.oe_structured_preflight import budget_floor
    from backend.app.rdagent_provider_preflight import provider_budget_floor
    from backend.app.research_workflow_structured_schemas import (
        WorkflowStructuredChoice,
    )
    from packages.research_runtime.budget_contracts import BudgetVector

    binding = env.oe_operator.public_binding(env.account.user.id)
    choice = WorkflowStructuredChoice.model_validate_json(
        json.dumps(
            {
                "seed_parameters": {
                    "threshold": 0.0,
                    "top_k": 1,
                    "maximum_weight": 0.5,
                    "maximum_gross_weight": 1.0,
                },
                "objective": {
                    "window": {"start": "2024-03-13", "end": "2024-03-15"},
                    "initial_cash": 10000.0,
                },
                "budget": {},
                "maximum_mutation_slots": 1,
                "model_profile_id": env.oe_operator.connection.id,
                "model_binding_sha256": binding.sha256,
            }
        )
    )
    oe_budget = budget_floor(choice, binding)
    rd_budget = provider_budget_floor(
        env.rd_operator.public_binding(env.account.user.id), profile=PROFILE
    )
    research = body(env.dataset, env.policy).model_dump(mode="json")
    research.update(
        schema_version="mineagent.rdagent-provider-submission.v1",
        model_profile_id=env.rd_operator.connection.id,
        selected_steer_version_ids=[],
        budget=rd_budget.model_dump(mode="json"),
        task={
            "name": "workflow-price-level",
            "description": GUIDANCE,
            "formulation": "close[t]",
            "factor_kind": "cross_sectional",
            "output_unit": "price",
            "interpretation": "higher_preferred",
            "explanation_origin": "task_declaration",
        },
    )
    steer = env.client.post(
        "/api/v1/rd-steers",
        headers=env.headers,
        json={"kind": "hypothesis", "text": GUIDANCE},
    )
    assert steer.status_code == 201, steer.text
    options = env.client.get(
        "/api/v1/research-workflow-inputs/manual", headers=env.headers
    )
    assert options.status_code == 200, options.text
    ref = next(
        v["ref"]
        for v in options.json()["items"]
        if v["ref"]["version_id"] == steer.json()["current"]["version_id"]
    )
    request = {
        "schema_version": "mineagent.workflow-public-request.v2",
        "research": research,
        "mode": mode,
        "maximum_iterations": 1,
        "budget_ceiling": rd_budget.add(oe_budget)
        .add(BudgetVector())
        .model_dump(mode="json"),
        "refs": [ref],
        "external_definition": None,
        "allow_workflow_development_feedback_release": False,
        "structured": {
            **choice.model_dump(mode="json"),
            "budget": oe_budget.model_dump(mode="json"),
        },
    }
    path = "/api/v1/research-workflows/structured"
    if browser_engine is not None:
        from workflow_history_browser import assert_actual_workflow_creation

        assert browser_tmp_path is not None
        observed = assert_actual_workflow_creation(
            browser_engine, env.settings, env.account, request, None, browser_tmp_path
        )
        # The browser's exact typed envelope is replayed, never a second request.
        replay = env.client.post(path, headers=env.headers, json=observed["envelope"])
        assert replay.status_code == 201, replay.text
        assert replay.json() == observed["receipt"]
        wid = UUID(observed["receipt"]["workflow_id"])
        no_engine_child(env.factory, wid)
        return wid
    assert browser_tmp_path is None
    quote = env.client.post(path + "/preflight", headers=env.headers, json=request)
    assert quote.status_code == 200 and quote.json()["can_create"] is True, quote.text
    envelope = {
        "schema_version": "mineagent.workflow-public-submit.v2",
        "request": request,
        "idempotency_key": uuid4().hex,
        "preflight_sha256": quote.json()["preflight_sha256"],
    }
    accepted = env.client.post(path, headers=env.headers, json=envelope)
    assert accepted.status_code == 201, accepted.text
    replay = env.client.post(path, headers=env.headers, json=envelope)
    assert replay.status_code == 201 and replay.json() == accepted.json(), replay.text
    return UUID(accepted.json()["workflow_id"])


def no_engine_child(factory, workflow_id):
    from backend.app.research_workflow_models import ResearchWorkflowStage

    with factory() as session:
        assert not session.scalars(
            select(ResearchWorkflowStage).filter_by(workflow_id=workflow_id)
        ).all()


def run_bridge(env, engine, tmp_path):
    from backend.app.execution_snapshot_history import historical_session
    from backend.app.models import ResearchRun
    from backend.app.oe_structured_models import (
        OEStructuredCandidate,
        OEStructuredExecution,
    )
    from backend.app.oe_workflow_models import OEWorkflowSubmission
    from backend.app.research_workflow_models import (
        ResearchWorkflow,
        ResearchWorkflowCheckpoint,
        ResearchWorkflowModelConsumption,
        ResearchWorkflowStage,
    )
    from backend.app.research_workflow_oe_terminal import read_oe_terminal_in_session
    from packages.factors.contracts import canonical_bytes
    from packages.openevolve_structured.workflow_prompt import (
        validate_workflow_rendered,
    )
    from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig
    from packages.research_workflow.rd_checkpoint import RDStageCheckpoint
    from workers.research.workflow_tick import drive_one

    wid = create_workflow(env, browser_engine=engine, browser_tmp_path=tmp_path)
    owner = env.account.user.id
    assert (
        drive_one(env.factory, env.settings, owner_id=owner, workflow_id=wid).state
        == "child_queued"
    )
    drain(env.factory, env.settings)
    assert (
        drive_one(env.factory, env.settings, owner_id=owner, workflow_id=wid).state
        == "waiting_release"
    )
    with env.factory() as session:
        root = session.get(ResearchWorkflow, wid)
        checkpoint = session.get(ResearchWorkflowCheckpoint, root.latest_checkpoint_id)
        rd = RDStageCheckpoint.model_validate_json(checkpoint.terminal_canonical)
        assert session.get(ResearchRun, rd.child_run_id).status == "succeeded"
        from rd_composed_profile_fixture import assert_composed_controller

        assert_composed_controller(session, rd.child_run_id)
        assert root.blocked_reason == "workflow_structured_release_required"
        assert not session.scalars(
            select(OEWorkflowSubmission).filter_by(workflow_id=wid)
        ).all()
    assert not env.oe_calls
    for path in (f"execution-snapshots/{rd.snapshot_id}", f"analyses/{rd.analysis_id}"):
        route = f"/api/v1/engine-runs/{rd.child_run_id}/{path}"
        unreleased = env.client.get(
            route + ("/values" if path.startswith("execution") else ""),
            headers=env.headers,
        )
        assert unreleased.status_code == 409, unreleased.text
        assert (
            env.client.post(route + "/release", headers=env.foreign).status_code == 404
        )
        released = env.client.post(route + "/release", headers=env.headers)
        assert released.status_code == 200, released.text
    assert (
        drive_one(env.factory, env.settings, owner_id=owner, workflow_id=wid).state
        == "structured_child_queued"
    )
    with env.factory() as session:
        attached = session.scalars(
            select(OEWorkflowSubmission).filter_by(workflow_id=wid)
        ).one()
        run = session.get(ResearchRun, attached.run_id)
        assert run.status == "queued" and run.active_attempt_id is None
        config = WorkflowStructuredRunConfig.model_validate_json(
            canonical_bytes(run.config)
        )
        assert config.workflow_context.source_run_id == rd.child_run_id
        assert config.workflow_context.snapshot_id == rd.snapshot_id
        assert config.workflow_context.rd_checkpoint_id == checkpoint.id
        assert attached.submission_origin == "workflow_stage"
        assert (
            attached.selection_sha256 == config.workflow_context.inputs.selection.sha256
        )
        assert attached.resolved_inputs_sha256 == config.workflow_context.inputs.sha256
        assert (
            attached.input_revision_id
            == config.workflow_context.inputs.binding.input_revision_id
        )
        assert attached.config_sha256 == config.sha256
        assert attached.request_sha256 == config.workflow_request_sha256
        child = json.loads(json.dumps(run.config["search"]["domain"]["seed_program"]))
        child["maximum_weight"] = 0.25
        env.reply["candidate"] = {
            "schema_version": "mineagent.oe-structured-candidate.v1",
            "program": child,
        }
        oe_run_id, oe_stage_id = run.id, attached.stage_id
    paused = command(env.client, env.headers, wid, "pause_after_stage")
    assert paused.status_code == 200, paused.text
    premature = command(env.client, env.headers, wid, "resume")
    assert premature.status_code == 409, premature.text
    assert (
        drive_one(env.factory, env.settings, owner_id=owner, workflow_id=wid).state
        == "child_owned_by_engine_worker"
    )
    with env.factory() as session:
        root = session.get(ResearchWorkflow, wid)
        assert root.status == "pause_requested"
        assert not session.scalars(
            select(ResearchWorkflowCheckpoint).filter_by(stage_id=oe_stage_id)
        ).all()
        assert session.get(ResearchRun, oe_run_id).status == "queued"
    drain(env.factory, env.settings)
    with env.factory() as session:
        assert session.get(ResearchRun, oe_run_id).status == "succeeded"
        candidates = session.scalars(
            select(OEStructuredCandidate)
            .filter_by(run_id=oe_run_id)
            .order_by(OEStructuredCandidate.ordinal)
        ).all()
        assert (
            len(candidates) == 2
            and candidates[1].parent_candidate_id == candidates[0].id
        )
        assert candidates[0].origin == "workflow_seed"
        assert candidates[1].origin == "model_proposal"
        executions = session.scalars(
            select(OEStructuredExecution).filter_by(run_id=oe_run_id)
        ).all()
        assert len(executions) == 2 and all(
            x.snapshot_id == rd.snapshot_id for x in executions
        )
        consumption = session.scalars(
            select(ResearchWorkflowModelConsumption).filter_by(stage_id=oe_stage_id)
        ).all()
        assert len(consumption) == len(env.oe_calls) == 1
        assert consumption[0].input_sha256 == config.workflow_context.inputs.sha256
        from backend.app.research_broker_models import (
            ResearchModelOperation,
            ResearchModelTransport,
        )

        operation = session.get(ResearchModelOperation, consumption[0].operation_id)
        transport = session.get(ResearchModelTransport, consumption[0].transport_id)
        assert operation.request_sha256 == consumption[0].request_sha256
        assert transport.operation_id == operation.id
        assert (
            consumption[0].input_revision_id
            == config.workflow_context.inputs.binding.input_revision_id
        )
        execution_ids = tuple(x.id for x in executions)
    for request in env.oe_calls:
        assert len(request["messages"]) == 2
        system, user = request["messages"]
        assert system["role"] == "system" and user["role"] == "user"
        validate_workflow_rendered(
            config.workflow_context, system["content"], user["content"]
        )
    with historical_session(env.factory, owner, env.settings) as session:
        terminal = read_oe_terminal_in_session(
            session, owner, wid, oe_stage_id, env.settings
        )
    assert {x.execution_id for x in terminal.executions} == set(execution_ids)
    assert (
        drive_one(env.factory, env.settings, owner_id=owner, workflow_id=wid).state
        == "structured_stage_closed"
    )
    with env.factory() as session:
        root = session.get(ResearchWorkflow, wid)
        final = session.get(ResearchWorkflowCheckpoint, root.latest_checkpoint_id)
        assert (
            root.status == "paused"
            and final.terminal_canonical == canonical_bytes(terminal).decode()
        )
        assert session.get(ResearchWorkflowStage, oe_stage_id).child_run_id == oe_run_id
    resumed = command(env.client, env.headers, wid, "resume")
    assert resumed.status_code == 200, resumed.text
    with env.factory() as session:
        assert session.get(ResearchWorkflow, wid).status == "completed"
        assert (
            len(
                session.scalars(
                    select(ResearchWorkflowStage).filter_by(workflow_id=wid)
                ).all()
            )
            == 2
        )
    assert len(env.oe_calls) == 1
    return NS(
        workflow_id=wid,
        rd=rd,
        oe_run_id=oe_run_id,
        oe_stage_id=oe_stage_id,
        terminal=terminal,
        execution_ids=execution_ids,
    )


def assert_restore(drill_databases, tmp_path, source, env, completed):
    """Exact031 same actual graph roundtrip, with all live operators disabled."""
    import pytest
    import test_backup_restore as legacy
    from oe_structured_restore_replay import _disabled_settings, _inventory
    from rd_submission_fixture import login
    from test_factor_restore import _files
    from test_neutral_restore import _roundtrip
    from workflow_restore_replay import _paths

    from backend.app.execution_snapshot_history import historical_session
    from backend.app.main import create_app
    from backend.app.oe_structured_result_history import read_result
    from backend.app.operations_backup import BackupLimits, PostgresDatabase
    from backend.app.research_workflow_oe_terminal import read_oe_terminal_in_session
    from backend.app.services import DomainError
    from packages.factors.contracts import canonical_bytes

    owner = env.account.user.id
    originals = tuple(
        read_result(env.factory, env.settings, owner_id=owner, execution_id=eid)
        for eid in completed.execution_ids
    )
    # Result existence/closed stage must never itself grant public release.
    blocked_paths = [
        f"/api/v1/engine-runs/structured-search/runs/{completed.oe_run_id}/executions/{eid}"
        for eid in completed.execution_ids
    ]
    for path in blocked_paths:
        response = env.client.get(path, headers=env.headers)
        assert response.status_code == 409, (path, response.text)
        assert env.client.get(path, headers=env.foreign).status_code == 404
    # Public workflow/input/history navigation is metadata, not output release.
    metadata = {}
    for path in _paths(env.factory, owner, completed.workflow_id):
        response = env.client.get(path, headers=env.headers)
        assert response.status_code == 200, (path, response.text)
        metadata[path] = canonical_bytes(response.json())
    normal_path = f"/api/v1/engine-runs/structured-search/runs/{completed.oe_run_id}"
    normal = env.client.get(normal_path, headers=env.headers)
    assert normal.status_code == 200, normal.text
    normal_value = normal.json()
    assert normal_value["schema_version"] == "mineagent.oe-structured-run-view.v2"
    assert normal_value["submission"]["origin"] == "workflow_stage"
    assert normal_value["submission"]["owner_id"] == str(owner)
    assert normal_value["submission"]["workflow_id"] == str(completed.workflow_id)
    assert normal_value["submission"]["stage_id"] == str(completed.oe_stage_id)
    assert "origin_user_id" not in normal_value["submission"]
    assert normal_value["actions"]["cancel"] is False
    assert env.client.get(normal_path, headers=env.foreign).status_code == 404
    metadata[normal_path] = canonical_bytes(normal_value)
    before_inventory = _inventory(env.factory)
    before = PostgresDatabase(
        source.url.render_as_string(hide_password=False)
    ).source_snapshot(BackupLimits())
    files = _files(env.settings.storage_root)
    target = tmp_path / "workflow-oe-restored"
    manifest = _roundtrip(
        drill_databases, tmp_path, source, env.settings.storage_root, target
    )
    assert manifest["schema_revision"] == "031_workflow_structured_stage"
    assert _files(target) == files
    expected = {
        "research_closure": "mineagent.research-restore-closure.v16",
        "rd_source_closure": "mineagent.rd-source-restore-closure.v11",
        "workflow_closure": "mineagent.research-workflow-restore-closure.v2",
        "oe_structured_closure": "mineagent.oe-structured-restore-closure.v2",
    }
    for key, schema in expected.items():
        assert before[key]["schema_version"] == schema
    restored = legacy._engine(drill_databases.restored)
    try:
        settings = _disabled_settings(env.settings, restored, target)
        factory = sessionmaker(restored, expire_on_commit=False)
        after = PostgresDatabase(
            restored.url.render_as_string(hide_password=False)
        ).source_snapshot(BackupLimits())
        keys = {key for key in before if key.endswith("_closure")}
        assert keys == {key for key in after if key.endswith("_closure")}
        assert all(before[key] == after[key] for key in keys)
        assert _inventory(factory) == before_inventory
        with historical_session(factory, owner, settings) as session:
            terminal = read_oe_terminal_in_session(
                session, owner, completed.workflow_id, completed.oe_stage_id, settings
            )
        assert canonical_bytes(terminal) == canonical_bytes(completed.terminal)
        for eid, original in zip(completed.execution_ids, originals, strict=True):
            assert (
                read_result(factory, settings, owner_id=owner, execution_id=eid)
                == original
            )
            with pytest.raises(DomainError):
                read_result(
                    factory, settings, owner_id=env.other.user.id, execution_id=eid
                )
        with TestClient(create_app(settings=settings, engine=restored)) as client:
            headers, foreign = login(client, env.account), login(client, env.other)
            for path, raw in metadata.items():
                response = client.get(path, headers=headers)
                assert response.status_code == 200, (path, response.text)
                assert canonical_bytes(response.json()) == raw
                assert client.get(path, headers=foreign).status_code == 404
            # Exact public result routes are resolved below from the same router;
            # no POST release is issued during restored reads.
            for path in blocked_paths:
                response = client.get(path, headers=headers)
                assert response.status_code == 409, (path, response.text)
                assert client.get(path, headers=foreign).status_code == 404
        assert _inventory(factory) == before_inventory
    finally:
        restored.dispose()

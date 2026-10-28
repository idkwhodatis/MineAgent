"""Hosted fixture executed INSIDE actual application images, never a host worker.

Only the HTTP provider is a synthetic double. API admission, durable outbox,
Procrastinate worker, isolated controller/numeric/analysis, and SDK are actual.
This script is copied explicitly into owned disposable containers, not shipped
as an application entrypoint or an operator/provider enablement default.
"""

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from script.compose_bounds import alarm_deadline

ROOT = Path("/tmp/mineagent-rd-compose")


def request(path, *, body=None, token=None, key=None, form=False):
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    if key:
        headers["Idempotency-Key"] = key
    raw = None
    if body is not None:
        raw = (urlencode(body) if form else json.dumps(body)).encode()
        headers["Content-Type"] = (
            "application/x-www-form-urlencoded" if form else "application/json"
        )
    with (
        alarm_deadline(time.monotonic() + 20),
        urlopen(
            Request("http://127.0.0.1:8000/api/v1" + path, raw, headers), timeout=20
        ) as response,
    ):
        result = response.read(1048577)
        if len(result) > 1048576:
            raise ValueError("Response bound")
        return json.loads(result)


def database():
    from backend.app.core.config import get_settings
    from backend.app.core.db import create_db_engine, session_factory

    settings = get_settings()
    engine = create_db_engine(settings)
    return settings, engine, session_factory(engine)


def owner_id():
    from sqlalchemy import select

    from backend.app.models import User

    _, engine, factory = database()
    try:
        with factory() as session:
            user = session.scalars(
                select(User).where(User.email == os.environ["MINEAGENT_COMPOSE_EMAIL"])
            ).one()
            return user.id
    finally:
        engine.dispose()


def compose_source():
    from rd_provider_fixture import SOURCE

    # Observation window only, inside the unchanged ten-second numeric bound.
    return SOURCE.replace(
        'root = Path("/input")', 'import time\ntime.sleep(3)\nroot = Path("/input")'
    )


def provider():
    from rd_provider_fixture import GUIDANCE, provider_server

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    with provider_server(sources=(compose_source(),), port=18765) as (
        endpoint,
        captured,
    ):
        if endpoint != "http://127.0.0.1:18765/v1":
            raise ValueError("Local fixture endpoint differs")
        (ROOT / "provider-ready").write_text("ready\n")
        deadline = time.monotonic() + 600
        while not stop.wait(0.1) and time.monotonic() < deadline:
            if len(captured) > 32:
                raise ValueError("Provider fixture request bound")
            first = next(
                (body for path, body in captured if path == "/v1/chat/completions"),
                None,
            )
            summary = {
                "requests": len(captured),
                "embedding_seen": any(path == "/v1/embeddings" for path, _ in captured),
                "guidance_seen": bool(
                    first
                    and GUIDANCE
                    in "\n".join(message["content"] for message in first["messages"])
                ),
            }
            temporary = ROOT / "provider-observed.tmp"
            temporary.write_text(json.dumps(summary, sort_keys=True))
            temporary.replace(ROOT / "provider-observed.json")


def run():
    from rd_composed_profile_fixture import PROFILE, assert_composed_controller
    from rd_configured_dataset_fixture import persist_configured_dataset
    from rd_provider_fixture import GUIDANCE
    from rd_submission_fixture import body
    from sqlalchemy import select

    from backend.app.execution_snapshot_history import (
        read_historical_execution_snapshot,
    )
    from backend.app.factor_analysis_models import (
        FactorDirectAnalysisIntent,
        FactorDirectAnalysisOutcome,
    )
    from backend.app.factor_models import FactorVersion
    from backend.app.models import ResearchRun
    from backend.app.rdagent_models import FactorGenerationSource
    from backend.app.rdagent_provider_models import RDProviderTransportReceipt
    from backend.app.rdagent_provider_preflight import provider_budget_floor
    from backend.app.research_broker_models import (
        ResearchModelOperation,
        ResearchModelTransport,
    )
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
        ResearchProcessState,
    )
    from script.compose_provider_runtime import verify_sdk

    settings, engine, factory = database()
    try:
        verify_sdk(settings.rdagent_provider_python)
        owner = owner_id()
        dataset, _, policy = persist_configured_dataset(factory, owner, settings)
        token = request(
            "/login/access-token",
            body={
                "username": os.environ["MINEAGENT_COMPOSE_EMAIL"],
                "password": os.environ["MINEAGENT_COMPOSE_PASSWORD"],
            },
            form=True,
        )["access_token"]
        steer = request(
            "/rd-steers", token=token, body={"kind": "hypothesis", "text": GUIDANCE}
        )
        model = settings.rdagent_provider_profiles[0]
        declaration = body(dataset, policy, controller_profile=PROFILE).model_dump(
            mode="json"
        )
        declaration.update(
            schema_version="mineagent.rdagent-provider-submission.v1",
            model_profile_id=model.connection.id,
            selected_steer_version_ids=[steer["current"]["version_id"]],
            budget=provider_budget_floor(
                model.public_binding(owner), profile=PROFILE
            ).model_dump(mode="json"),
            task={
                "name": "compose-current-price",
                "description": GUIDANCE,
                "formulation": "close[t]",
                "factor_kind": "cross_sectional",
                "output_unit": "price",
                "interpretation": "higher_preferred",
                "explanation_origin": "task_declaration",
            },
        )
        checked = request("/engine-runs/preflight", body=declaration, token=token)
        if checked.get("can_submit") is not True:
            raise ValueError("Normal preflight unavailable")
        exact = {
            **checked["normalized_request"],
            "preflight_sha256": checked["preflight_sha256"],
        }
        key = str(uuid4())
        receipt = request("/engine-runs", body=exact, token=token, key=key)
        if receipt.get("accepted") is not True:
            raise ValueError("Normal submission not accepted")
        rid = UUID(receipt["run_id"])
        deadline = time.monotonic() + 360
        while True:
            detail = request(f"/engine-runs/{rid}", token=token)
            if detail["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                break
            if time.monotonic() >= deadline:
                raise ValueError("Normal Compose worker deadline")
            time.sleep(1)
        if detail["status"] != "succeeded":
            raise ValueError("Normal Compose worker did not succeed")
        with factory() as session:
            assert_composed_controller(session, rid)
            run_row = session.get(ResearchRun, rid)
            if run_row.owner_id != owner or run_row.status != "succeeded":
                raise ValueError("Owned terminal run differs")
            source = session.scalars(
                select(FactorGenerationSource).filter_by(run_id=rid)
            ).one()
            if (
                session.get(FactorVersion, source.factor_version_id).code
                != compose_source()
            ):
                raise ValueError("Actual SDK generated source differs")
            measured = session.scalars(
                select(FactorDirectAnalysisOutcome).filter_by(run_id=rid)
            ).one()
            if (
                measured.result["rank_ic"] != 1.0
                or measured.measurement["status"] != "passed"
            ):
                raise ValueError("Actual direct analysis differs")
            snapshot = session.get(
                FactorDirectAnalysisIntent, measured.analysis_id
            ).snapshot_id
            units = session.scalars(
                select(ResearchProcessAdmission).filter_by(run_id=rid)
            ).all()
            if sorted(row.role for row in units) != [
                "factor_cutoff_candidate",
                "factor_normalizer",
                "native_evaluator",
                "rd_orchestrator",
            ]:
                raise ValueError("Exact four native units required")
            if any(
                session.get(ResearchProcessState, row.id).status != "stopped"
                for row in units
            ):
                raise ValueError("Actual native stop not confirmed")
            operations = session.scalars(
                select(ResearchModelOperation).filter_by(run_id=rid)
            ).all()
            transports = session.scalars(
                select(ResearchModelTransport).filter_by(run_id=rid)
            ).all()
            sdk_rows = session.scalars(
                select(RDProviderTransportReceipt).filter_by(run_id=rid)
            ).all()
            if not (
                4 < len(operations) <= 32
                and len(transports) == len(operations)
                and all(row.state == "completed" for row in transports)
                and len(sdk_rows) == 3 * len(transports)
            ):
                raise ValueError(
                    "Actual SDK request/identity/result/stop chain differs"
                )
        history = read_historical_execution_snapshot(factory, owner, snapshot, settings)
        if sorted(row.value for row in history.rows) != [98.0, 102.0, 104.0]:
            raise ValueError("Real retained numeric values differ")
        replay = request("/engine-runs", body=exact, token=token, key=key)
        if replay["run_id"] != str(rid) or replay.get("replayed") is not True:
            raise ValueError("Normal exact request replay differs")
        return {
            "status": "passed",
            "native_units": 4,
            "sdk_transports": len(transports),
            "provider": "in_worker_localhost_double",
            "host_worker_used": False,
            "services": [
                "database",
                "migrate",
                "bootstrap",
                "api",
                "research-dispatcher",
                "research-worker",
                "evaluator-reconciler",
            ],
            "qualification": "actual_compose_application_subset_not_full_frontend_or_live_provider",
        }
    finally:
        engine.dispose()


def failure_location(error):
    """Only fixed local frame identifiers, never exception payloads or paths."""
    allowed_functions = {
        "<module>",
        "request",
        "database",
        "owner_id",
        "compose_source",
        "provider",
        "run",
    }
    allowed_types = {
        "ModuleNotFoundError",
        "ImportError",
        "ValueError",
        "TypeError",
        "KeyError",
        "AttributeError",
        "HTTPError",
        "TimeoutError",
        "CalledProcessError",
        "PermissionError",
        "AssertionError",
    }
    frames = []
    trace = error.__traceback__
    while trace is not None:
        frame = trace.tb_frame
        if (
            frame.f_code.co_filename == __file__
            and frame.f_code.co_name in allowed_functions
            and 0 < trace.tb_lineno <= 10000
        ):
            frames.append(
                {
                    "module": "script.ci_rd_compose_inside",
                    "function": frame.f_code.co_name,
                    "line": trace.tb_lineno,
                }
            )
        trace = trace.tb_next
    kind = type(error).__name__
    return {
        "schema": "mineagent.compose-fixture-failure.v1",
        "type": kind if kind in allowed_types else "Exception",
        "frames": frames[-8:],
    }


if __name__ == "__main__":
    try:
        mode = sys.argv[1]
        if mode == "owner":
            print(owner_id())
        elif mode == "provider":
            provider()
        elif mode == "run":
            print(json.dumps(run(), sort_keys=True))
        else:
            raise ValueError("Unknown fixture mode")
    except Exception as error:  # noqa: BLE001 - private payloads never emitted
        print(
            "COMPOSE_FAILURE_LOCATION:"
            + json.dumps(failure_location(error), sort_keys=True),
            file=sys.stderr,
        )
        raise SystemExit(
            "Packaged RD Compose fixture failed; acceptance not established"
        ) from None

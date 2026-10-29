"""Real 028 workflow history browser over the still-live actual fixture database."""

import json
import os
import re
import secrets
import shutil
import socket
import threading
import time
from pathlib import Path

import uvicorn
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.app.main import create_app
from script.ci_process_owner import OwnedCommandFailed, ProcessOwner
from script.oe_browser_diagnostic import browser_failure

HISTORY_CASE = "actual workflow history preserves frozen stages and provenance without new authority"
CREATE_CASE = "actual workflow entry creates exactly one retained request"


def assert_actual_workflow_browser(
    engine, settings, owner, other, workflow_id, expected, tmp_path
):
    return _run_browser(
        engine,
        settings,
        owner,
        other,
        tmp_path,
        HISTORY_CASE,
        {
            "E2E_WORKFLOW_OBSERVATIONS": json.dumps(
                {"workflow_id": str(workflow_id), **expected}
            )
        },
    )


def assert_actual_workflow_creation(
    engine, settings, owner, expected_request, raw_content, tmp_path
):
    from backend.app.research_workflow_public_schemas import (
        WorkflowRequest,
        WorkflowSubmit,
    )
    from backend.app.research_workflow_structured_schemas import (
        StructuredWorkflowRequest,
        StructuredWorkflowSubmit,
    )

    schema = expected_request.get("schema_version")
    if schema == "mineagent.workflow-public-request.v1":
        request_type, submit_type = WorkflowRequest, WorkflowSubmit
    elif schema == "mineagent.workflow-public-request.v2":
        request_type, submit_type = StructuredWorkflowRequest, StructuredWorkflowSubmit
    else:
        raise AssertionError("Exact public workflow request version required")
    request = request_type.model_validate_json(json.dumps(expected_request))
    assert request.external_definition is None
    raw = (
        raw_content.decode("utf-8", errors="strict") if raw_content is not None else ""
    )
    assert len(raw.encode("utf-8")) <= 65536
    output = tmp_path / ("workflow-create-" + secrets.token_hex(12) + ".json")
    _run_browser(
        engine,
        settings,
        owner,
        owner,
        tmp_path,
        CREATE_CASE,
        {
            "E2E_WORKFLOW_CREATE_REQUEST": request.model_dump_json(),
            "E2E_WORKFLOW_RAW": raw,
            "E2E_WORKFLOW_CREATE_OUTPUT": str(output),
        },
    )
    import stat

    info = output.lstat()
    assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 131072
    assert info.st_mode & 0o077 == 0
    with output.open("rb") as stream:
        encoded = stream.read(131073)
    assert len(encoded) <= 131072
    result = json.loads(encoded)
    assert set(result) == {"envelope", "receipt", "raw_receipt"}
    observed = submit_type.model_validate_json(json.dumps(result["envelope"])).request
    expected = request.model_dump(mode="json")
    expected["external_definition"] = observed.model_dump(mode="json")[
        "external_definition"
    ]
    assert observed.model_dump(mode="json") == expected
    if raw_content is None:
        assert observed.external_definition is None and result["raw_receipt"] is None
    else:
        import hashlib

        assert (
            result["raw_receipt"]["sha256"] == hashlib.sha256(raw_content).hexdigest()
        )
        assert result["raw_receipt"]["size_bytes"] == len(raw_content)
        assert (
            str(observed.external_definition.raw_object_id)
            == result["raw_receipt"]["id"]
        )
        assert (
            observed.external_definition.raw_sha256 == result["raw_receipt"]["sha256"]
        )
        from packages.research_inputs.contracts import ExternalFeedMapping

        assert observed.external_definition.mapping == ExternalFeedMapping()
    return result


def assert_actual_observation_checkpoint(
    engine, settings, owner, workflow_id, run_id, analysis_id, tmp_path
):
    """UI saves/selects/resumes; caller must execute and verify the real next child."""
    import stat

    output = tmp_path / ("workflow-observation-" + secrets.token_hex(12) + ".json")
    _run_browser(
        engine,
        settings,
        owner,
        owner,
        tmp_path,
        "actual released observation is selected and resumed only at a safe checkpoint",
        {
            "E2E_WORKFLOW_CHECKPOINT": json.dumps(
                {
                    "workflow_id": str(workflow_id),
                    "run_id": str(run_id),
                    "analysis_id": str(analysis_id),
                }
            ),
            "E2E_WORKFLOW_CHECKPOINT_OUTPUT": str(output),
        },
    )
    info = output.lstat()
    assert (
        stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_mode & 0o077 == 0
    )
    assert info.st_size <= 32768
    with output.open("rb") as stream:
        data = stream.read(32769)
    assert len(data) <= 32768
    result = json.loads(data)
    assert set(result) == {
        "saved",
        "previous_stage",
        "analysis_id",
        "result_sha256",
        "session",
        "selection",
        "resume",
    }
    assert result["analysis_id"] == str(analysis_id)
    return result


def _run_browser(engine, settings, owner, other, tmp_path, case, additions):
    assert os.environ.get("MINEAGENT_RD_WORKFLOW_BROWSER_REQUIRED") == "1", (
        "Actual workflow browser is mandatory; set its required gate flag"
    )
    root = Path(__file__).resolve().parents[2]
    dist = root / "frontend/dist"
    node = shutil.which("node")
    assert node and (dist / "index.html").is_file(), "Build the real frontend first"
    # Expected observations come from the actual completed fixture, not a
    # replacement API or fabricated runtime. Credentials/settings stay separate.
    assert sum(len(value.encode("utf-8")) for value in additions.values()) <= 131072
    app = create_app(settings=settings, engine=engine)
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="history-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        return FileResponse(dist / "index.html")

    # Bind the exact socket before starting, avoiding a find-free-port race.
    listener = socket.socket()
    server = thread = process = None
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        address = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(
            uvicorn.Config(app, log_level="error", access_log=False)
        )
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        process = ProcessOwner(time.monotonic() + 180)
        process.contain()
        thread.start()
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started, "Actual HTTP server did not start"
        # Only synthetic login credentials reach Node; never DB URL/settings.
        env = {
            key: os.environ[key]
            for key in ("PATH", "HOME", "LANG", "PLAYWRIGHT_BROWSERS_PATH")
            if key in os.environ
        }
        env.update(
            E2E_BASE_URL=address,
            E2E_API_URL=address,
            E2E_EMAIL=owner.user.email,
            E2E_PASSWORD=owner.password,
            E2E_OTHER_EMAIL=other.user.email,
            E2E_OTHER_PASSWORD=other.password,
            PLAYWRIGHT_JUNIT_OUTPUT_FILE=str(tmp_path / "workflow-history-browser.xml"),
        )
        env.update(additions)
        try:
            process.run(
                [
                    node,
                    str(root / "frontend/node_modules/@playwright/test/cli.js"),
                    "test",
                    "-c",
                    "playwright.workflow-history.config.ts",
                    "--grep",
                    re.escape(case) + "$",
                    "--reporter=junit",
                    "--output",
                    str(tmp_path / ("browser-" + secrets.token_hex(4))),
                ],
                cwd=root / "frontend",
                env=env,
                seconds=120,
            )
        except OwnedCommandFailed:
            diagnostic = browser_failure(
                tmp_path / "workflow-history-browser.xml", root
            )
            raise RuntimeError(
                "WORKFLOW_BROWSER_FAILURE_V1:" + json.dumps(diagnostic, sort_keys=True)
            ) from None
        # A zero exit after filtering/listing/skipping is not acceptance.
        from xml.etree import ElementTree as ET

        cases = (
            ET.parse(tmp_path / "workflow-history-browser.xml")
            .getroot()
            .findall(".//testcase")
        )
        assert len(cases) == 1
        assert cases[0].get("name") == case
        assert not any(
            cases[0].find(tag) is not None for tag in ("failure", "error", "skipped")
        )
    finally:
        stopped = process.cleanup() if process is not None else True
        if server is not None:
            server.should_exit = True
        if thread is not None and thread.ident is not None:
            thread.join(timeout=15)
        listener.close()
        assert stopped and (thread is None or not thread.is_alive()), (
            "Browser/API cleanup unconfirmed"
        )

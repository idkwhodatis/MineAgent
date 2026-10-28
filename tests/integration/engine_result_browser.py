"""Real completed022 HTTP/Chromium gate; reuses actual four-unit results only."""

import os
import secrets
import shutil
import socket
import threading
import time
from pathlib import Path

import uvicorn
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.app.execution_snapshot_models import ExecutionSnapshotPublication
from backend.app.factor_analysis_models import FactorDirectAnalysisIntent
from backend.app.factor_models import FactorVersion
from backend.app.main import create_app
from script.ci_browser_diagnostics import failure_diagnostic
from script.ci_process_owner import ProcessOwner


def assert_actual_engine_result_browser(
    engine, settings, owner, other, factory, run_id, analysis_id, tmp_path
):
    assert os.environ.get("MINEAGENT_CONFIGURED_BROWSER_REQUIRED") == "1", (
        "Configured result browser is mandatory; set its required gate flag"
    )
    root = Path(__file__).resolve().parents[2]
    dist = root / "frontend/dist"
    node = shutil.which("node")
    assert node and (dist / "index.html").is_file(), "Build the real frontend first"
    with factory() as session:
        intent = session.get(FactorDirectAnalysisIntent, analysis_id)
        publication = session.get(ExecutionSnapshotPublication, intent.snapshot_id)
        version = session.get(FactorVersion, publication.factor_version_id)
        snapshot_id, factor_id, version_id = (
            publication.id,
            version.factor_id,
            version.id,
        )
    settings = settings.model_copy(update={"factor_library_enabled": True})
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
            E2E_RD_RESULT_RUN_ID=str(run_id),
            E2E_ANALYSIS_ID=str(analysis_id),
            E2E_SNAPSHOT_ID=str(snapshot_id),
            E2E_FACTOR_ID=str(factor_id),
            E2E_FACTOR_VERSION_ID=str(version_id),
            PLAYWRIGHT_JUNIT_OUTPUT_FILE=str(tmp_path / "engine-results-browser.xml"),
        )
        try:
            process.run(
                [
                    node,
                    str(root / "frontend/node_modules/@playwright/test/cli.js"),
                    "test",
                    "-c",
                    "playwright.engine-results.config.ts",
                    "--reporter=junit",
                    "--output",
                    str(tmp_path / ("browser-" + secrets.token_hex(4))),
                ],
                cwd=root / "frontend",
                env=env,
                seconds=120,
            )
        except Exception:  # noqa: BLE001 - child output remains private
            print(
                failure_diagnostic(
                    tmp_path / "engine-results-browser.xml",
                    root,
                    source_suite="engine-results",
                )
            )
            raise RuntimeError("Actual configured result browser failed") from None
        # A zero exit after filtering/listing/skipping is not acceptance.
        from xml.etree import ElementTree as ET

        cases = (
            ET.parse(tmp_path / "engine-results-browser.xml")
            .getroot()
            .findall(".//testcase")
        )
        assert len(cases) == 1
        assert (
            cases[0].get("name")
            == "actual configured results require release and survive reload without live authority"
        )
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

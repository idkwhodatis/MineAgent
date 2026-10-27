"""Genuine023 entry browser only: no worker, synthetic attempt or execution grant."""

import os
import secrets
import shutil
import socket
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.app.main import create_app
from script.ci_process_owner import ProcessOwner


def create_queued_submission_browser(settings, engine, account, dataset, request):
    assert os.environ.get("MINEAGENT_RD_CREATE_BROWSER_REQUIRED") == "1", (
        "Actual RD entry browser is required; no fallback to API submission"
    )
    with TemporaryDirectory(prefix="mineagent-rd-create-browser-") as directory:
        _browser(settings, engine, account, dataset, request, Path(directory))


def _browser(settings, engine, account, dataset, request, tmp_path):
    root = Path(__file__).resolve().parents[2]
    dist = root / "frontend/dist"
    node = shutil.which("node")
    assert node and (dist / "index.html").is_file(), "Build the real frontend first"
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
            E2E_EMAIL=account.user.email,
            E2E_PASSWORD=account.password,
            E2E_RD_REQUEST_JSON=request.model_dump_json(),
            E2E_RD_REQUEST_SHA256=request.sha256,
            E2E_RD_DATASET_ID=str(dataset.id),
            PLAYWRIGHT_JUNIT_OUTPUT_FILE=str(tmp_path / "engine-create-browser.xml"),
        )
        process.run(
            [
                node,
                str(root / "frontend/node_modules/@playwright/test/cli.js"),
                "test",
                "-c",
                "playwright.engine-create.config.ts",
                "--reporter=junit",
                "--output",
                str(tmp_path / ("browser-" + secrets.token_hex(4))),
            ],
            cwd=root / "frontend",
            env=env,
            seconds=120,
        )
        # A zero exit after filtering/listing/skipping is not acceptance.
        from xml.etree import ElementTree as ET

        cases = (
            ET.parse(tmp_path / "engine-create-browser.xml")
            .getroot()
            .findall(".//testcase")
        )
        assert len(cases) == 1
        assert (
            cases[0].get("name")
            == "actual configured entry creates one queued run and survives reload"
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

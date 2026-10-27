"""Real PostgreSQL source persistence + HTTP + Chromium, explicit standalone gate.

Run separately: python -m pytest tests/engine_history_browser -q
Requires TEST_DATABASE_URL, built frontend/dist, installed Chromium and Node.
No external provider/worker or candidate execution is involved.
"""

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
from sqlalchemy.orm import sessionmaker

from backend.app.main import create_app
from script.ci_process_owner import ProcessOwner
from tests.integration.rd_configured_dataset_fixture import persist_configured_dataset
from tests.integration.rd_source_fixture import persist_maintained_source


def test_real_isolated_schema_history_browser(
    migrated_engine, settings, owner, other, tmp_path
):
    root = Path(__file__).resolve().parents[2]
    dist = root / "frontend/dist"
    node = shutil.which("node")
    assert node and (dist / "index.html").is_file(), "Build the real frontend first"
    saved = persist_maintained_source(
        sessionmaker(migrated_engine, expire_on_commit=False),
        owner=owner.user.id,
        dataset=owner.dataset,
        settings=settings,
        source_utf8=b"# Actual browser history fixture; never executed\ndef factor():\n    return 1\n",
    )
    retained_dataset, _, _ = persist_configured_dataset(
        sessionmaker(migrated_engine, expire_on_commit=False), owner.user.id, settings
    )
    app = create_app(settings=settings, engine=migrated_engine)
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
            E2E_RD_HISTORY_RUN_ID=str(saved.fence.run_id),
            E2E_RD_PREFLIGHT_DATASET_ID=str(retained_dataset.id),
            PLAYWRIGHT_JUNIT_OUTPUT_FILE=str(tmp_path / "browser.xml"),
        )
        process.run(
            [
                node,
                str(root / "frontend/node_modules/@playwright/test/cli.js"),
                "test",
                "-c",
                "playwright.engine-history.config.ts",
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

        cases = ET.parse(tmp_path / "browser.xml").getroot().findall(".//testcase")
        assert len(cases) == 1
        assert (
            cases[0].get("name")
            == "actual saved RD history survives reload, back and owner denial without enabling execution"
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

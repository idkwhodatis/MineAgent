"""Genuine023 entry browser only: no worker, synthetic attempt or execution grant."""

import os
import secrets
import shutil
import socket
import stat
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.app.main import create_app
from script.ci_process_owner import OwnedCommandFailed, ProcessOwner

_BROWSER_CASE = "actual configured entry creates one queued run and survives reload"
_DIAGNOSTIC_LIMIT = 64 * 1024


def _browser_failure_category(directory):
    """Return constants only; never expose XML attributes, text or exception strings."""
    from xml.etree import ElementTree as ET

    unavailable = "diagnostic_unavailable"
    directory_fd = file_fd = None
    try:
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        file_fd = os.open(
            "engine-create-browser.xml",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
        before = os.fstat(file_fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or not 0 < before.st_size <= _DIAGNOSTIC_LIMIT
        ):
            return unavailable
        raw = os.read(file_fd, _DIAGNOSTIC_LIMIT + 1)
        after = os.fstat(file_fd)
        if len(raw) != before.st_size or (
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            return unavailable
        document = raw.decode("utf-8", errors="strict")
        if "<!" in document or "\x00" in document:
            return unavailable
        cases = ET.fromstring(document).findall(".//testcase")
        if len(cases) != 1 or cases[0].get("name") != _BROWSER_CASE:
            return unavailable
        failure = cases[0].find("failure") is not None
        error = cases[0].find("error") is not None
        if failure and error:
            return "expected_case_failure_and_error"
        if failure:
            return "expected_case_failure"
        if error:
            return "expected_case_error"
        return unavailable
    except (OSError, ValueError, ET.ParseError):
        return unavailable
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)


def _run_browser(process, *args, diagnostic_directory, **kwargs):
    try:
        return process.run(*args, **kwargs)
    except OwnedCommandFailed:
        try:
            category = _browser_failure_category(diagnostic_directory)
            print("RD browser phase=browser_exit category=" + category)
        except Exception:  # noqa: BLE001, S110 -- never log untrusted exceptions
            # Diagnostics must not replace the original failure or cleanup path.
            pass
        raise


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
        _run_browser(
            process,
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
            diagnostic_directory=tmp_path,
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

"""Actual027 browser seams, invoked only while the real fixture DB is alive.

No fake routes, SQL inserts, provider calls or worker dispatch in this helper.
Browser-observed immutable requests are returned for exact replay by the fixture.
"""

import json
import os
import secrets
import shutil
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID
from xml.etree import ElementTree as ET

import uvicorn
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.main import create_app
from backend.app.models import (
    DataUsageExposure,
    ResearchNote,
    ResearchNoteEvidencePin,
    ResearchNoteRevision,
)
from backend.app.oe_structured_provider_submission import (
    ProviderStructuredSearchSubmit,
    ProviderStructuredSubmissionReceipt,
)
from backend.app.structured_demo_routes import StructuredDemoReceipt
from packages.factors.contracts import canonical_bytes
from script.ci_process_owner import OwnedCommandFailed, ProcessOwner
from script.oe_browser_diagnostic import browser_failure

NAMES = {
    "normal_notes_edit": "actual workflow normal notes retain exact released rows and restore boundaries",
    "normal_notes": "actual workflow normal notes retain exact released rows and restore boundaries",
    "demo": "empty owner explicitly creates demo and applies RD inputs without submitting",
    "submit": "normal search explicitly checks applies budget and submits one immutable request",
    "status": "actual normal run status reload preserves owner and release boundaries",
    "results": "actual normal results require separate releases and preserve owner isolation",
}


@dataclass(frozen=True)
class BrowserSubmission:
    receipt: ProviderStructuredSubmissionReceipt
    idempotency_key: str
    body: ProviderStructuredSearchSubmit
    request_canonical: str


def create_demo_in_browser(engine, settings, account):
    value = _browser(engine, settings, account, None, {"mode": "demo"})
    return StructuredDemoReceipt.model_validate_json(canonical_bytes(value))


def create_oe_submission_in_browser(engine, settings, account, request):
    value = _browser(
        engine,
        settings,
        account,
        None,
        {"mode": "submit", "request": request.model_dump(mode="json")},
    )
    assert set(value) == {"receipt", "idempotency_key", "body"}
    body = ProviderStructuredSearchSubmit.model_validate_json(
        canonical_bytes(value["body"])
    )
    receipt = ProviderStructuredSubmissionReceipt.model_validate_json(
        canonical_bytes(value["receipt"])
    )
    assert receipt.origin_user_id == account.user.id
    assert body.model_dump(
        exclude={"preflight_sha256"}, mode="json"
    ) == request.model_dump(mode="json")
    assert body.preflight_sha256 == receipt.preflight_sha256
    assert receipt.normalized_request == request
    key = value["idempotency_key"]
    assert str(UUID(key)) == key
    return BrowserSubmission(
        receipt, key, body, canonical_bytes(value["body"]).decode()
    )


def assert_oe_run_in_browser(engine, settings, account, run_id, other):
    def releases():
        with Session(engine) as session:
            return frozenset(
                session.scalars(
                    select(DataUsageExposure.id).where(
                        DataUsageExposure.owner_id.in_((account.user.id, other.user.id))
                    )
                ).all()
            )

    before = releases()
    value = _browser(
        engine,
        settings,
        account,
        other,
        {"mode": "status", "run_id": str(run_id)},
    )
    assert releases() == before, (
        "Status/browser reads must not create release authority"
    )
    assert value == {
        "run_id": str(run_id),
        "status": "succeeded",
        "other_owner_status": 404,
    }


def assert_oe_results_in_browser(
    engine, settings, account, run_id, execution_ids, other
):
    ids = [str(UUID(str(value))) for value in execution_ids]
    assert len(ids) == 2 and len(set(ids)) == 2
    value = _browser(
        engine,
        settings,
        account,
        other,
        {"mode": "results", "run_id": str(run_id), "execution_ids": ids},
    )
    assert value == {
        "run_id": str(run_id),
        "execution_ids": ids,
        "explicit_release_count": 2,
        "other_owner_status": 404,
    }


def assert_normal_oe_notes_in_browser(
    engine, settings, account, run_id, execution_id, other, saved, *, edit=False
):
    """Source may explicitly edit; restored mode remains strictly GET-only."""
    revisions = []
    for path, raw in saved.responses:
        value = json.loads(raw)
        if (
            isinstance(value, dict)
            and "revision_sha256" in value
            and "restore_href" in value
        ):
            assert path.startswith("/api/v1/research-notes/")
            revisions.append(
                {
                    "path": path.removeprefix("/api/v1"),
                    "revision_sha256": value["revision_sha256"],
                    "restore_href": value["restore_href"],
                }
            )
    assert len(revisions) == (2 if edit else 4)

    def retained_rows():
        with Session(engine) as session:
            return tuple(
                frozenset(session.scalars(select(model.id)).all())
                for model in (
                    DataUsageExposure,
                    ResearchNote,
                    ResearchNoteRevision,
                    ResearchNoteEvidencePin,
                )
            )

    before = retained_rows()
    value = _browser(
        engine,
        settings,
        account,
        other,
        {
            "mode": "normal_notes_edit" if edit else "normal_notes",
            "run_id": str(UUID(str(run_id))),
            "execution_id": str(UUID(str(execution_id))),
            "revisions": revisions,
        },
    )
    observed = value.pop("editor_revisions")
    after = retained_rows()
    assert value == {
        "run_id": str(run_id),
        "execution_id": str(execution_id),
        "restored_revisions": [item["revision_sha256"] for item in revisions],
        "other_owner_status": 404,
        "mutation_count": 2 if edit else 0,
    }
    if not edit:
        assert observed == [] and after == before
        return ()
    assert type(observed) is list and len(observed) == 2
    for index, item in enumerate(observed, 1):
        assert set(item) == {"note_id", "revision", "revision_sha256"}
        assert str(UUID(item["note_id"])) == item["note_id"]
        assert type(item["revision"]) is int and item["revision"] == index
    assert observed[0]["note_id"] == observed[1]["note_id"]
    note_id = UUID(observed[0]["note_id"])
    assert after[0] == before[0], "Composer must not create exposure releases"
    assert after[1] - before[1] == {note_id} and before[1] <= after[1]
    with Session(engine) as session:
        note = session.get(ResearchNote, note_id)
        assert note.owner_id == account.user.id
        rows = session.scalars(
            select(ResearchNoteRevision)
            .where(ResearchNoteRevision.note_id == note_id)
            .order_by(ResearchNoteRevision.revision)
        ).all()
        assert len(rows) == 2 and after[2] - before[2] == {row.id for row in rows}
        assert before[2] <= after[2] and before[3] <= after[3]
        pins = session.scalars(
            select(ResearchNoteEvidencePin).where(
                ResearchNoteEvidencePin.revision_id.in_([row.id for row in rows])
            )
        ).all()
        assert len(pins) == 2 and after[3] - before[3] == {pin.id for pin in pins}
        assert all(
            pin.owner_id == account.user.id
            and pin.resource_kind == "normal_oe_execution"
            and pin.resource_id == UUID(str(execution_id))
            for pin in pins
        )
        for row, item in zip(rows, observed, strict=True):
            assert (
                row.owner_id == account.user.id
                and row.revision == item["revision"]
                and row.revision_sha256 == item["revision_sha256"]
            )
    return tuple(observed)


def _browser(engine, settings, account, other, observation):
    assert os.environ.get("MINEAGENT_OE_NORMAL_BROWSER_REQUIRED") == "1", (
        "Actual normal027 browser is required; no API-only fallback"
    )
    root = Path(__file__).resolve().parents[2]
    dist = root / "frontend/dist"
    node = shutil.which("node")
    assert node and (dist / "index.html").is_file(), "Build actual frontend first"
    manifest = json.dumps(observation, allow_nan=False)
    assert len(manifest.encode()) <= 65536
    app = create_app(settings=settings, engine=engine)
    app.mount(
        "/assets", StaticFiles(directory=dist / "assets"), name="oe-browser-assets"
    )

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        return FileResponse(dist / "index.html")

    with TemporaryDirectory(prefix="mineagent-oe-normal-browser-") as directory:
        private = Path(directory)
        output = private / "observed.json"
        junit = private / "browser.xml"
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
            while (
                not server.started and thread.is_alive() and time.monotonic() < deadline
            ):
                time.sleep(0.05)
            assert server.started, "Actual HTTP server did not start"
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
                E2E_OE_OBSERVATIONS=manifest,
                E2E_OE_OUTPUT=str(output),
                PLAYWRIGHT_JUNIT_OUTPUT_FILE=str(junit),
            )
            if other is not None:
                env.update(
                    E2E_OTHER_EMAIL=other.user.email, E2E_OTHER_PASSWORD=other.password
                )
            try:
                process.run(
                    [
                        node,
                        str(root / "frontend/node_modules/@playwright/test/cli.js"),
                        "test",
                        "-c",
                        "playwright.oe-normal.config.ts",
                        "--grep",
                        NAMES[observation["mode"]],
                        "--reporter=junit",
                        "--output",
                        str(private / ("browser-" + secrets.token_hex(4))),
                    ],
                    cwd=root / "frontend",
                    env=env,
                    seconds=120,
                )
            except OwnedCommandFailed:
                # Private JUnit is deleted by TemporaryDirectory; emit only the
                # closed source-frame projection, never browser stdout or values.
                diagnostic = browser_failure(junit, root)
                raise RuntimeError(
                    "OE_BROWSER_FAILURE_V1:" + json.dumps(diagnostic, sort_keys=True)
                ) from None
            cases = ET.parse(junit).getroot().findall(".//testcase")
            assert (
                len(cases) == 1 and cases[0].get("name") == NAMES[observation["mode"]]
            )
            assert not any(
                cases[0].find(tag) is not None
                for tag in ("failure", "error", "skipped")
            )
            assert (
                output.is_file()
                and not output.is_symlink()
                and output.stat().st_size <= 131072
            )
            return json.loads(output.read_bytes())
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

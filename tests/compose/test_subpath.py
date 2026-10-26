"""Runtime prefix contract without a Docker daemon or a live database."""

import os
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.api.subpath import SubpathMiddleware
from backend.app.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]
INVALID = [
    "/ok\n/also-ok",
    "/ok\n",
    "/ok\r",
    "/a/",
    "//a",
    "/.",
    "/a/..",
    "/a//b",
    "/a%2fb",
    "/a?x",
    "/a#x",
    "/a\\b",
    "/é",
    "a",
    "/" + "a" * 200,
]


@pytest.mark.parametrize("prefix", ["", "/", "/lab/research-7", "/api"])
def test_runtime_renderer_and_settings(prefix, tmp_path):
    expected = "" if prefix == "/" else prefix
    settings = Settings(
        database_url="postgresql://x:x@localhost/x", secret_key="x" * 32, subpath=prefix
    )
    assert settings.subpath == expected
    source = ROOT / "frontend/index.html"
    original = source.read_bytes()
    output = tmp_path / "index.html"
    subprocess.run(
        [
            "sh",
            str(ROOT / "infra/frontend-entrypoint.sh"),
            "--render-only",
            str(source),
            str(output),
        ],
        env={**os.environ, "SUBPATH": prefix},
        check=True,
    )
    assert f'data-mineagent-base href="{expected}/"' in output.read_text()
    assert f'name="mineagent-subpath" content="{expected}"' in output.read_text()
    assert source.read_bytes() == original


@pytest.mark.parametrize("prefix", INVALID)
def test_bad_prefix_fails_closed(prefix, tmp_path):
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql://x:x@localhost/x",
            secret_key="x" * 32,
            subpath=prefix,
        )
    output = tmp_path / "index.html"
    result = subprocess.run(
        [
            "sh",
            str(ROOT / "infra/frontend-entrypoint.sh"),
            "--render-only",
            str(ROOT / "frontend/index.html"),
            str(output),
        ],
        env={**os.environ, "SUBPATH": prefix},
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert not output.exists()


@pytest.mark.parametrize("prefix", ["", "/lab/research-7", "/api"])
def test_strip_prefix_routing_redirects_and_untrusted_header(prefix):
    app = FastAPI()
    app.add_middleware(SubpathMiddleware, subpath=prefix)

    @app.get("/api/v1/probe/")
    def probe(request: Request):
        return {"url": str(request.url), "root": request.scope["root_path"]}

    client = TestClient(app)
    headers = {"X-Forwarded-Prefix": "/attacker"}
    response = client.get("/api/v1/probe", headers=headers, follow_redirects=False)
    assert response.status_code == 307
    assert (
        response.headers["location"]
        == ("" if prefix else "http://testserver") + prefix + "/api/v1/probe/"
    )
    response = client.get("/api/v1/probe/", headers=headers)
    assert response.json() == {
        "url": "http://testserver" + prefix + "/api/v1/probe/",
        "root": prefix,
    }
    schema = client.get("/openapi.json").json()
    if prefix:
        assert schema["servers"] == [{"url": prefix}]


@pytest.mark.parametrize("prefix", ["", "/lab/research-7", "/api"])
def test_real_app_ingress_still_rejects_bad_media_under_prefix(prefix):
    from backend.app.api.deps import reusable_oauth2
    from backend.app.main import create_app

    settings = Settings(
        database_url="postgresql://x:x@localhost/x", secret_key="x" * 32, subpath=prefix
    )
    client = TestClient(create_app(settings))
    # No DB connection: bounded ingress must reject before route dependencies.
    response = client.post(
        "/api/v1/factor-packages/import",
        content=b"not zip",
        headers={"content-type": "text/plain"},
    )
    assert response.status_code == 415
    assert response.json()["code"] == "factor_import_media_type"
    assert reusable_oauth2.model.flows.password.tokenUrl == "api/v1/login/access-token"


def test_prefixed_redirects_preserve_outer_tls_without_trusting_headers():
    from fastapi.responses import RedirectResponse

    app = FastAPI()
    app.add_middleware(SubpathMiddleware, subpath="/lab")

    @app.get("/internal")
    def internal(request: Request):
        return RedirectResponse(str(request.url_for("target")))

    @app.get("/target/")
    def target():
        return {}

    @app.get("/external")
    def external():
        return RedirectResponse("https://elsewhere.example/path")

    client = TestClient(app, base_url="http://internal:8000")
    assert (
        client.get("/internal", follow_redirects=False).headers["location"]
        == "/lab/target/"
    )
    assert (
        client.get("/external", follow_redirects=False).headers["location"]
        == "https://elsewhere.example/path"
    )

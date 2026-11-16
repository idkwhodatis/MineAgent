"""Public deployment contract is independent of database and private server source."""

import secrets
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.core.config import Settings
from backend.app.main import create_app
from backend.app.schemas import ServerInfo


def settings(**kwargs):
    return Settings(
        database_url="postgresql://localhost/not_used",
        secret_key=secrets.token_urlsafe(48),
        **kwargs,
    )


@pytest.mark.parametrize("mode", ["self_hosted", "hosted"])
@pytest.mark.parametrize("subpath", ["", "/mineagent"])
def test_public_contract_needs_no_database_or_credentials(mode, subpath):
    engine = MagicMock()
    app = create_app(settings(deployment_mode=mode, subpath=subpath), engine=engine)
    with TestClient(app) as client:
        response = client.get("/api/v1/server-info")
    assert response.status_code == 200
    assert response.json() == {
        "protocol_version": "1",
        "deployment_mode": mode,
        "display_name": "MineAgent",
        "authentication_methods": ["password_bearer"],
        "capabilities": ["authenticated_sse", "idempotent_run_submission"],
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-correlation-id"]
    engine.connect.assert_not_called()


def test_self_host_is_default_and_name_is_normalized():
    configured = settings(deployment_name="  My server  ")
    assert configured.deployment_mode == "self_hosted"
    assert configured.deployment_name == "My server"


@pytest.mark.parametrize("name", ["", "   ", "x\ny", "x\x00y", "x\x7fy", "x" * 81])
def test_rejects_invalid_display_names(name):
    with pytest.raises(ValidationError):
        settings(deployment_name=name)


def test_rejects_unknown_deployment_mode():
    with pytest.raises(ValidationError):
        settings(deployment_mode="premium")


@pytest.mark.parametrize("mode", ["self_hosted", "hosted"])
def test_deployment_mode_never_bypasses_existing_auth(mode):
    app = create_app(settings(deployment_mode=mode), engine=MagicMock())
    with TestClient(app) as client:
        assert client.get("/api/v1/users/me").status_code == 401
        assert client.get("/api/v1/datasets").status_code == 401


def test_server_info_is_public_in_openapi_and_typed():
    schema = create_app(settings(), engine=MagicMock()).openapi()
    operation = schema["paths"]["/api/v1/server-info"]["get"]
    assert operation["operationId"] == "read_server_info"
    assert not operation.get("security")
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ServerInfo"
    }
    assert set(schema["components"]["schemas"]["ServerInfo"]["required"]) == {
        "protocol_version",
        "deployment_mode",
        "display_name",
        "authentication_methods",
        "capabilities",
    }


@pytest.mark.parametrize(
    "missing", ["protocol_version", "authentication_methods", "capabilities"]
)
def test_contract_does_not_invent_missing_server_capabilities(missing):
    fields = {
        "protocol_version": "1",
        "deployment_mode": "self_hosted",
        "display_name": "MineAgent",
        "authentication_methods": ["password_bearer"],
        "capabilities": ["authenticated_sse", "idempotent_run_submission"],
    }
    del fields[missing]
    with pytest.raises(ValidationError):
        ServerInfo.model_validate(fields)

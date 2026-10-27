"""Authenticated HTTP assertions over an already genuine completed 4-unit run.

No synthetic execution rows, mocked sessions or extra Docker units are created.
"""

import secrets
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.core.security import get_password_hash
from backend.app.execution_snapshot_models import ExecutionSnapshotPublication
from backend.app.factor_analysis_models import FactorDirectAnalysisIntent
from backend.app.factor_models import FactorVersion
from backend.app.main import create_app
from backend.app.models import User


def assert_factor_execution_http(engine, settings, account, factory, analysis_id):
    with factory() as session:
        analysis = session.get(FactorDirectAnalysisIntent, analysis_id)
        publication = session.get(ExecutionSnapshotPublication, analysis.snapshot_id)
        version = session.get(FactorVersion, publication.factor_version_id)
        factor_id, version_id, snapshot_id, run_id = (
            version.factor_id,
            version.id,
            publication.id,
            publication.run_id,
        )
    other_password = secrets.token_urlsafe(24)
    other_email = uuid4().hex + "@example.test"
    with factory.begin() as session:
        session.add(
            User(
                id=uuid4(),
                email=other_email,
                hashed_password=get_password_hash(other_password),
                full_name="Cross-owner navigation check",
            )
        )
    configured = settings.model_copy(update={"factor_library_enabled": True})
    with TestClient(create_app(settings=configured, engine=engine)) as api:
        login = api.post(
            "/api/v1/login/access-token",
            data={"username": account.user.email, "password": account.password},
        )
        assert login.status_code == 200
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        path = f"/api/v1/factors/{factor_id}/versions/{version_id}/execution-records"
        assert api.get(path).status_code in (401, 403)
        library = api.get("/api/v1/factors", headers=headers)
        assert library.status_code == 200, library.text
        entry = next(
            item for item in library.json()["data"] if item["id"] == str(factor_id)
        )
        assert entry["selected_snapshot_id"] is None
        assert entry["coverage"] is None
        assert entry["latest_execution"]["execution_snapshot_id"] == str(snapshot_id)
        assert entry["latest_execution"]["analysis_id"] == str(analysis_id)

        other_login = api.post(
            "/api/v1/login/access-token",
            data={"username": other_email, "password": other_password},
        )
        assert other_login.status_code == 200
        assert (
            api.get(
                path,
                headers={
                    "Authorization": "Bearer " + other_login.json()["access_token"]
                },
            ).status_code
            == 404
        )
        response = api.get(path, headers=headers)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["count"] == 1 and len(data["data"]) == 1
        row = data["data"][0]
        assert row["execution_snapshot_id"] == str(snapshot_id)
        assert row["analysis_id"] == str(analysis_id)
        assert row["run_id"] == str(run_id) and row["factor_version_id"] == str(
            version_id
        )
        assert (
            row["publication_state"] == "completed"
            and row["analysis_state"] == "result_recorded"
        )
        assert not set(row) & {
            "snapshot_id",
            "row_count",
            "valid_count",
            "values",
            "metrics",
            "result",
            "measurement",
            "storage_key",
        }
        assert api.get(path, headers=headers, params={"offset": 1}).json()["data"] == []
        assert (
            api.get(
                path.replace(str(factor_id), str(uuid4())), headers=headers
            ).status_code
            == 404
        )
        assert (
            api.get(
                path.replace(str(version_id), str(uuid4())), headers=headers
            ).status_code
            == 404
        )
        # Dedicated execution UUID must not become a legacy FeatureSnapshot.
        assert (
            api.get(
                f"/api/v1/factor-snapshots/{snapshot_id}", headers=headers
            ).status_code
            == 404
        )

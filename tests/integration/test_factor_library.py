"""Real PostgreSQL factor API/immutability tests; missing server is not a pass."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.app.factor_models import (
    FactorEvaluation,
    FactorObject,
)
from backend.app.models import DataUsageExposure
from packages.factors.fixtures import fixed_factor_code, fixture_definition
from packages.factors.package import decode_package

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def enable_factors(settings):
    settings.factor_library_enabled = True
    settings.data_provider_mode = "fixture"


def post(api, headers, path, body, key=None):
    return api.post(
        "/api/v1/" + path,
        headers={**headers, "Idempotency-Key": key or uuid4().hex},
        json=body,
    )


def manual():
    return {
        "name": "Momentum",
        "definition": fixture_definition("momentum").model_dump(mode="json"),
        "code": fixed_factor_code("momentum"),
    }


def fixture(api, headers, **body):
    response = post(api, headers, "factor-fixtures", {"kind": "momentum", **body})
    assert response.status_code == 201, response.text
    return response.json()


def test_create_exact_source_replay_conflict_and_owner(
    api, owner_headers, other_headers
):
    key = uuid4().hex
    body = manual()
    first = post(api, owner_headers, "factors", body, key)
    assert first.status_code == 201, first.text
    second = post(api, owner_headers, "factors", body, key)
    assert second.status_code == 201, second.text
    assert first.json()["version"]["id"] == second.json()["version"]["id"]
    assert (
        post(api, owner_headers, "factors", {**body, "name": "Other"}, key).status_code
        == 409
    )
    factor = first.json()["factor"]
    version = first.json()["version"]
    assert (
        api.get(f"/api/v1/factors/{factor['id']}", headers=other_headers).status_code
        == 404
    )
    patch = api.patch(
        f"/api/v1/factors/{factor['id']}",
        headers=owner_headers,
        json={"expected_presentation_revision": 1, "bookmarked": True},
    )
    assert patch.status_code == 200, patch.text
    detail = api.get(
        f"/api/v1/factors/{factor['id']}/versions/{version['id']}",
        headers=owner_headers,
    )
    assert detail.json()["version"]["definition_sha256"] == version["definition_sha256"]
    assert detail.json()["version"]["code"] == body["code"]


def test_fixture_actual_values_samples_comparison_and_export(
    api, owner_headers, migrated_engine, owner
):
    first = fixture(api, owner_headers)
    snapshot = first["snapshot"]
    version = first["version"]
    factor = first["factor"]
    page = api.get(
        f"/api/v1/factor-snapshots/{snapshot['id']}/values",
        headers=owner_headers,
        params={
            "listing_id": "fixture:XNAS:AAA",
            "start": "2024-03-08",
            "end": "2024-03-09",
        },
    )
    assert page.status_code == 200, page.text
    assert page.json()["count"] == 1
    assert page.json()["data"][0]["value"] == pytest.approx(0.21)
    sample = api.get(
        f"/api/v1/factor-snapshots/{snapshot['id']}/sample",
        headers=owner_headers,
        params={"listing_id": "fixture:XNAS:AAA", "session_date": "2024-03-08"},
    )
    assert sample.status_code == 200, sample.text
    assert sample.json()["evidence_status"] == "available"
    second = fixture(
        api,
        owner_headers,
        kind="risk_adjusted_momentum",
        factor_id=factor["id"],
        parent_version_id=version["id"],
    )
    assert second["version"]["ordinal"] == 2
    compared = post(
        api,
        owner_headers,
        "factor-comparisons",
        {
            "left": {"version_id": version["id"], "snapshot_id": snapshot["id"]},
            "right": {
                "version_id": second["version"]["id"],
                "snapshot_id": second["snapshot"]["id"],
            },
        },
    )
    assert compared.status_code == 201, compared.text
    paired = api.get(
        f"/api/v1/factor-comparisons/{compared.json()['id']}/values",
        headers=owner_headers,
    )
    assert paired.status_code == 200, paired.text
    assert paired.json()["count"] == 24
    assert all(row["delta"] is None for row in paired.json()["data"])
    package = post(
        api,
        owner_headers,
        f"factor-versions/{version['id']}/exports",
        {"snapshot_ids": [snapshot["id"]], "include_values": True},
    )
    assert package.status_code == 201, package.text
    download = api.get(package.json()["download_url"], headers=owner_headers)
    assert download.status_code == 200, download.text
    decoded = decode_package(download.content)
    assert decoded.manifest["code_sha256"] == version["code_sha256"]
    with Session(migrated_engine) as session:
        assert (
            session.scalar(
                select(DataUsageExposure.id).where(
                    DataUsageExposure.owner_id == owner.user.id
                )
            )
            is not None
        )


def test_immutable_evidence_and_same_owner_snapshot_tuple_rejected(
    api, owner_headers, migrated_engine
):
    first = fixture(api, owner_headers)
    second = fixture(api, owner_headers, kind="risk_adjusted_momentum")
    with Session(migrated_engine) as session:
        with pytest.raises(DBAPIError):
            session.execute(
                text("UPDATE factor_versions SET code=:code WHERE id=:id"),
                {"code": "changed", "id": first["version"]["id"]},
            )
            session.commit()
        session.rollback()
        original = session.get(FactorEvaluation, UUID(first["evaluation"]["id"]))
        values = {
            c.name: getattr(original, c.name)
            for c in FactorEvaluation.__table__.columns
        }
        values["id"] = uuid4()
        values["factor_version_id"] = UUID(second["version"]["id"])
        session.add(FactorEvaluation(**values))
        with pytest.raises(DBAPIError):
            session.commit()
        session.rollback()


def test_tampered_partition_is_not_empty_success(
    api, owner_headers, migrated_engine, settings
):
    first = fixture(api, owner_headers)
    with Session(migrated_engine) as session:
        obj = session.scalar(
            select(FactorObject).where(FactorObject.object_kind == "values_partition")
        )
        path = settings.storage_root / obj.storage_key
        path.chmod(0o600)
        path.write_bytes(b"corrupt")
    response = api.get(
        f"/api/v1/factor-snapshots/{first['snapshot']['id']}/values",
        headers=owner_headers,
    )
    assert response.status_code == 409, response.text


def test_disabled_and_binary_import_fail_closed(api, owner_headers, settings):
    assert (
        api.get("/api/v1/factors/capabilities", headers=owner_headers).json()[
            "package_import_enabled"
        ]
        is False
    )
    assert (
        api.post(
            "/api/v1/factor-packages/import",
            headers={
                **owner_headers,
                "Content-Type": "application/zip",
                "Idempotency-Key": uuid4().hex,
            },
            content=b"not parsed",
        ).status_code
        == 503
    )
    settings.factor_library_enabled = False
    assert api.get("/api/v1/factors", headers=owner_headers).status_code == 403


def test_raw_import_queues_exact_archive_and_pinned_profile(
    api, owner_headers, other_headers, settings
):
    from packages.factors.package import encode_package

    settings.factor_import_enabled = True
    settings.factor_parser_image_id = "sha256:" + "a" * 64
    archive = encode_package({"note": "opaque source claim, not verification"}, {})
    key = uuid4().hex
    headers = {
        **owner_headers,
        "Idempotency-Key": key,
        "Content-Type": "application/zip",
    }
    first = api.post("/api/v1/factor-packages/import", headers=headers, content=archive)
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "queued"
    assert first.json()["parser_image_id"] == settings.factor_parser_image_id
    repeat = api.post(
        "/api/v1/factor-packages/import", headers=headers, content=archive
    )
    assert repeat.status_code == 202, repeat.text
    assert repeat.json()["id"] == first.json()["id"]
    changed = api.post(
        "/api/v1/factor-packages/import",
        headers=headers,
        content=encode_package({"note": "different"}, {}),
    )
    assert changed.status_code == 409, changed.text
    status = api.get(
        "/api/v1/factor-imports/" + first.json()["id"], headers=owner_headers
    )
    assert status.status_code == 200, status.text
    assert "lease_token" not in status.json()
    assert (
        api.get(
            "/api/v1/factor-imports/" + first.json()["id"], headers=other_headers
        ).status_code
        == 404
    )


def test_foreign_snapshot_and_wrong_factor_parent_are_indistinguishable(
    api, owner_headers, other_headers
):
    first = fixture(api, owner_headers)
    other_factor = post(api, owner_headers, "factors", manual()).json()
    source = manual()
    append = {
        "parent_version_id": first["version"]["id"],
        "definition": source["definition"],
        "code": source["code"],
    }
    response = post(
        api, owner_headers, f"factors/{other_factor['factor']['id']}/versions", append
    )
    assert response.status_code == 404, response.text
    for suffix in (
        "",
        "/values",
        "/sample?listing_id=fixture:XNAS:AAA&session_date=2024-03-08",
    ):
        assert (
            api.get(
                f"/api/v1/factor-snapshots/{first['snapshot']['id']}" + suffix,
                headers=other_headers,
            ).status_code
            == 404
        )


def test_concurrent_exact_create_never_duplicates(api, owner_headers, migrated_engine):
    from concurrent.futures import ThreadPoolExecutor

    key = uuid4().hex
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda _: post(api, owner_headers, "factors", manual(), key), range(2)
            )
        )
    assert all(r.status_code in (201, 503) for r in responses), [
        r.text for r in responses
    ]
    # Nonblocking admission can return busy rather than waiting with held locks.
    retry = post(api, owner_headers, "factors", manual(), key)
    assert retry.status_code == 201, retry.text
    successes = [r.json()["version"]["id"] for r in responses if r.status_code == 201]
    assert successes and all(i == retry.json()["version"]["id"] for i in successes)

    # The real library read records exposures under the same owner lock. Its
    # transaction must finish before a separate publication can be admitted.
    from backend.app.factor_models import FeatureSnapshot
    from backend.app.factor_services import expose_snapshot

    observed = fixture(api, owner_headers)
    after_read_key = uuid4().hex
    with Session(migrated_engine) as reading:
        snapshot = reading.get(FeatureSnapshot, UUID(observed["snapshot"]["id"]))
        expose_snapshot(reading, snapshot.owner_id, snapshot, "list")
        busy = post(api, owner_headers, "factors", manual(), after_read_key)
        assert busy.status_code == 503, busy.text
        assert busy.json()["code"] == "factor_busy"
        reading.rollback()
    admitted = post(api, owner_headers, "factors", manual(), after_read_key)
    assert admitted.status_code == 201, admitted.text


def test_imported_known_exposure_enters_owner_classifier_even_without_snapshot_binding(
    api, owner_headers, owner, migrated_engine, settings
):
    from datetime import date

    from fastapi.encoders import jsonable_encoder

    from backend.app.factor_import_services import admit_import_exposures
    from backend.app.factor_services import public, publication
    from packages.factors.contracts import digest

    fixed = fixture(api, owner_headers)
    with Session(migrated_engine) as session:
        source = session.scalar(
            select(DataUsageExposure).where(DataUsageExposure.owner_id == owner.user.id)
        )
        evidence = jsonable_encoder(public(source))
    evidence["end_date"] = (
        "2024-05-01"  # Forward-label/selection exposure extends beyond values.
    )
    evidence["dataset_version_id"] = str(
        uuid4()
    )  # Foreign source UUID is not authority.
    claim = {
        "source": "locally_recorded_claim",
        "evidence_sha256": digest(evidence),
        "dataset_content_sha256": fixed["snapshot"]["dataset_content_sha256"],
        "evidence": evidence,
    }
    bindings = {}
    with Session(migrated_engine) as session:
        with publication(
            session, owner.user.id, settings, peak_bytes=0, peak_files=0
        ) as deadline:
            result = admit_import_exposures(
                session,
                owner.user.id,
                {"exposure_claims": [claim]},
                bindings,
                "b" * 64,
                settings,
                deadline,
            )
        assert len(result["admitted"]) == 1
        exposure = session.get(
            DataUsageExposure, UUID(result["admitted"][0]["local_exposure_id"])
        )
        assert exposure.purpose == "imported_factor_research_exposure"
        assert exposure.end_date == date(2024, 5, 1)
        assert exposure.source_key.startswith("factor:import:")
        assert exposure.provenance["verification"] == "declared_imported_claim"
    mismatch = {
        **evidence,
        "identities": [{"listing_id": str(uuid4()), "market": "US", "symbol": "AAA"}],
    }
    bad = {**claim, "evidence": mismatch, "evidence_sha256": digest(mismatch)}
    with Session(migrated_engine) as session:
        with publication(
            session, owner.user.id, settings, peak_bytes=0, peak_files=0
        ) as deadline:
            result = admit_import_exposures(
                session,
                owner.user.id,
                {"exposure_claims": [bad]},
                {},
                "c" * 64,
                settings,
                deadline,
            )
        assert not result["admitted"]
        assert (
            result["unresolved"][0]["reason"]
            == "source_listing_identity_mapping_unresolved"
        )


def test_completed_code_only_import_with_no_claims_keeps_holdout_history_unknown(
    api, owner_headers, owner, other, migrated_engine, settings
):
    from datetime import date
    from types import SimpleNamespace

    from sqlalchemy.orm import sessionmaker

    from backend.app.experiment_services import (
        classify_exposures,
        has_unknown_import_history,
    )
    from workers.research.factor_import import process_one

    identities = [{"listing_id": "exact-listing", "market": "US", "symbol": "AAA"}]
    window = SimpleNamespace(start=date(2024, 1, 1), end=date(2024, 2, 1))
    with Session(migrated_engine) as session:
        assert has_unknown_import_history(session, owner.user.id) is False
    original = post(api, owner_headers, "factors", manual())
    assert original.status_code == 201, original.text
    package = post(
        api,
        owner_headers,
        f"factor-versions/{original.json()['version']['id']}/exports",
        {},
    )
    assert package.status_code == 201, package.text
    archive = api.get(package.json()["download_url"], headers=owner_headers).content
    settings.factor_import_enabled = True
    settings.factor_parser_image_id = "sha256:" + "a" * 64
    queued = api.post(
        "/api/v1/factor-packages/import",
        headers={
            **owner_headers,
            "Content-Type": "application/zip",
            "Idempotency-Key": uuid4().hex,
        },
        content=archive,
    )
    assert queued.status_code == 202, queued.text

    class NoNativeParser:
        def __init__(self, **kwargs):
            pass

        def run(self, *args, **kwargs):
            raise AssertionError("Code-only evidence must not invoke a native parser")

    assert process_one(
        settings,
        queued.json()["id"],
        factory=sessionmaker(bind=migrated_engine, expire_on_commit=False),
        parser_factory=NoNativeParser,
    )
    status = api.get(
        "/api/v1/factor-imports/" + queued.json()["id"], headers=owner_headers
    )
    assert status.json()["status"] == "succeeded", status.text
    with Session(migrated_engine) as session:
        assert has_unknown_import_history(session, owner.user.id) is True
        assert has_unknown_import_history(session, other.user.id) is False
        result = classify_exposures(
            identities,
            window,
            [],
            unknown_import_history=has_unknown_import_history(session, owner.user.id),
        )
        assert result["classification"] == "unknown_import_history"
        assert result["matching_count"] == 0


def test_execution_record_navigation_is_separate_and_owner_scoped(
    api, owner_headers, other_headers
):
    created = post(api, owner_headers, "factors", manual())
    assert created.status_code == 201, created.text
    body = created.json()
    factor, version = body["factor"]["id"], body["version"]["id"]
    path = f"/api/v1/factors/{factor}/versions/{version}/execution-records"
    response = api.get(path, headers=owner_headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"data": [], "count": 0}
    assert api.get(path, headers=other_headers).status_code == 404
    assert (
        api.get(path.replace(version, str(uuid4())), headers=owner_headers).status_code
        == 404
    )
    assert (
        api.get(path, headers=owner_headers, params={"limit": 101}).status_code == 422
    )

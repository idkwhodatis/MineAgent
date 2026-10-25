"""Import protocol/fencing tests; actual PostgreSQL/Docker acceptance is separate."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from backend.app.models import utcnow
from backend.app.services import DomainError
from packages.factors.fixtures import demo_fixture, fixed_factor_code
from packages.factors.package import decode_package, encode_package
from workers.research.factor_import import (
    Claim,
    _outcome,
    claim_one,
    fenced_admission,
    operation_path,
    validate_payload,
)


def exported():
    fixture = demo_fixture(
        "momentum", factor_version_id=uuid4(), dataset_version_id=uuid4()
    )
    manifest = fixture.manifest
    definition = manifest.input.definition
    payload = {
        "origin_namespace": "test-origin",
        "factor_id": str(uuid4()),
        "version_id": str(manifest.input.version.factor_version_id),
        "parent_version_id": None,
        "ordinal": 1,
        "definition": definition.model_dump(mode="json"),
        "definition_sha256": definition.sha256,
        "code_sha256": definition.code_sha256,
        "source_provenance": {},
        "snapshots": [],
        "source_datasets": [],
        "evaluations": [],
        "include_values": False,
        "history_completeness": "unknown",
        "execution_binding_status": "unresolved",
        "exposure_claims": [],
    }
    objects = {
        f"objects/{definition.code_sha256}.py": fixed_factor_code("momentum").encode()
    }
    return payload, objects, manifest


def test_exact_source_code_only_import_metadata():
    payload, objects, _ = exported()
    definition, code, snapshots = validate_payload(
        decode_package(encode_package(payload, objects))
    )
    assert definition.sha256 == payload["definition_sha256"]
    assert code.encode() == next(iter(objects.values())) and snapshots == []


@pytest.mark.parametrize(
    "change",
    [
        {"definition_sha256": "0" * 64},
        {"include_values": "true"},
        {"exposure_claims": {}},
        {"snapshots": [{}, {}, {}]},
    ],
)
def test_invalid_or_excessive_payload(change):
    payload, objects, _ = exported()
    payload.update(change)
    with pytest.raises((DomainError, ValueError)):
        validate_payload(decode_package(encode_package(payload, objects)))


def test_reference_snapshot_requires_original_manifest():
    payload, objects, manifest = exported()
    payload["snapshots"] = [manifest.model_dump(mode="json")]
    with pytest.raises(DomainError):
        validate_payload(decode_package(encode_package(payload, objects)))


def admission_row():
    return SimpleNamespace(
        id=uuid4(),
        owner_id=uuid4(),
        generation=0,
        lease_token=None,
        lease_expires_at=None,
        archive_object_id=uuid4(),
        archive_sha256="a" * 64,
        parser_image_id="sha256:" + "b" * 64,
        status="queued",
        error_code=None,
        result_envelope_id=None,
    )


def test_claim_is_durable_generation_and_lease():
    row = admission_row()
    session = MagicMock()
    session.scalar.return_value = row
    claim = claim_one(session, row.id)
    assert claim.generation == 1 and claim.token == row.lease_token
    assert row.status == "processing" and row.lease_expires_at > utcnow()
    session.commit.assert_called_once()
    assert fenced_admission(session, claim) is row
    row.generation += 1
    with pytest.raises(DomainError):
        fenced_admission(session, claim)


def test_expired_or_wrong_lease_rejected():
    row = admission_row()
    session = MagicMock()
    session.scalar.return_value = row
    claim = claim_one(session)
    row.lease_expires_at = utcnow() - timedelta(seconds=1)
    with pytest.raises(DomainError):
        fenced_admission(session, claim)
    row.lease_expires_at = utcnow() + timedelta(seconds=60)
    row.lease_token = uuid4()
    with pytest.raises(DomainError):
        fenced_admission(session, claim)


def test_operation_identity_is_generation_and_partition_specific(tmp_path):
    claim = Claim(uuid4(), uuid4(), 1, uuid4(), uuid4(), "a" * 64, "sha256:" + "b" * 64)
    settings = SimpleNamespace(storage_root=tmp_path)
    assert operation_path(settings, claim, 0) != operation_path(settings, claim, 1)
    assert operation_path(settings, claim, 0) == operation_path(settings, claim, 0)
    next_claim = Claim(
        claim.id,
        claim.owner_id,
        2,
        uuid4(),
        claim.archive_object_id,
        claim.archive_sha256,
        claim.image_id,
    )
    assert operation_path(settings, claim, 0) != operation_path(settings, next_claim, 0)


def test_uncertain_commit_preserves_committed_envelope(tmp_path):
    row = admission_row()
    session = MagicMock()
    session.scalar.return_value = row
    claim = claim_one(session)
    envelope = SimpleNamespace(id=uuid4())
    session.scalar.side_effect = [row, envelope]
    factory = MagicMock()
    factory.return_value.__enter__.return_value = session
    assert (
        _outcome(
            factory, claim, "lost_response", SimpleNamespace(storage_root=tmp_path)
        )
        == "succeeded"
    )
    assert row.result_envelope_id == envelope.id and row.lease_token is None


def test_uncertain_commit_does_not_overwrite_new_generation(tmp_path):
    row = admission_row()
    session = MagicMock()
    session.scalar.return_value = row
    claim = claim_one(session)
    row.generation += 1
    session.scalar.side_effect = [row, None]
    factory = MagicMock()
    factory.return_value.__enter__.return_value = session
    assert (
        _outcome(
            factory, claim, "lost_response", SimpleNamespace(storage_root=tmp_path)
        )
        == "superseded"
    )
    assert row.status == "processing"


def test_code_only_finish_never_constructs_unavailable_docker(tmp_path, monkeypatch):
    import hashlib

    from backend.app import factor_import_services
    from workers.research import factor_import as worker

    payload, objects, _ = exported()
    package = decode_package(encode_package(payload, objects))
    row = admission_row()
    session = MagicMock()
    session.scalar.return_value = row
    claim = claim_one(session)
    session.scalar.side_effect = [row, None, None, row]
    session.scalars.return_value.all.return_value = []

    def publish(_session, _owner, kind, content, _settings, _deadline):
        return SimpleNamespace(
            id=uuid4(),
            sha256=hashlib.sha256(content).hexdigest(),
            object_kind=kind,
            size_bytes=len(content),
        )

    monkeypatch.setattr(worker, "publish_object", publish)
    monkeypatch.setattr(
        factor_import_services,
        "admit_import_exposures",
        lambda *args, **kwargs: {"admitted": [], "unresolved": []},
    )
    monkeypatch.setattr("shutil.which", lambda _: None)
    import time

    result = worker._finish(
        session,
        claim,
        package,
        SimpleNamespace(storage_root=tmp_path),
        time.monotonic() + 20,
        worker.DockerParser,
    )
    assert result == row.result_envelope_id and row.status == "succeeded"


@pytest.mark.parametrize("code", ["", "   ", "bad\0source"])
def test_import_rejects_empty_or_nul_source(code):
    import hashlib

    from packages.factors.contracts import digest

    payload, _, _ = exported()
    sha = hashlib.sha256(code.encode()).hexdigest()
    payload["code_sha256"] = sha
    payload["definition"]["code_sha256"] = sha
    payload["definition_sha256"] = digest(payload["definition"])
    with pytest.raises((DomainError, ValueError)):
        validate_payload(
            decode_package(
                encode_package(payload, {f"objects/{sha}.py": code.encode()})
            )
        )


def test_existing_dataset_requires_all_physical_inputs(tmp_path):
    import hashlib
    import time

    from packages.factors.contracts import canonical_bytes
    from workers.research.factor_import import verify_dataset_integrity

    content = b"[]"
    sha = hashlib.sha256(content).hexdigest()
    manifest = {
        "schema_version": "mineagent.factor-fixture-dataset.v1",
        "files": {"observations.json": sha},
    }
    encoded = canonical_bytes(manifest)
    directory = tmp_path / "dataset"
    directory.mkdir()
    (directory / "manifest.json").write_bytes(encoded)
    (directory / "observations.json").write_bytes(content)
    dataset = SimpleNamespace(
        manifest=manifest,
        content_sha256=hashlib.sha256(encoded).hexdigest(),
        storage_key="dataset",
    )
    settings = SimpleNamespace(storage_root=tmp_path)
    verify_dataset_integrity(dataset, settings, time.monotonic() + 20)
    (directory / "observations.json").write_bytes(b"corrupt")
    with pytest.raises(DomainError):
        verify_dataset_integrity(dataset, settings, time.monotonic() + 20)


def value_package(
    *, missing_aux=False, wrong_size=False, wrong_code=False, reference_only=False
):
    import hashlib

    from packages.factors.contracts import ObjectRef, canonical_bytes

    payload, objects, source = exported()
    code = next(iter(objects.values()))
    partition = b"opaque-parquet-fixture"
    aux = b'{"samples":[]}'
    psha = hashlib.sha256(partition).hexdigest()
    asha = hashlib.sha256(aux).hexdigest()
    refs = (
        ObjectRef(
            role="code",
            ordinal=0,
            sha256="0" * 64 if wrong_code else payload["code_sha256"],
            size_bytes=len(code),
            name="code.py",
        ),
        ObjectRef(
            role="values_partition",
            ordinal=0,
            sha256=psha,
            size_bytes=len(partition) + (1 if wrong_size else 0),
            name="values.parquet",
            rows=source.row_count,
        ),
        ObjectRef(
            role="calculation_samples",
            ordinal=0,
            sha256=asha,
            size_bytes=len(aux),
            name="samples.json",
        ),
    )
    manifest = source.model_copy(update={"objects": refs})
    payload["snapshots"] = [manifest.model_dump(mode="json")]
    payload["include_values"] = not reference_only
    objects[f"objects/{manifest.sha256}.json"] = canonical_bytes(manifest)
    if not reference_only:
        objects[f"objects/{psha}.parquet"] = partition
        if not missing_aux:
            objects[f"objects/{asha}.json"] = aux
    return decode_package(encode_package(payload, objects))


def test_value_import_requires_complete_auxiliary_inventory():
    validate_payload(value_package())
    with pytest.raises(DomainError):
        validate_payload(value_package(missing_aux=True))


def test_value_import_rejects_lying_partition_size():
    with pytest.raises(DomainError):
        validate_payload(value_package(wrong_size=True))


def test_snapshot_code_reference_must_match_exact_version_even_reference_only():
    for reference_only in (False, True):
        with pytest.raises(DomainError):
            validate_payload(
                value_package(wrong_code=True, reference_only=reference_only)
            )


def test_reference_only_can_explicitly_omit_values_and_auxiliary_bytes():
    _, _, manifests = validate_payload(value_package(reference_only=True))
    assert len(manifests) == 1

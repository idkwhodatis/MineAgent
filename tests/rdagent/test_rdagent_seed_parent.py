"""Actual bounded retained bytes, DB selection doubles; no026/PG/launch claim."""

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.rdagent_seed_services import (
    require_seeded_submission_authority,
    resolve_seed_parent,
)
from backend.app.services import DomainError
from packages.factors.contracts import (
    FactorDefinition,
    InputField,
    source_sha256,
)
from packages.rdagent_adapter.seed_parent import ResolvedSeedParent, SeedParentReference


@pytest.fixture
def retained_parent(tmp_path):
    owner, factor, version, obj = (uuid4() for _ in range(4))
    source = Path("runtimes/factor_execution/abi_fixture.py").read_text()
    sha = source_sha256(source)
    definition = FactorDefinition(
        code_sha256=sha,
        factor_kind="time_series",
        input_fields=(
            InputField(name="close", unit="USD", price_semantics="unadjusted"),
        ),
        lookback_sessions=2,
        output_unit="return",
    )
    key = f"factors/{owner}/objects/{sha}.py"
    path = tmp_path / key
    path.parent.mkdir(parents=True)
    path.write_bytes(source.encode())
    row = SimpleNamespace(
        id=version,
        owner_id=owner,
        factor_id=factor,
        ordinal=1,
        code_object_id=obj,
        code_sha256=sha,
        code=source,
        definition=definition.model_dump(mode="json"),
        definition_sha256=definition.sha256,
        source_provenance={"schema_version": "retained-test"},
    )
    object_row = SimpleNamespace(
        id=obj,
        owner_id=owner,
        object_kind="code",
        media_type="text/plain",
        sha256=sha,
        size_bytes=len(source.encode()),
        storage_key=key,
    )
    factor_row = SimpleNamespace(id=factor, owner_id=owner)

    def session():
        rows = iter([row, factor_row, object_row])
        return SimpleNamespace(scalar=lambda *_: next(rows))

    return SimpleNamespace(
        owner=owner,
        version=row,
        obj=object_row,
        factor=factor_row,
        path=path,
        session=session,
        settings=SimpleNamespace(storage_root=tmp_path),
    )


def resolve(r):
    return resolve_seed_parent(r.session(), r.owner, r.version.id, r.settings)


def test_actual_parent_bytes_and_definition_are_repeatedly_revalidated(retained_parent):
    r = retained_parent
    first = resolve(r)
    second = resolve(r)
    assert first == second and first.reference.factor_id == r.factor.id
    assert first.reference.definition_sha256 == r.version.definition_sha256
    assert first.source == r.path.read_text()
    assert (
        "owned_parent_input_only_not_new_evaluation_or_adoption"
        in first.prompt_section()
    )
    assert ResolvedSeedParent.model_validate_json(first.model_dump_json()) == first
    r.path.write_bytes(b"tampered")
    with pytest.raises(DomainError):
        resolve(r)


@pytest.mark.parametrize(
    "field,value",
    [
        ("object_kind", "package"),
        ("media_type", "application/json"),
        ("owner_id", None),
        ("sha256", "0" * 64),
        ("size_bytes", True),
        ("size_bytes", 0),
        ("storage_key", "../outside.py"),
    ],
)
def test_object_identity_drift_has_no_seed_authority(retained_parent, field, value):
    r = retained_parent
    setattr(r.obj, field, value)
    with pytest.raises(DomainError):
        resolve(r)


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner_id", None),
        ("factor_id", None),
        ("definition_sha256", "0" * 64),
        ("code", "different"),
        ("ordinal", True),
    ],
)
def test_parent_row_drift_rejected(retained_parent, field, value):
    r = retained_parent
    setattr(r.version, field, value)
    with pytest.raises(DomainError):
        resolve(r)


def test_foreign_or_missing_parent_not_found_before_file_read(retained_parent):
    r = retained_parent
    with pytest.raises(DomainError) as exc:
        resolve_seed_parent(
            SimpleNamespace(scalar=lambda *_: None), r.owner, uuid4(), r.settings
        )
    assert exc.value.code == "not_found"


def test_seed_prompt_is_bounded_without_truncation(retained_parent):
    r = retained_parent
    seed = resolve(r)
    source = "#" * 7000
    reference = seed.reference.model_copy(
        update={"code_sha256": source_sha256(source), "code_size_bytes": len(source)}
    )
    definition = seed.definition.model_copy(
        update={"code_sha256": reference.code_sha256}
    )
    reference = reference.model_copy(update={"definition_sha256": definition.sha256})
    large = ResolvedSeedParent(
        reference=reference, definition=definition, source=source
    )
    with pytest.raises(ValueError, match="never truncate"):
        large.prompt_section()


def test_reference_is_not_a_launch_grant(retained_parent):
    seed = resolve(retained_parent)
    assert (
        SeedParentReference.model_validate_json(seed.reference.model_dump_json())
        == seed.reference
    )
    with pytest.raises(DomainError) as exc:
        require_seeded_submission_authority(seed)
    assert exc.value.code == "seeded_submission_not_enabled"


def seeded_request(parent_id):
    from datetime import date

    from backend.app.engine_run_preflight import RECOMMENDED_BUDGET
    from backend.app.engine_run_submission_schemas import RDSubmissionRequest
    from backend.app.rdagent_seed_contracts import RDSeededSubmissionRequest
    from packages.factors.contracts import DateWindow
    from packages.qlib_adapter.objective import DirectObjectiveDeclaration
    from packages.rdagent_adapter.configured_source import ConfiguredSourcePolicy
    from packages.rdagent_adapter.contracts import FactorTaskSpec

    window = DateWindow(start=date(2024, 3, 13), end=date(2024, 3, 14))
    request = RDSubmissionRequest(
        dataset_version_id=uuid4(),
        window=window,
        input_fields=("close",),
        lookback_sessions=2,
        lag_sessions=0,
        task=FactorTaskSpec(
            name="seed-refinement",
            description="Refine owned source",
            formulation="close[t]/close[t-2]-1",
            factor_kind="time_series",
            output_unit="return",
            interpretation="unsigned",
        ),
        objective=DirectObjectiveDeclaration(
            window=window,
            label_horizon_sessions=1,
            metric="rank_ic",
            direction="at_least",
            threshold=0.9,
            minimum_defined_days=1,
            minimum_pairs=3,
        ),
        budget=RECOMMENDED_BUDGET,
        max_iterations=1,
        source_policy=ConfiguredSourcePolicy(acknowledged_unverified=True),
    )
    raw = request.model_dump(mode="json")
    raw["schema_version"] = "mineagent.rdagent-seeded-submission.v1"
    raw["parent_factor_version_id"] = str(parent_id)
    return RDSeededSubmissionRequest.model_validate(raw)


def test_seeded_request_never_sneaks_into_fresh023_contract():
    from backend.app.engine_run_submission_schemas import RDSubmissionRequest
    from backend.app.rdagent_seed_contracts import RDSeededSubmissionRequest

    seeded = seeded_request(uuid4())
    raw = seeded.model_dump(mode="json")
    assert (
        RDSeededSubmissionRequest.model_validate_json(seeded.model_dump_json())
        == seeded
    )
    with pytest.raises(ValueError):
        RDSubmissionRequest.model_validate(raw)
    with pytest.raises(ValueError):
        RDSeededSubmissionRequest.model_validate({**raw, "parent_code": "caller code"})


@pytest.fixture
def saved_seed(retained_parent):
    from packages.factors.contracts import canonical_bytes, digest

    parent = resolve(retained_parent)
    ref = parent.reference
    request = seeded_request(ref.factor_version_id)
    row = SimpleNamespace(
        owner_id=ref.owner_id,
        parent_factor_id=ref.factor_id,
        parent_factor_version_id=ref.factor_version_id,
        parent_ordinal=ref.ordinal,
        parent_code_object_id=ref.code_object_id,
        parent_code_sha256=ref.code_sha256,
        parent_code_size_bytes=ref.code_size_bytes,
        parent_definition_sha256=ref.definition_sha256,
        parent_provenance_sha256=ref.source_provenance_sha256,
        parent_provenance_canonical=canonical_bytes(
            retained_parent.version.source_provenance
        ).decode("ascii"),
        parent_binding_sha256=ref.sha256,
        request_canonical=canonical_bytes(request).decode("ascii"),
        request_sha256=digest(request),
    )
    return row, request, ref


def test_saved_reference_decodes_exact_canonical_request(saved_seed):
    from backend.app.rdagent_seed_services import read_saved_seed

    row, request, ref = saved_seed
    assert read_saved_seed(row) == (request, ref)


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_canonical", "{}"),
        ("request_sha256", "f" * 64),
        ("parent_binding_sha256", "f" * 64),
        ("parent_ordinal", True),
        ("parent_factor_id", uuid4()),
        ("parent_code_object_id", uuid4()),
        ("parent_code_sha256", "f" * 64),
        ("parent_code_size_bytes", 1),
        ("parent_definition_sha256", "f" * 64),
        ("parent_provenance_sha256", "f" * 64),
        ("parent_provenance_canonical", "{}"),
        ("parent_provenance_canonical", '{"x":1,"x":2}'),
    ],
)
def test_saved_seed_rejects_any_reference_drift(saved_seed, field, value):
    from backend.app.rdagent_seed_services import read_saved_seed

    row, _, _ = saved_seed
    setattr(row, field, value)
    with pytest.raises(DomainError) as exc:
        read_saved_seed(row)
    assert exc.value.code == "seed_submission_drift"


def test_saved_receipt_does_not_reread_deleted_parent(saved_seed, retained_parent):
    from backend.app.rdagent_seed_services import read_saved_seed

    row, request, ref = saved_seed
    retained_parent.path.unlink()
    assert read_saved_seed(row) == (request, ref)


def test_new_work_rereads_saved_parent_bytes(saved_seed, retained_parent):
    from backend.app.rdagent_seed_services import reread_saved_seed

    row, _, ref = saved_seed
    r = retained_parent
    assert reread_saved_seed(r.session(), r.owner, row, r.settings).reference == ref
    r.path.write_bytes(b"changed after declaration")
    with pytest.raises(DomainError):
        reread_saved_seed(r.session(), r.owner, row, r.settings)


def test_new_work_rejects_parent_metadata_drift(saved_seed, retained_parent):
    from backend.app.rdagent_seed_services import reread_saved_seed

    row, _, _ = saved_seed
    r = retained_parent
    r.version.source_provenance = {"schema_version": "changed-but-valid"}
    with pytest.raises(DomainError) as exc:
        reread_saved_seed(r.session(), r.owner, row, r.settings)
    assert exc.value.code == "seed_parent_drift"


def test_foreign_seed_rejected_before_database_or_files(saved_seed, retained_parent):
    from backend.app.rdagent_seed_services import reread_saved_seed

    row, _, _ = saved_seed
    with pytest.raises(DomainError) as exc:
        reread_saved_seed(None, uuid4(), row, retained_parent.settings)
    assert exc.value.code == "not_found"

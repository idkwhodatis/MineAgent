"""Real prefix/input codecs with scoped persistence doubles, not PG/launch proof."""

import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_rdagent_configured_source import retained  # noqa: F401
from test_rdagent_configured_source_service import setup_source  # noqa: F401

from backend.app import rdagent_configured_inputs as service
from backend.app.factor_models import FactorVersion
from backend.app.rdagent_configured_input_models import FactorConfiguredInputWitness
from backend.app.rdagent_models import (
    FactorCutoffExecution,
    FactorEvaluationStart,
    FactorGenerationSource,
)
from backend.app.research_process_models import ResearchProcessAdmission
from packages.factors.contracts import ExposureHistory, digest
from packages.rdagent_adapter.host_evidence import research_snapshot_input
from packages.research_runtime.process_contracts import ProcessError
from packages.research_runtime.process_declarations_v2 import (
    NoIPC,
    ProcessDeclarationV2,
)
from packages.research_runtime.process_persistence_codec import encode_row


@pytest.fixture
def case(setup_source, monkeypatch):  # noqa: F811
    s = setup_source
    s.fence.attempt_id = uuid4()
    s.fence.generation = 1
    scope = {
        key: getattr(s.fence, key)
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    }
    code = b"# maintained data-only fixture\nvalue = 1\n"
    version = FactorVersion(
        id=uuid4(), owner_id=s.fence.owner_id, code_object_id=uuid4()
    )
    source = FactorGenerationSource(
        id=uuid4(),
        **scope,
        start_id=uuid4(),
        factor_version_id=version.id,
        source_sha256=hashlib.sha256(code).hexdigest(),
    )
    snapshot = research_snapshot_input(
        s.config,
        factor_version_id=version.id,
        code_sha256=source.source_sha256,
        exposure_history=ExposureHistory(),
    )
    evaluation = FactorEvaluationStart(
        id=uuid4(),
        **scope,
        start_id=source.start_id,
        evaluated_source_id=source.id,
        factor_version_id=version.id,
        snapshot_input=snapshot.model_dump(mode="json"),
        input_sha256=snapshot.sha256,
        expected_cutoffs=1,
    )
    resolved_code = SimpleNamespace(
        start_id=source.start_id,
        source_id=source.id,
        factor_version_id=version.id,
        code_utf8=code,
        code_sha256=source.source_sha256,
        code_object_id=version.code_object_id,
        read_witness_id=uuid4(),
        read_witness_sha256="c" * 64,
    )
    scoped = {FactorEvaluationStart: evaluation, FactorGenerationSource: source}
    scope_reads = []

    def scope_row(session, model, row_id, fence, **kw):
        row = scoped[model]
        assert row.id == row_id and all(
            getattr(row, key) == value for key, value in scope.items()
        )
        scope_reads.append((model, kw))
        return row

    monkeypatch.setattr(service, "_scope_row", scope_row)
    monkeypatch.setattr(
        service,
        "require_research_spec",
        lambda session, fence: (SimpleNamespace(id=s.plan.spec_id), s.config),
    )
    code_reads = []

    def read_code(session, fence, **kw):
        assert (
            kw["source_id"] == source.id
            and kw["expected_factor_version_id"] == version.id
        )
        assert (
            kw["expected_code_object_id"] == version.code_object_id
            and kw["expected_sha256"] == source.source_sha256
        )
        code_reads.append(kw)
        return resolved_code

    monkeypatch.setattr(service, "require_source_code", read_code)
    base_scalar = s.session.scalar
    existing = []
    added = []
    flushed = []

    def scalar(query):
        entity = query.column_descriptions[0]["entity"]
        if entity is FactorVersion:
            params = query.compile().params
            assert s.fence.owner_id in params.values() and version.id in params.values()
            return version
        if entity is FactorConfiguredInputWitness:
            return existing[0] if existing else None
        return base_scalar(query)

    s.session.scalar = scalar
    s.session.add = lambda row: (added.append(row), existing.append(row))
    s.session.flush = lambda: flushed.append(True)
    s.source_row = source
    s.version = version
    s.evaluation = evaluation
    s.code = resolved_code
    s.scoped = scoped
    s.existing = existing
    s.added = added
    s.flushed = flushed
    s.code_reads = code_reads
    s.scope_reads = scope_reads
    s.resolved = service.resolve_candidate_inputs(
        s.session,
        s.fence,
        evaluation_id=evaluation.id,
        cutoff_ordinal=0,
        settings=s.settings,
    )
    execution = FactorCutoffExecution(
        id=uuid4(),
        **scope,
        evaluation_id=evaluation.id,
        cutoff_ordinal=0,
        candidate_admission_id=uuid4(),
        start_id=source.start_id,
        evaluated_source_id=source.id,
        factor_version_id=version.id,
        projection=s.resolved.prefix.model_dump(mode="json"),
        projection_sha256=s.resolved.prefix.sha256,
    )
    admission = ProcessDeclarationV2(
        schema_version="mineagent.process-admission.v2",
        id=execution.candidate_admission_id,
        **scope,
        role="factor_cutoff_candidate",
        logical_operation_id=execution.id,
        launch_ordinal=1,
        profile=s.resolved.prefix.candidate_profile,
        input_sha256=digest(s.resolved.binding.model_dump(mode="json")),
        reservation_id=uuid4(),
        image_id=s.resolved.prefix.candidate_image,
        ipc=NoIPC(ipc_policy="none", ticket_sha256=None),
    )
    s.admission = ResearchProcessAdmission(**encode_row(admission))
    s.execution = execution
    scoped.update(
        {FactorCutoffExecution: execution, ResearchProcessAdmission: s.admission}
    )
    s.auth = []
    monkeypatch.setattr(
        service,
        "require_rd_controller",
        lambda session, fence, authorization: s.auth.append(authorization),
    )
    return s


def resolve(s, ordinal=0):
    return service.resolve_candidate_inputs(
        s.session,
        s.fence,
        evaluation_id=s.evaluation.id,
        cutoff_ordinal=ordinal,
        settings=s.settings,
    )


def require(s):
    return service.require_configured_input_witness(
        s.session, s.fence, execution_id=s.execution.id, settings=s.settings
    )


def persist(s, auth="host-peer-double"):
    return service.persist_configured_input_witness(
        s.session, s.fence, auth, execution_id=s.execution.id, settings=s.settings
    )


def test_actual_four_file_prefix_and_full_input_commitment(case):
    s = case
    value = resolve(s)
    files = dict(value.files)
    assert set(files) == {
        "/input/source.py",
        "/input/observations.json",
        "/input/listings.json",
        "/input/manifest.json",
    }
    assert files["/input/source.py"] == s.code.code_utf8
    for item in value.binding.files:
        assert item.sha256 == hashlib.sha256(files[item.destination]).hexdigest()
        assert item.size_bytes == len(files[item.destination])
    assert value.witness["input_sha256"] == digest(
        value.binding.model_dump(mode="json")
    )
    assert value.witness["source_read_witness_id"] == str(s.code.read_witness_id)
    assert (
        value.witness["availability_revision_sha256"]
        == s.source.dataset.availability_revision_sha256
    )
    assert value.prefix.sha256 == value.binding.prefix_sha256
    observations = json.loads(files["/input/observations.json"])
    assert b"2024-03-13" not in files["/input/observations.json"]
    assert b"close" not in files["/input/observations.json"]
    assert observations
    assert len(s.code_reads) == 2


def test_readonly_never_creates_first_witness(case):
    with pytest.raises(ProcessError, match="configured_witness_missing"):
        require(case)
    assert not case.added and not case.flushed and not case.auth


def test_first_append_auth_then_exact_replay_without_new_append(case):
    row, resolved = persist(case)
    assert type(row) is FactorConfiguredInputWitness
    assert (
        case.auth == ["host-peer-double"] and len(case.added) == len(case.flushed) == 1
    )
    replay, new_resolved = require(case)
    assert replay is row and new_resolved == resolved
    assert row.witness_sha256 == digest(row.document)
    assert len(case.added) == len(case.flushed) == 1


def test_controller_refusal_before_any_new_append(case, monkeypatch):
    def refuse(*args):
        raise ProcessError("dead_controller")

    monkeypatch.setattr(service, "require_rd_controller", refuse)
    with pytest.raises(ProcessError, match="dead_controller"):
        persist(case)
    assert not case.added


@pytest.mark.parametrize("ordinal", [True, -1, 1, 1.0, "0", None])
def test_cutoff_ordinal_strict_and_bounded(case, ordinal):
    with pytest.raises(ProcessError, match="configured_evaluation_drift"):
        resolve(case, ordinal)


@pytest.mark.parametrize(
    "field", ["input_sha256", "start_id", "factor_version_id", "expected_cutoffs"]
)
def test_evaluation_exact_bindings(case, field):
    setattr(
        case.evaluation,
        field,
        2
        if field == "expected_cutoffs"
        else "f" * 64
        if field == "input_sha256"
        else uuid4(),
    )
    with pytest.raises(ProcessError, match="configured_evaluation_drift"):
        resolve(case)


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_sha256", "f" * 64),
        ("logical_operation_id", None),
        ("launch_ordinal", 2),
        ("profile", "wrong.profile.v1"),
        ("image_id", "sha256:" + "e" * 64),
        ("role", "factor_normalizer"),
    ],
)
def test_admission_full_commitment_role_image_ordinal(case, field, value):
    setattr(case.admission, field, uuid4() if value is None else value)
    with pytest.raises(ProcessError, match="configured_cutoff_input_drift"):
        persist(case)
    assert not case.added


def test_candidate_cannot_gain_ipc(case):
    case.admission.ipc_policy = "host_ticket"
    case.admission.ticket_sha256 = "f" * 64
    with pytest.raises(ValueError):
        persist(case)
    assert not case.added


@pytest.mark.parametrize(
    "field",
    ["start_id", "evaluated_source_id", "factor_version_id", "projection_sha256"],
)
def test_execution_source_projection_binding(case, field):
    setattr(
        case.execution, field, "f" * 64 if field == "projection_sha256" else uuid4()
    )
    with pytest.raises(ProcessError, match="configured_cutoff_input_drift"):
        persist(case)
    assert not case.added


@pytest.mark.parametrize(
    "field",
    [
        "owner_id",
        "plan_id",
        "source_read_witness_id",
        "dataset_id",
        "input_sha256",
        "witness_sha256",
        "document",
    ],
)
def test_existing_witness_replay_drift_never_overwrites(case, field):
    row, _ = persist(case)
    setattr(
        row,
        field,
        {}
        if field == "document"
        else "f" * 64
        if field.endswith("sha256")
        else uuid4(),
    )
    with pytest.raises(ProcessError, match="configured_witness_replay_drift"):
        require(case)
    assert len(case.added) == len(case.flushed) == 1


def test_actual_retained_input_mutation_blocks_replay(case):
    persist(case)
    path = case.directory / "preview.json"
    path.write_bytes(path.read_bytes() + b" ")
    from backend.app.services import DomainError

    with pytest.raises(DomainError):
        require(case)
    assert len(case.added) == 1

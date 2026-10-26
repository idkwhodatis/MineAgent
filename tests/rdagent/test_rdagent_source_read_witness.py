"""Actual retained-file reads/parsing with DB/ledger authority doubles only."""

import hashlib
from types import SimpleNamespace
from uuid import UUID

import pytest

from backend.app import rdagent_services as domain
from backend.app import rdagent_source_witness_services as service
from backend.app.factor_models import FactorObject, FactorVersion
from backend.app.rdagent_models import (
    FactorGenerationModelOperation,
    FactorGenerationSource,
    FactorResponseAssembly,
    FactorResponseAssemblyPart,
    FactorUpstreamCall,
    FactorUpstreamCallOutcome,
)
from backend.app.rdagent_source_witness_models import (
    FactorSourceReadPart,
    FactorSourceReadWitness,
)
from backend.app.research_broker_models import ResearchModelOperation
from backend.app.services import DomainError
from backend.app.storage import read_verified
from packages.rdagent_adapter.host_evidence import (
    ModelResponsePart,
    ResponseAssemblyDocument,
)
from packages.research_runtime.engine_broker_contracts import RDChatResponse
from packages.research_runtime.fences import AttemptFence
from packages.research_runtime.process_contracts import digest


class Session:
    def __init__(self):
        self.rows = []
        self.flushes = 0

    def add(self, row):
        self.rows.append(row)

    def flush(self):
        self.flushes += 1

    def scalars(self, query):
        model = query.column_descriptions[0]["entity"]
        rows = [row for row in self.rows if isinstance(row, model)]
        for clause in query._where_criteria:
            rows = [
                row
                for row in rows
                if getattr(row, clause.left.key) == clause.right.value
            ]
        for clause in query._order_by_clauses:
            rows.sort(key=lambda row: getattr(row, clause.key))
        return SimpleNamespace(all=lambda: rows)

    def scalar(self, query):
        return next(iter(self.scalars(query).all()), None)


@pytest.fixture
def setup(monkeypatch, tmp_path):
    fence = AttemptFence(UUID(int=1), UUID(int=2), UUID(int=3), 1, UUID(int=4))
    scope = domain._scope(fence)
    session = Session()
    context = UUID(int=5)
    start = UUID(int=6)
    launch = SimpleNamespace(id=UUID(int=7))
    call = FactorUpstreamCall(
        id=UUID(int=8),
        **scope,
        start_id=start,
        context_id=context,
        phase="generation",
        kind="chat",
    )
    assembly = FactorResponseAssembly(
        id=UUID(int=9),
        **scope,
        start_id=start,
        context_id=context,
        call_id=call.id,
        object_id=UUID(int=10),
        object_kind="factor_response_assembly",
        part_count=2,
    )
    code = "print(1)\n"
    sha = hashlib.sha256(code.encode()).hexdigest()
    obj = FactorObject(
        id=UUID(int=11),
        owner_id=fence.owner_id,
        object_kind="code",
        media_type="text/plain",
        sha256=sha,
        size_bytes=len(code.encode()),
        storage_key="source.py",
    )
    definition = {"code_sha256": sha}
    version = FactorVersion(
        id=UUID(int=12),
        owner_id=fence.owner_id,
        code=code,
        code_sha256=sha,
        code_object_id=obj.id,
        definition=definition,
        definition_sha256=digest(definition),
    )
    source = FactorGenerationSource(
        id=UUID(int=13),
        **scope,
        start_id=start,
        context_id=context,
        call_id=call.id,
        assembly_id=assembly.id,
        factor_version_id=version.id,
        source_sha256=sha,
    )
    closed = FactorUpstreamCallOutcome(
        id=UUID(int=14),
        **scope,
        call_id=call.id,
        status="completed",
        controller_launch_id=launch.id,
    )
    session.rows.extend([call, assembly, obj, version, source, closed])
    (tmp_path / "source.py").write_bytes(code.encode())
    responses = []
    members = []
    parts = []
    blobs = {}
    outcomes = {}
    for index, (content, finish) in enumerate(
        [('{"code":"pri', "length"), ('nt(1)\\n"}', "stop")]
    ):
        response = RDChatResponse(content=content, finish_reason=finish)
        raw = response.model_dump_json().encode()
        object_id = UUID(int=30 + index)
        outcome_id = UUID(int=40 + index)
        path = tmp_path / f"{object_id}.json"
        path.write_bytes(raw)
        blobs[object_id] = (path.name, hashlib.sha256(raw).hexdigest())
        member = FactorGenerationModelOperation(
            id=UUID(int=50 + index),
            **scope,
            call_id=call.id,
            start_id=start,
            context_id=context,
            operation_id=UUID(int=60 + index),
            request_ordinal=index,
            relation="initial" if index == 0 else "continuation",
            protocol="mineagent.rdagent-chat.v1",
        )
        operation = ResearchModelOperation(
            id=member.operation_id,
            **scope,
            context_id=context,
            request={
                "kind": "chat",
                "model": "fixture.rdagent.v1",
                "messages": [{"role": "user", "content": "json code"}],
                "max_output_tokens": 100,
                "temperature_millionths": 0,
                "response_format": {"type": "json_object"},
            },
        )
        part = FactorResponseAssemblyPart(
            id=UUID(int=70 + index),
            **scope,
            assembly_id=assembly.id,
            call_id=call.id,
            member_id=member.id,
            outcome_id=outcome_id,
            response_object_id=object_id,
            response_object_kind="model_response",
            response_sha256=blobs[object_id][1],
            ordinal=index,
        )
        session.rows.extend([member, operation, part])
        members.append(member)
        parts.append(part)
        responses.append(response)
        outcomes[member.id] = SimpleNamespace(
            id=outcome_id,
            response_object_id=object_id,
            response_sha256=part.response_sha256,
        )
    document = ResponseAssemblyDocument(
        call_id=call.id,
        orchestrator_admission_id=UUID(int=99),
        parts=tuple(
            ModelResponsePart(
                member_id=p.member_id,
                outcome_id=p.outcome_id,
                response_object_id=p.response_object_id,
                response_sha256=p.response_sha256,
            )
            for p in parts
        ),
        source=code,
        source_sha256=sha,
    )
    raw = document.model_dump_json().encode()
    (tmp_path / "assembly.json").write_bytes(raw)
    assembly.object_sha256 = hashlib.sha256(raw).hexdigest()
    blobs[assembly.object_id] = ("assembly.json", assembly.object_sha256)
    settings = SimpleNamespace(storage_root=tmp_path)
    reads = []

    def read(session, fence, identity, kind, expected, settings):
        path, sha = blobs[identity]
        assert expected == sha
        reads.append(identity)
        return read_verified(settings.storage_root, path, sha, 262144)

    def completed(session, fence, member, settings):
        outcome = outcomes[member.id]
        raw = read(
            session,
            fence,
            outcome.response_object_id,
            "model_response",
            outcome.response_sha256,
            settings,
        )
        return outcome, RDChatResponse.model_validate_json(raw)

    monkeypatch.setattr(service, "require_neutral_transaction", lambda *a, **k: None)
    monkeypatch.setattr(service, "require_recorded_controller", lambda *a: launch)
    monkeypatch.setattr(service, "read_research_object", read)
    monkeypatch.setattr(domain, "_completed_member_response", completed)

    def invoke(create=True):
        return service.persist_source_read_witness(
            session, fence, source_id=source.id, settings=settings, allow_create=create
        )

    return SimpleNamespace(
        invoke=invoke,
        session=session,
        source=source,
        assembly=assembly,
        document=document,
        parts=parts,
        operations=[
            row for row in session.rows if isinstance(row, ResearchModelOperation)
        ],
        members=members,
        blobs=blobs,
        root=tmp_path,
        reads=reads,
        closed=closed,
        settings=settings,
        fence=fence,
        obj=obj,
    )


def test_witness_comes_from_actual_retained_files_not_declared_source(setup):
    witness = setup.invoke()
    assert witness.part_count == 2 and witness.selected_first_ordinal == 0
    assert witness.source_sha256 == setup.source.source_sha256
    assert witness.raw_text_sha256 != witness.source_sha256
    assert len(setup.reads) == 3
    assert setup.session.flushes == 2
    assert not hasattr(witness, "raw_text") and not hasattr(witness, "normalized_json")
    rows = [row for row in setup.session.rows if isinstance(row, FactorSourceReadPart)]
    assert [row.finish_reason for row in rows] == ["length", "stop"]
    assert sum(row.content_size_bytes for row in rows) == witness.raw_text_bytes
    assert setup.invoke(False) is witness and setup.session.flushes == 2
    assert len(setup.reads) == 6  # Exact replay still rereads immutable object bytes.


@pytest.mark.parametrize(
    "case",
    [
        "missing_witness_replay",
        "wrong_phase",
        "open_call",
        "wrong_controller",
        "wrong_source_kind",
        "raw_corruption",
        "source_corruption",
        "missing_part",
        "ordinal_gap",
        "response_object_swap",
        "nonjson_request",
    ],
)
def test_broken_read_parse_provenance_cannot_publish_witness(setup, case):
    if case == "wrong_phase":
        next(
            row for row in setup.session.rows if isinstance(row, FactorUpstreamCall)
        ).phase = "critique"
    if case == "open_call":
        setup.closed.status = "aborted"
    if case == "wrong_controller":
        setup.closed.controller_launch_id = UUID(int=999)
    if case == "wrong_source_kind":
        setup.obj.object_kind = "native_hdf"
    if case == "raw_corruption":
        (setup.root / setup.blobs[setup.parts[0].response_object_id][0]).write_bytes(
            b"{}"
        )
    if case == "source_corruption":
        (setup.root / "source.py").write_bytes(b"changed")
    if case == "missing_part":
        setup.session.rows.remove(setup.parts[1])
    if case == "ordinal_gap":
        setup.parts[1].ordinal = 5
    if case == "response_object_swap":
        setup.parts[0].response_object_id = setup.parts[1].response_object_id
    if case == "nonjson_request":
        setup.operations[0].request["response_format"] = None
    with pytest.raises((DomainError, ValueError)):
        setup.invoke(case != "missing_witness_replay")
    assert not any(
        isinstance(row, FactorSourceReadWitness) for row in setup.session.rows
    )
    assert setup.session.flushes == 0


def test_controller_declared_code_must_equal_host_parsed_response(setup):
    changed = setup.document.model_copy(
        update={
            "source": "pass\n",
            "source_sha256": hashlib.sha256(b"pass\n").hexdigest(),
        }
    )
    raw = changed.model_dump_json().encode()
    (setup.root / "assembly.json").write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    setup.assembly.object_sha256 = sha
    setup.blobs[setup.assembly.object_id] = ("assembly.json", sha)
    with pytest.raises(DomainError):
        setup.invoke()
    assert setup.session.flushes == 0


@pytest.mark.parametrize(
    "field", ["raw_text_sha256", "normalized_json_bytes", "controller_launch_id"]
)
def test_existing_witness_metadata_drift_is_not_silently_repaired(setup, field):
    witness = setup.invoke()
    value = getattr(witness, field)
    setattr(
        witness,
        field,
        UUID(int=999)
        if isinstance(value, UUID)
        else (value + 1 if isinstance(value, int) else "0" * 64),
    )
    with pytest.raises(DomainError):
        setup.invoke(False)
    assert setup.session.flushes == 2


def test_existing_witness_part_order_drift_rejected(setup):
    setup.invoke()
    rows = [row for row in setup.session.rows if isinstance(row, FactorSourceReadPart)]
    rows[1].ordinal = 4
    with pytest.raises(DomainError):
        setup.invoke(False)
    assert setup.session.flushes == 2


def test_new_witness_rejected_after_terminal_but_exact_replay_remains(setup):
    from backend.app.rdagent_models import FactorGenerationOutcome

    terminal = FactorGenerationOutcome(
        id=UUID(int=999), **domain._scope(setup.fence), start_id=setup.source.start_id
    )
    setup.session.add(terminal)
    with pytest.raises(DomainError):
        setup.invoke()
    assert setup.session.flushes == 0
    setup.session.rows.remove(terminal)
    witness = setup.invoke()
    setup.session.add(terminal)
    assert setup.invoke(False) is witness
    assert setup.session.flushes == 2

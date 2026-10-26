"""Service/storage doubles only; v2 DB admission and object codecs remain closed."""

import hashlib
from dataclasses import asdict
from types import SimpleNamespace
from uuid import UUID

import pytest
from test_rdagent_numeric_profile import projected

from backend.app import rdagent_input_services as service
from backend.app.rdagent_input_models import (
    FactorCandidateInputFile,
    FactorCandidateInputInventory,
)
from backend.app.research_object_models import ResearchObjectPublication
from backend.app.services import DomainError
from packages.rdagent_adapter.numeric_profile import candidate_inputs
from packages.research_runtime.fences import AttemptFence
from packages.research_runtime.process_contracts import digest
from packages.research_runtime.process_declarations_v2 import (
    NoIPC,
    ProcessDeclarationV2,
)
from packages.research_runtime.process_persistence_codec import encode_row


class Session:
    def __init__(self):
        self.added = []
        self.flushes = 0
        self.outcomes = {}
        self.terminal = None

    def add(self, row):
        self.added.append(row)

    def flush(self):
        self.flushes += 1

    def get(self, model, key):
        return self.outcomes.get(key)

    def scalar(self, query):
        model = query.column_descriptions[0]["entity"]
        if model is FactorCandidateInputInventory:
            return next((row for row in self.added if isinstance(row, model)), None)
        return self.terminal

    def scalars(self, query):
        return SimpleNamespace(
            all=lambda: sorted(
                (
                    row
                    for row in self.added
                    if isinstance(row, FactorCandidateInputFile)
                ),
                key=lambda row: row.ordinal,
            )
        )


@pytest.fixture
def setup(monkeypatch, tmp_path):
    config, snapshot, prefix, files, source, sha = projected()
    bindings, inputs, _ = candidate_inputs(
        prefix,
        snapshot_input=snapshot,
        source=source,
        expected_source_sha256=sha,
        projected_files=files,
    )
    fence = AttemptFence(
        owner_id=UUID(int=2),
        run_id=UUID(int=3),
        attempt_id=UUID(int=4),
        generation=1,
        lease_token=UUID(int=5),
    )
    scope = {
        key: getattr(fence, key)
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    }
    execution = SimpleNamespace(
        id=UUID(int=6),
        candidate_admission_id=UUID(int=7),
        factor_version_id=UUID(int=1),
        evaluation_id=UUID(int=8),
        start_id=UUID(int=9),
        projection=prefix.model_dump(mode="json"),
        projection_sha256=prefix.sha256,
        **scope,
    )
    declaration = ProcessDeclarationV2(
        schema_version="mineagent.process-admission.v2",
        id=execution.candidate_admission_id,
        **scope,
        role="factor_cutoff_candidate",
        logical_operation_id=execution.id,
        launch_ordinal=1,
        profile=config.runtime.candidate_profile,
        image_id=config.runtime.candidate_image,
        input_sha256=digest([asdict(item) for item in bindings]),
        reservation_id=UUID(int=10),
        ipc=NoIPC(ipc_policy="none", ticket_sha256=None),
    )
    admission = SimpleNamespace(**encode_row(declaration))
    version = SimpleNamespace(
        code_object_id=UUID(int=11), code_sha256=sha, code=source.decode()
    )
    (tmp_path / "source.py").write_bytes(source)
    obj = SimpleNamespace(
        id=version.code_object_id,
        object_kind="code",
        sha256=sha,
        size_bytes=len(source),
        storage_key="source.py",
    )
    ticket = SimpleNamespace(
        start_id=execution.start_id,
        factor_version_id=execution.factor_version_id,
        snapshot_input=snapshot.model_dump(mode="json"),
        input_sha256=snapshot.sha256,
    )
    rows = {
        execution.id: execution,
        admission.id: admission,
        execution.evaluation_id: ticket,
    }
    session = Session()
    publications = {}
    contents = {}
    for ordinal, (destination, kind) in enumerate(service.KINDS.items(), 20):
        raw = inputs[destination]
        identity = UUID(int=ordinal)
        object_id = UUID(int=ordinal + 10)
        publication = SimpleNamespace(
            id=identity,
            object_kind=kind,
            sha256=hashlib.sha256(raw).hexdigest(),
            size_bytes=len(raw),
            **scope,
        )
        rows[identity] = publication
        publications[destination] = identity
        contents[object_id] = raw
        session.outcomes[identity] = SimpleNamespace(
            owner_id=fence.owner_id,
            object_id=object_id,
            object_kind=kind,
            sha256=publication.sha256,
        )
    monkeypatch.setattr(service, "require_neutral_transaction", lambda *a, **k: None)
    monkeypatch.setattr(service, "require_rd_controller", lambda *a, **k: None)
    monkeypatch.setattr(service, "require_research_spec", lambda *a: (None, config))

    def scoped(session, model, key, current, **kwargs):
        row = rows[key]
        if model is ResearchObjectPublication and any(
            getattr(row, name) != getattr(current, name) for name in scope
        ):
            raise DomainError("scope", "foreign", 409)
        return row

    monkeypatch.setattr(service, "_scope_row", scoped)
    monkeypatch.setattr(
        service,
        "_owned",
        lambda session, model, owner, key: (
            version if model is service.FactorVersion else obj
        ),
    )
    monkeypatch.setattr(
        service,
        "read_research_object",
        lambda session, fence, key, *args: contents[key],
    )

    def invoke():
        return service.persist_candidate_inputs(
            session,
            fence,
            object(),
            execution_id=execution.id,
            publications=publications,
            settings=SimpleNamespace(storage_root=tmp_path),
        )

    return SimpleNamespace(
        invoke=invoke,
        session=session,
        execution=execution,
        admission=admission,
        publications=publications,
        rows=rows,
        contents=contents,
        obj=obj,
        prefix=prefix,
        root=tmp_path,
    )


def test_actual_source_bytes_and_published_inputs_create_four_flush_only_rows(setup):
    row = setup.invoke()
    assert row.input_sha256 == setup.admission.input_sha256
    assert row.projection_sha256 != row.input_sha256
    assert len(setup.session.added) == 5 and setup.session.flushes == 2
    assert [item.destination for item in setup.session.added[1:]] == sorted(
        ["/input/source.py", *service.KINDS]
    )


def test_exact_replay_after_terminal_does_not_write(setup):
    first = setup.invoke()
    setup.session.terminal = object()
    assert setup.invoke() is first
    assert len(setup.session.added) == 5 and setup.session.flushes == 2


@pytest.mark.parametrize(
    "case",
    [
        "old_v1",
        "ticket",
        "wrong_role",
        "wrong_image",
        "wrong_logical_id",
        "prefix_as_input",
        "source_kind",
        "source_bytes",
        "foreign_publication",
        "swapped_publication",
        "missing_outcome",
        "changed_json",
        "terminal",
    ],
)
def test_foreign_or_unclosed_input_cannot_write(setup, case):
    if case == "old_v1":
        setup.admission.schema_version = "mineagent.process-admission.v1"
    if case == "ticket":
        setup.admission.ticket_sha256 = "f" * 64
    if case == "wrong_role":
        setup.admission.role = "factor_cutoff_normalizer"
    if case == "wrong_image":
        setup.admission.image_id = "sha256:" + "f" * 64
    if case == "wrong_logical_id":
        setup.admission.logical_operation_id = UUID(int=99)
    if case == "prefix_as_input":
        setup.admission.input_sha256 = setup.prefix.sha256
    if case == "source_kind":
        setup.obj.object_kind = "native_hdf"
    if case == "source_bytes":
        (setup.root / "source.py").write_bytes(b"evil")
    first = next(iter(setup.publications.values()))
    if case == "foreign_publication":
        setup.rows[first].owner_id = UUID(int=99)
    if case == "swapped_publication":
        setup.publications["/input/manifest.json"] = setup.publications[
            "/input/listings.json"
        ]
    if case == "missing_outcome":
        setup.session.outcomes.pop(first)
    if case == "changed_json":
        setup.contents[setup.session.outcomes[first].object_id] += b" "
    if case == "terminal":
        setup.session.terminal = object()
    with pytest.raises((DomainError, ValueError, RuntimeError)):
        setup.invoke()
    assert not setup.session.added and setup.session.flushes == 0


@pytest.mark.parametrize(
    "drift",
    [
        "ordinal",
        "bool_ordinal",
        "destination",
        "file_owner",
        "file_inventory",
        "inventory_owner",
        "inventory_execution",
    ],
)
def test_replay_checks_scope_and_exact_order_before_dictionary_canonicalization(
    setup, drift
):
    inventory = setup.invoke()
    row = setup.session.added[1]
    if drift == "ordinal":
        row.ordinal = 9
    if drift == "bool_ordinal":
        row.ordinal = False
    if drift == "destination":
        row.destination = "/input/source.py"
    if drift == "file_owner":
        row.owner_id = UUID(int=99)
    if drift == "file_inventory":
        row.inventory_id = UUID(int=99)
    if drift == "inventory_owner":
        inventory.owner_id = UUID(int=99)
    if drift == "inventory_execution":
        inventory.execution_id = UUID(int=99)
    with pytest.raises(DomainError):
        setup.invoke()
    assert len(setup.session.added) == 5 and setup.session.flushes == 2


def test_invalid_persisted_prefix_is_bounded_domain_drift(setup):
    setup.execution.projection = {}
    with pytest.raises(DomainError) as error:
        setup.invoke()
    assert error.value.code == "factor_candidate_input_drift"
    assert not setup.session.added


@pytest.mark.parametrize("existing", [False, True])
def test_observation_freezes_new_input_witness_but_not_exact_replay(
    setup, monkeypatch, existing
):
    from backend.app import rdagent_boundary_services as boundary

    first = setup.invoke() if existing else None

    def observed(*args):
        raise DomainError("factor_boundary_drift", "observed", 409)

    monkeypatch.setattr(boundary, "require_unobserved_generation", observed)
    if existing:
        assert setup.invoke() is first
        assert setup.session.flushes == 2
    else:
        with pytest.raises(DomainError):
            setup.invoke()
        assert not setup.session.added and setup.session.flushes == 0

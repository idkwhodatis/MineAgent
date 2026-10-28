"""Actual storage initialization; explicit SQL/lifecycle doubles, not PG proof."""

import ast
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app.models import utcnow
from backend.app.research_object_services import object_storage_guard
from packages.research_runtime.engine_broker_contracts import OEChatResponse
from tests.openevolve.test_contracts import complete_spec
from workers.openevolve import operational as worker


@pytest.mark.parametrize("fault", [None, "symlink", "file"])
def test_initial_worker_provisions_before_image_or_capacity(
    monkeypatch, tmp_path, fault
):
    spec = complete_spec()
    fence = NS(owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4())
    row = NS(owner_id=fence.owner_id, spec=spec.model_dump(mode="json"))
    session = NS(
        get=lambda cls, key: (
            row
            if key == fence.run_id
            else NS(lease_expires_at=utcnow() + timedelta(seconds=1200))
        )
    )
    active = []

    @contextmanager
    def lifecycle(*args):
        active.append(True)
        try:
            yield
        finally:
            active.pop()

    @contextmanager
    def transaction(*args, **kwargs):
        assert active
        yield session

    monkeypatch.setattr(worker, "shared_lifecycle", lifecycle)
    monkeypatch.setattr(worker, "neutral_transaction", transaction)
    original = worker.provision_directory

    def provision(*args, **kwargs):
        assert active
        original(*args, **kwargs)

    monkeypatch.setattr(worker, "provision_directory", provision)
    if fault == "symlink":
        (tmp_path / "elsewhere").mkdir()
        (tmp_path / "research-objects").symlink_to(tmp_path / "elsewhere")
    elif fault == "file":
        (tmp_path / "research-objects").write_bytes(b"must not replace")
    images = []

    def image(_):
        images.append(True)
        assert active
        assert all(
            (tmp_path / name).is_dir()
            for name in ("factors", "research-objects", "research-processes")
        )
        with object_storage_guard(tmp_path):
            pass
        raise RuntimeError("stop before any Docker or capacity request")

    monkeypatch.setattr(worker, "_image_inventory", image)
    schedule = worker.FixtureSchedule(
        tuple(
            OEChatResponse(content="fixed") for _ in range(spec.maximum_mutation_slots)
        )
    )
    with pytest.raises(OSError if fault else RuntimeError):
        worker.run_initial_epoch(
            NS(kw={"bind": object()}),
            fence,
            runtime=None,
            schedule=schedule,
            settings=NS(storage_root=tmp_path),
        )
    assert images == ([] if fault else [True])
    if fault == "file":
        assert (tmp_path / "research-objects").read_bytes() == b"must not replace"


def test_owner_loss_fixture_migrates_its_own_connection_to_exact024(monkeypatch):
    from alembic import command

    path = Path("tests/integration/test_openevolve_initial_runtime.py")
    node = next(
        n
        for n in ast.parse(path.read_text()).body
        if isinstance(n, ast.FunctionDef) and n.name == "migrated_engine"
    )
    assert [a.arg for a in node.args.args] == ["postgres_engine"]
    scope = {
        "pytest": pytest,
        "Path": Path,
        "__file__": str(path.absolute()),
        "REVISION": "024_openevolve_initial",
    }
    # Compile only the checked-in fixture to exercise its actual connection dispatch.
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), scope)  # noqa: S102
    observed = []
    monkeypatch.setattr(
        command,
        "upgrade",
        lambda config, target: observed.append(
            (config.attributes["connection"], target)
        ),
    )
    for _ in range(2):
        connection = object()

        @contextmanager
        def begin(connection=connection):
            yield connection

        engine = NS(begin=begin)
        assert scope["migrated_engine"].__wrapped__(engine) is engine
        assert observed[-1] == (connection, "024_openevolve_initial")
    assert observed[0][0] is not observed[1][0]

    # All integration process-unit admissions pass the real fixture Settings,
    # including historical revision-specific cases; no guard bypass via None.
    reservations = []
    paths = [
        path
        for root in ("backend/app", "workers/research", "workers/openevolve", "tests")
        for path in Path(root).rglob("*.py")
    ]
    for path in paths:
        tree = ast.parse(path.read_text())
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        aliases = {"reserve_budget"}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "backend.app.research_broker_services"
            ):
                aliases.update(
                    item.asname or item.name
                    for item in node.names
                    if item.name == "reserve_budget"
                )
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            if (
                isinstance(call.func, ast.Name)
                and call.func.id == "_admit_fixture_process"
            ):
                assert any(
                    k.arg == "settings"
                    and isinstance(k.value, ast.Name)
                    and k.value.id == "settings"
                    for k in call.keywords
                ), path
            if not (
                (isinstance(call.func, ast.Name) and call.func.id in aliases)
                or (
                    isinstance(call.func, ast.Attribute)
                    and call.func.attr == "reserve_budget"
                )
            ):
                continue
            purposes = [k.value for k in call.keywords if k.arg == "purpose"]
            assert len(purposes) == 1, (path, call.lineno)
            purpose = purposes[0]
            # Variable/parameterized purposes can include process_unit. Never
            # infer their safety from only literal-purpose call sites.
            if isinstance(purpose, ast.Constant) and purpose.value != "process_unit":
                continue
            reservations.append((path.name, call.lineno))
            ancestors = []
            parent = call
            while parent in parents:
                parent = parents[parent]
                ancestors.append(parent)
            transactions = [
                item.context_expr
                for parent in ancestors
                if isinstance(parent, ast.With)
                for item in parent.items
                if isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Name)
                and item.context_expr.func.id == "neutral_transaction"
            ]
            if transactions:
                for transaction in transactions:
                    scope = next(
                        (
                            k.value
                            for k in transaction.keywords
                            if k.arg == "resource_scope"
                        ),
                        None,
                    )
                    assert scope is not None, (path, call.lineno)
                    if isinstance(scope, ast.IfExp):
                        # Inspect both branches of the actual parameterized fixture.
                        assert (
                            ast.unparse(scope)
                            == "'processes' if purpose == 'process_unit' else 'objects'"
                        )
                    else:
                        assert isinstance(scope, ast.Constant) and scope.value in {
                            "processes",
                            "factor_numeric",
                        }, (path, call.lineno)
            else:
                # Only these existing session-taking producers delegate the
                # transaction to their reviewed caller; no new silent exemption.
                function = next(n for n in ancestors if isinstance(n, ast.FunctionDef))
                assert (str(path), function.name) in {
                    (
                        "backend/app/openevolve_epoch_admission.py",
                        "prepare_initial_epoch",
                    ),
                    ("workers/research/rdagent_numeric.py", "_intent"),
                }, (path, call.lineno)
            assert any(
                k.arg == "settings"
                and isinstance(k.value, ast.Name)
                and k.value.id == "settings"
                for k in call.keywords
            ), path
    # The OE evaluator now separates read-only reservation admission from the
    # one-shot write. Audit that replacement, rather than silently dropping it.
    path = Path("backend/app/openevolve_evaluation_admission.py")
    acquisition = next(
        n
        for n in ast.parse(path.read_text()).body
        if isinstance(n, ast.FunctionDef) and n.name == "_preparation_admission"
    )
    calls = [n for n in ast.walk(acquisition) if isinstance(n, ast.Call)]
    preflight = [
        n
        for n in calls
        if isinstance(n.func, ast.Name) and n.func.id == "_budget_reservation_preflight"
    ]
    transaction = [
        n
        for n in calls
        if isinstance(n.func, ast.Name) and n.func.id == "neutral_transaction"
    ]
    assert len(preflight) == len(transaction) == 1
    assert any(
        k.arg == "resource_scope"
        and isinstance(k.value, ast.Constant)
        and k.value.value == "processes"
        for k in transaction[0].keywords
    )
    assert any(
        k.arg == "settings"
        and isinstance(k.value, ast.Name)
        and k.value.id == "settings"
        for k in preflight[0].keywords
    )
    assert (
        transaction[0].lineno
        < preflight[0].lineno
        < min(n.lineno for n in ast.walk(acquisition) if isinstance(n, ast.Yield))
    )
    reservations.append((path.name, preflight[0].lineno))
    assert len(reservations) >= 16
    assert any(name == "test_model_broker_accounting.py" for name, _ in reservations)
    path = Path("tests/integration/test_research_process_recovery.py")
    node = next(
        n
        for n in ast.parse(path.read_text()).body
        if isinstance(n, ast.FunctionDef) and n.name == "_provision_process_storage"
    )
    local = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), local)  # noqa: S102
    # This helper uses the production durable/no-follow provisioner, not mkdir.
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        local["_provision_process_storage"](NS(storage_root=root))
        with object_storage_guard(root):
            assert (root / "research-processes").is_dir()

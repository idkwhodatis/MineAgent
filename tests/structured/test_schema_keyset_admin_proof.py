"""All administrative safety checks are tested without Docker or a database."""

import ast
import copy
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from script import schema_keyset_admin_proof as proof

CID = "a" * 64
NAME = "ma032g_latin1_" + "b" * 32
SECRET = "private-password-must-not-appear"


def environment(root, token="owned-ci-postgres"):
    return {
        "GITHUB_ACTIONS": "true",
        "GITHUB_WORKSPACE": str(root),
        "MINEAGENT_KEYSET_ADMIN_PROOF": token,
        "TEST_DATABASE_URL": f"postgresql+psycopg://tradegrad_ci:{SECRET}@127.0.0.1:5432/tradegrad_ci",
    }


def inspection(token="owned-ci-postgres"):
    port = {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5432"}]}
    return {
        "Id": CID,
        "Name": "/tradegrad-ci-postgres",
        "Image": "sha256:owned",
        "Created": "created",
        "State": {"Running": True, "StartedAt": "started"},
        "Config": {
            "Labels": {
                "io.tradegrad.ci": "postgres",
                "io.mineagent.ci": proof.TOKENS[token],
            },
            "Env": [
                "POSTGRES_USER=tradegrad_ci",
                "POSTGRES_DB=tradegrad_ci",
                "POSTGRES_PASSWORD=" + SECRET,
            ],
        },
        "HostConfig": {"PortBindings": copy.deepcopy(port)},
        "NetworkSettings": {"Ports": copy.deepcopy(port)},
    }


@pytest.fixture
def service_setup(tmp_path, monkeypatch):
    path = tmp_path / "var/numeric-private/postgres.cid"
    path.parent.mkdir(parents=True)
    path.write_text(CID + "\n")
    inspect = MagicMock(return_value=inspection())
    monkeypatch.setattr(proof, "inspect_container", inspect)
    return tmp_path, path, environment(tmp_path), inspect


def test_import_performs_no_io_or_engine_creation(monkeypatch):
    source = Path(proof.__file__).read_text()
    code = compile(source, proof.__file__, "exec")
    module = ModuleType("admin_proof_import_test")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    blocked = MagicMock(side_effect=AssertionError("I/O during import"))
    for name in ("resolve", "read_text", "stat", "lstat"):
        monkeypatch.setattr(Path, name, blocked)
    monkeypatch.setattr(os, "open", blocked)
    monkeypatch.setattr(subprocess, "run", blocked)
    monkeypatch.setattr("sqlalchemy.create_engine", blocked)
    exec(code, module.__dict__)  # noqa: S102 - import-only execution of this reviewed module
    blocked.assert_not_called()


@pytest.mark.parametrize("token", tuple(proof.TOKENS))
def test_owned_tokens_capture_exact_service(service_setup, monkeypatch, token):
    root, _, env, inspect = service_setup
    env["MINEAGENT_KEYSET_ADMIN_PROOF"] = token
    inspect.return_value = inspection(token)
    service = proof.capture_service(env, root)
    assert service.cid == CID
    assert service.token == token
    assert SECRET not in repr(service)
    proof.reverify_service(service, env)


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_ACTIONS", None),
        ("GITHUB_ACTIONS", "false"),
        ("GITHUB_ACTIONS", "TRUE"),
        ("MINEAGENT_KEYSET_ADMIN_PROOF", None),
        ("MINEAGENT_KEYSET_ADMIN_PROOF", "1"),
        ("MINEAGENT_KEYSET_ADMIN_PROOF", "arbitrary"),
        ("GITHUB_WORKSPACE", None),
        ("GITHUB_WORKSPACE", "/other-checkout"),
        ("TEST_DATABASE_URL", "not-a-url"),
        ("TEST_DATABASE_URL", "sqlite:///local.db"),
        ("TEST_DATABASE_URL", "postgresql://tradegrad_ci@127.0.0.1:5432/tradegrad_ci"),
        (
            "TEST_DATABASE_URL",
            "postgresql+psycopg://postgres@127.0.0.1:5432/tradegrad_ci",
        ),
        (
            "TEST_DATABASE_URL",
            "postgresql+psycopg://tradegrad_ci@localhost:5432/tradegrad_ci",
        ),
        (
            "TEST_DATABASE_URL",
            "postgresql+psycopg://tradegrad_ci@remote:5432/tradegrad_ci",
        ),
        (
            "TEST_DATABASE_URL",
            "postgresql+psycopg://tradegrad_ci@127.0.0.1:5433/tradegrad_ci",
        ),
        (
            "TEST_DATABASE_URL",
            "postgresql+psycopg://tradegrad_ci@127.0.0.1/tradegrad_ci",
        ),
        (
            "TEST_DATABASE_URL",
            "postgresql+psycopg://tradegrad_ci@127.0.0.1:5432/production",
        ),
        (
            "TEST_DATABASE_URL",
            "postgresql+psycopg://tradegrad_ci@127.0.0.1:5432/tradegrad_ci?host=remote",
        ),
        (
            "TEST_DATABASE_URL",
            "postgresql+psycopg://tradegrad_ci@127.0.0.1:5432/tradegrad_ci?options=-csearch_path=public",
        ),
    ],
)
def test_configuration_refuses_before_inspection(service_setup, key, value):
    root, _, env, inspect = service_setup
    env[key] = value
    with pytest.raises(proof.ProofGuardError) as failure:
        proof.capture_service(env, root)
    assert SECRET not in str(failure.value)
    inspect.assert_not_called()


@pytest.mark.parametrize(
    "contents",
    [
        "",
        "a" * 12,
        "g" * 64,
        "A" * 64,
        "a" * 64 + "\r\n",
        "a" * 63 + " ",
        "a" * 64 + "\n\n",
    ],
)
def test_cid_rejects_invalid_contents(service_setup, contents):
    root, path, env, inspect = service_setup
    path.write_text(contents)
    with pytest.raises(proof.ProofGuardError):
        proof.capture_service(env, root)
    inspect.assert_not_called()


@pytest.mark.parametrize(
    "kind", ["missing", "directory", "symlink", "parent_symlink", "fifo"]
)
def test_cid_requires_regular_nonsymlink_path(service_setup, kind):
    root, path, env, inspect = service_setup
    path.unlink()
    if kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        target = root / "cid"
        target.write_text(CID)
        path.symlink_to(target)
    elif kind == "parent_symlink":
        path.parent.rmdir()
        target = root / "other"
        target.mkdir()
        (target / "postgres.cid").write_text(CID)
        path.parent.symlink_to(target, target_is_directory=True)
    elif kind == "fifo":
        os.mkfifo(path)
    with pytest.raises(proof.ProofGuardError):
        proof.capture_service(env, root)
    inspect.assert_not_called()


def test_cid_open_failure_is_sanitized(service_setup, monkeypatch):
    root, _, env, inspect = service_setup
    monkeypatch.setattr(os, "open", MagicMock(side_effect=OSError(SECRET)))
    with pytest.raises(proof.ProofGuardError) as failure:
        proof.capture_service(env, root)
    assert SECRET not in str(failure.value)
    inspect.assert_not_called()


def test_cid_inode_change_is_rejected(service_setup, monkeypatch):
    root, _, env, inspect = service_setup
    real_fstat = os.fstat

    def changed(fd):
        result = real_fstat(fd)
        return SimpleNamespace(
            st_mode=result.st_mode, st_dev=result.st_dev, st_ino=result.st_ino + 1
        )

    monkeypatch.setattr(os, "fstat", changed)
    with pytest.raises(proof.ProofGuardError, match="identity changed"):
        proof.capture_service(env, root)
    inspect.assert_not_called()


@pytest.mark.parametrize(
    "path,value",
    [
        (("Id",), "b" * 64),
        (("Name",), "/production"),
        (("State", "Running"), False),
        (("State", "Running"), 1),
        (("Config", "Labels", "io.tradegrad.ci"), "other"),
        (("Config", "Labels", "io.mineagent.ci"), "v1-application-postgres"),
        (("Config", "Env"), ["POSTGRES_USER=postgres", "POSTGRES_DB=tradegrad_ci"]),
        (("Config", "Env"), ["POSTGRES_USER=tradegrad_ci", "POSTGRES_DB=production"]),
        (
            ("Config", "Env"),
            [
                "POSTGRES_USER=tradegrad_ci",
                "POSTGRES_USER=tradegrad_ci",
                "POSTGRES_DB=tradegrad_ci",
            ],
        ),
        (("Config", "Env"), None),
        (("Config", "Env"), [None]),
        (
            ("HostConfig", "PortBindings"),
            {"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5432"}]},
        ),
        (
            ("NetworkSettings", "Ports"),
            {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5433"}]},
        ),
        (
            ("NetworkSettings", "Ports"),
            {"5432/tcp": [{"HostIp": "::1", "HostPort": "5432"}]},
        ),
        (("NetworkSettings", "Ports"), {}),
        (("Image",), ""),
        (("Created",), None),
        (("State", "StartedAt"), ""),
    ],
)
def test_inspection_guard_mutations(path, value):
    record = inspection()
    current = record
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = value
    with pytest.raises(proof.ProofGuardError) as failure:
        proof.validate_inspection(record, CID, "owned-ci-postgres")
    assert SECRET not in str(failure.value)


@pytest.mark.parametrize("record", [None, [], {}, {"Id": CID}])
def test_inspection_missing_fields_fail_closed(record):
    with pytest.raises(proof.ProofGuardError):
        proof.validate_inspection(record, CID, "owned-ci-postgres")


@pytest.mark.parametrize(
    "result",
    [
        SimpleNamespace(returncode=1, stdout=SECRET),
        SimpleNamespace(returncode=0, stdout=SECRET),
        SimpleNamespace(returncode=0, stdout="[]"),
        SimpleNamespace(returncode=0, stdout="[{},{}]"),
        OSError(SECRET),
        subprocess.TimeoutExpired("docker", 10, output=SECRET),
    ],
)
def test_inspection_errors_never_expose_output(monkeypatch, result, capsys):
    mock = (
        MagicMock(side_effect=result)
        if isinstance(result, Exception)
        else MagicMock(return_value=result)
    )
    monkeypatch.setattr(subprocess, "run", mock)
    with pytest.raises(proof.ProofGuardError) as failure:
        proof.inspect_container(CID)
    assert SECRET not in str(failure.value)
    assert not capsys.readouterr().out
    assert not capsys.readouterr().err
    assert mock.call_args.args[0] == ["docker", "inspect", CID]
    assert mock.call_args.kwargs["timeout"] == 10


@pytest.mark.parametrize("change", ["cid", "inode", "runtime", "token", "url"])
def test_cleanup_reverification_requires_same_captured_service(service_setup, change):
    root, path, env, inspect = service_setup
    service = proof.capture_service(env, root)
    if change == "cid":
        path.write_text("b" * 64)
        inspect.return_value["Id"] = "b" * 64
    elif change == "inode":
        replacement = path.with_suffix(".new")
        replacement.write_text(CID)
        replacement.replace(path)
    elif change == "runtime":
        inspect.return_value["State"]["StartedAt"] = "restarted"
    elif change == "token":
        env["MINEAGENT_KEYSET_ADMIN_PROOF"] = "owned-v1-postgres"
        inspect.return_value = inspection("owned-v1-postgres")
    else:
        env["TEST_DATABASE_URL"] = env["TEST_DATABASE_URL"].replace(SECRET, "changed")
    with pytest.raises(proof.ProofGuardError, match="identity changed"):
        proof.reverify_service(service, env)


def valid_connection():
    connection = MagicMock()
    connection.execute.return_value.mappings.return_value.one.return_value = {
        "database": "tradegrad_ci",
        "current_user": "tradegrad_ci",
        "session_user": "tradegrad_ci",
        "port": 5432,
        "version": 180006,
        "superuser": True,
        "system_id": "system",
        "started": "started",
    }
    return connection


@pytest.mark.parametrize(
    "key,value",
    [
        ("database", "production"),
        ("current_user", "other"),
        ("session_user", "other"),
        ("port", 5433),
        ("version", 170006),
        ("version", 190000),
        ("superuser", False),
        ("superuser", 1),
        ("system_id", ""),
        ("started", ""),
    ],
)
def test_connected_admin_identity_guards(key, value):
    connection = valid_connection()
    connection.execute.return_value.mappings.return_value.one.return_value[key] = value
    with pytest.raises(proof.ProofGuardError):
        proof.verify_connection(connection)


def test_original_database_service_must_match():
    with pytest.raises(proof.ProofGuardError, match="service changed"):
        proof.verify_connection(valid_connection(), original=("other", "started"))


def test_engines_use_bounded_timeouts_and_hide_parameters(monkeypatch):
    factory = MagicMock()
    monkeypatch.setattr(proof, "create_engine", factory)
    proof.make_engine("private-url")
    assert factory.call_args.kwargs == {
        "poolclass": proof.NullPool,
        "connect_args": {
            "connect_timeout": 5,
            "options": "-cstatement_timeout=10000 -clock_timeout=5000",
        },
        "hide_parameters": True,
    }


def fake_admin(monkeypatch):
    connection = valid_connection()
    connection.__enter__.return_value = connection
    connection.execution_options.return_value = connection
    engine = MagicMock()
    engine.connect.return_value = connection
    admin = SimpleNamespace(
        engine=engine,
        identity=("system", "started"),
        verify=MagicMock(),
        service=SimpleNamespace(url=MagicMock()),
    )
    identity = MagicMock(return_value=(123, 456))
    monkeypatch.setattr(proof, "database_identity", identity)
    return admin, connection, identity


@pytest.mark.parametrize(
    "failure",
    ["name", "uncaptured", "container", "server", "missing", "oid", "owner", "drop"],
)
def test_database_cleanup_refuses_every_changed_identity(monkeypatch, failure):
    admin, connection, identity = fake_admin(monkeypatch)
    name, expected = NAME, (123, 456)
    if failure == "name":
        name = "existing_database"
    elif failure == "uncaptured":
        expected = None
    elif failure == "container":
        admin.verify.side_effect = proof.ProofGuardError("changed service")
    elif failure == "server":
        admin.identity = ("different", "started")
    elif failure == "missing":
        identity.return_value = None
    elif failure == "oid":
        identity.return_value = (999, 456)
    elif failure == "owner":
        identity.return_value = (123, 999)
    else:
        connection.exec_driver_sql.side_effect = RuntimeError("database busy")
    with pytest.raises((proof.ProofGuardError, RuntimeError)):
        proof.drop_owned_database(admin, name, expected)
    assert connection.exec_driver_sql.call_count == (1 if failure == "drop" else 0)


def test_database_cleanup_drops_only_exact_owned_name_after_checks(monkeypatch):
    admin, connection, identity = fake_admin(monkeypatch)
    proof.drop_owned_database(admin, NAME, (123, 456))
    admin.verify.assert_called_once()
    identity.assert_called_once_with(connection, NAME)
    connection.exec_driver_sql.assert_called_once_with(f'DROP DATABASE "{NAME}"')


@pytest.mark.parametrize(
    "failure",
    [
        "none",
        "before_create",
        "create",
        "capture",
        "connect",
        "encoding",
        "collate",
        "ctype",
        "body",
    ],
)
def test_latin1_lifecycle_only_cleans_confirmed_owned_creation(monkeypatch, failure):
    admin, connection, identity = fake_admin(monkeypatch)
    monkeypatch.setattr(proof, "uuid4", lambda: SimpleNamespace(hex="b" * 32))
    db_connection = valid_connection()
    db_connection.execute.return_value.mappings.return_value.one.return_value[
        "database"
    ] = NAME
    db_connection.__enter__.return_value = db_connection
    db_connection.scalar.side_effect = ["LATIN1", "C", "C"]
    db_engine = MagicMock()
    db_engine.connect.return_value = db_connection
    factory = MagicMock(return_value=db_engine)
    monkeypatch.setattr(proof, "make_engine", factory)
    cleanup = MagicMock()
    monkeypatch.setattr(proof, "drop_owned_database", cleanup)
    if failure == "before_create":
        admin.verify.side_effect = proof.ProofGuardError("service changed")
    elif failure == "create":
        connection.exec_driver_sql.side_effect = RuntimeError("create failed")
    elif failure == "capture":
        identity.return_value = None
    elif failure == "connect":
        db_engine.connect.side_effect = RuntimeError("connect failed")
    elif failure == "encoding":
        db_connection.scalar.side_effect = ["UTF8"]
    elif failure == "collate":
        db_connection.scalar.side_effect = ["LATIN1", "other"]
    elif failure == "ctype":
        db_connection.scalar.side_effect = ["LATIN1", "C", "other"]

    def run():
        with proof.latin1_database(admin) as engine:
            assert engine is db_engine
            if failure == "body":
                raise RuntimeError("body failed")

    if failure == "none":
        run()
        queries = [str(call.args[0]) for call in db_connection.scalar.call_args_list]
        assert queries == [
            "SHOW server_encoding",
            (
                "SELECT datcollate FROM pg_catalog.pg_database "
                "WHERE datname=pg_catalog.current_database()"
            ),
            (
                "SELECT datctype FROM pg_catalog.pg_database "
                "WHERE datname=pg_catalog.current_database()"
            ),
        ]
    else:
        with pytest.raises((proof.ProofGuardError, RuntimeError)):
            run()
    if failure in ("before_create", "create"):
        cleanup.assert_not_called()
    else:
        cleanup.assert_called_once_with(
            admin, NAME, None if failure == "capture" else (123, 456)
        )
    if failure not in ("before_create", "create", "capture"):
        db_engine.dispose.assert_called_once()
    if failure != "before_create":
        sql = connection.exec_driver_sql.call_args.args[0]
        assert "TEMPLATE template0" in sql and "LOCALE_PROVIDER libc" in sql
        assert "ENCODING 'LATIN1'" in sql and "LC_COLLATE 'C' LC_CTYPE 'C'" in sql
        assert "IF NOT EXISTS" not in sql


def test_cleanup_failure_is_visible_and_not_retried(monkeypatch):
    admin, _, _ = fake_admin(monkeypatch)
    monkeypatch.setattr(
        proof, "make_engine", MagicMock(side_effect=RuntimeError("connect failed"))
    )
    cleanup = MagicMock(side_effect=proof.ProofGuardError("cleanup identity changed"))
    monkeypatch.setattr(proof, "drop_owned_database", cleanup)
    with (
        pytest.raises(proof.ProofGuardError, match="cleanup identity changed"),
        proof.latin1_database(admin),
    ):
        pytest.fail("unreachable")
    cleanup.assert_called_once()


def test_owned_admin_disposes_after_verification_failure(monkeypatch):
    monkeypatch.setattr(
        proof, "capture_service", lambda *_: SimpleNamespace(url="private")
    )
    engine = MagicMock()
    monkeypatch.setattr(proof, "make_engine", lambda _: engine)
    monkeypatch.setattr(
        proof,
        "verify_connection",
        MagicMock(side_effect=proof.ProofGuardError("not owned")),
    )
    with pytest.raises(proof.ProofGuardError), proof.owned_admin():
        pytest.fail("unreachable")
    engine.dispose.assert_called_once()


def test_integration_collection_has_no_admin_calls_and_uses_full_latin1_chain():
    path = Path(__file__).parents[1] / "integration/test_schema_keyset_admin_proof.py"
    source = path.read_text()
    tree = ast.parse(source)
    for statement in tree.body:
        if isinstance(statement, ast.Expr):
            assert isinstance(statement.value, ast.Constant)
    from tests.integration.test_schema_keyset_admin_proof import role_schema_probe

    statement = role_schema_probe("proof_schema")
    assert set(statement.compile().params) == {"value", "rule", "defs", "depth"}
    supplied = {
        "value": '{"café":"ÿ"}',
        "rule": '{"additionalProperties":false}',
        "defs": "{}",
        "depth": 0,
    }
    assert statement.compile().construct_params(supplied) == supplied
    assert "full_chain=True" in source
    assert "command.upgrade(config, module.down_revision)" in source
    assert "WITH INHERIT {inherit}, SET FALSE" in source
    assert "pg_has_role(current_user,:owner,'USAGE')" in source
    assert "SET LOCAL ROLE" in source
    assert "DROP OWNED" not in source
    assert "transaction.rollback()" in source

"""Fail-closed access to the two owned CI PostgreSQL services, never production.

Import and collection perform no filesystem, process or database operations.
Only an explicitly opted-in fixture calls these guards. No raw URL, Docker
inspection (which contains a password), or database driver exception is logged.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.pool import NullPool

TOKENS = {
    "owned-ci-postgres": "numeric-postgres",
    "owned-v1-postgres": "v1-application-postgres",
}
TIMEOUTS = {
    "connect_timeout": 5,
    "options": "-cstatement_timeout=10000 -clock_timeout=5000",
}


class ProofGuardError(RuntimeError):
    """A deliberately credential-free refusal."""


def require(condition, message):
    if not condition:
        raise ProofGuardError(message)


def repository_root():
    return Path(__file__).resolve().parents[1]


@dataclass(frozen=True, repr=False)
class Service:
    root: Path
    url: object = field(repr=False)
    token: str
    cid: str
    inode: tuple
    runtime: tuple

    def __repr__(self):
        return "Service(<owned CI PostgreSQL>)"


def configuration(environ, root):
    require(environ.get("GITHUB_ACTIONS") == "true", "GitHub Actions required")
    token = environ.get("MINEAGENT_KEYSET_ADMIN_PROOF")
    require(token in TOKENS, "Explicit owned CI admin proof token required")
    workspace = environ.get("GITHUB_WORKSPACE")
    require(bool(workspace), "Exact GitHub workspace required")
    root = Path(root).resolve()
    require(Path(workspace).resolve() == root, "Exact GitHub workspace required")
    try:
        url = make_url(environ.get("TEST_DATABASE_URL", ""))
        valid = (
            url.drivername == "postgresql+psycopg"
            and url.username == "tradegrad_ci"
            and url.database == "tradegrad_ci"
            and url.host == "127.0.0.1"
            and url.port == 5432
            and not url.query
        )
    except (ArgumentError, TypeError, ValueError):
        raise ProofGuardError("Exact owned CI database URL required") from None
    require(valid, "Exact owned CI database URL required")
    return root, url, token


def read_cid(root):
    path = root / "var/numeric-private/postgres.cid"
    # Reject symlinked parents as well as the leaf; no FIFO/device may be opened.
    for entry in (root / "var", path.parent, path):
        require(not entry.is_symlink(), "Ordinary private CID path required")
    try:
        before = path.lstat()
        require(stat.S_ISREG(before.st_mode), "Regular CID file required")
        require(before.st_size in (64, 65), "Exact full container ID required")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            opened = os.fstat(fd)
            require(
                stat.S_ISREG(opened.st_mode)
                and (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                "CID file identity changed",
            )
            data = os.read(fd, 66)
        finally:
            os.close(fd)
    except OSError:
        raise ProofGuardError("Readable ordinary private CID file required") from None
    require(
        re.fullmatch(rb"[0-9a-f]{64}\n?", data) is not None,
        "Exact full container ID required",
    )
    return data.decode("ascii").rstrip("\n"), (opened.st_dev, opened.st_ino)


def inspect_container(cid):
    try:
        result = subprocess.run(
            ["docker", "inspect", cid],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        require(result.returncode == 0, "Owned container inspection failed")
        records = json.loads(result.stdout)
        require(
            type(records) is list and len(records) == 1,
            "Exact single owned container required",
        )
        return records[0]
    except (OSError, ValueError, subprocess.SubprocessError):
        raise ProofGuardError("Owned container inspection failed") from None


def validate_inspection(record, cid, token):
    try:
        require(type(record) is dict, "Exact owned container required")
        require(record["Id"] == cid, "Owned container ID changed")
        require(
            record["Name"] == "/tradegrad-ci-postgres",
            "Exact owned container name required",
        )
        require(record["State"]["Running"] is True, "Running owned container required")
        labels = record["Config"]["Labels"]
        require(
            labels["io.tradegrad.ci"] == "postgres", "Owned PostgreSQL label required"
        )
        require(
            labels["io.mineagent.ci"] == TOKENS[token],
            "Proof token and service class differ",
        )
        port = {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5432"}]}
        require(
            record["HostConfig"]["PortBindings"] == port,
            "Exact loopback PostgreSQL publication required",
        )
        require(
            record["NetworkSettings"]["Ports"] == port,
            "Exact live loopback PostgreSQL publication required",
        )
        env = record["Config"]["Env"]
        require(
            type(env) is list and all(type(x) is str for x in env),
            "Owned PostgreSQL environment required",
        )
        for key in ("POSTGRES_USER", "POSTGRES_DB"):
            require(
                [entry for entry in env if entry.startswith(key + "=")]
                == [key + "=tradegrad_ci"],
                "Exact owned PostgreSQL user/database required",
            )
        runtime = (record["Image"], record["Created"], record["State"]["StartedAt"])
        require(
            all(type(x) is str and x for x in runtime),
            "Owned container runtime identity required",
        )
        return runtime
    except (KeyError, TypeError):
        raise ProofGuardError("Incomplete owned container identity") from None


def capture_service(environ=None, root=None):
    environ = os.environ if environ is None else environ
    root, url, token = configuration(
        environ, repository_root() if root is None else root
    )
    cid, inode = read_cid(root)
    runtime = validate_inspection(inspect_container(cid), cid, token)
    return Service(root, url, token, cid, inode, runtime)


def reverify_service(service, environ=None):
    fresh = capture_service(environ, service.root)
    require(fresh == service, "Owned CI service identity changed; cleanup refused")


def make_engine(url):
    return create_engine(
        url, poolclass=NullPool, connect_args=dict(TIMEOUTS), hide_parameters=True
    )


def verify_connection(connection, database="tradegrad_ci", original=None):
    row = (
        connection.execute(
            text("""
        SELECT current_database() AS database, current_user AS current_user,
               session_user AS session_user, inet_server_port() AS port,
               current_setting('server_version_num')::integer AS version,
               (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) AS superuser,
               (SELECT system_identifier::text FROM pg_control_system()) AS system_id,
               pg_postmaster_start_time()::text AS started
    """)
        )
        .mappings()
        .one()
    )
    require(
        row["database"] == database
        and row["current_user"] == "tradegrad_ci"
        and row["session_user"] == "tradegrad_ci"
        and row["port"] == 5432
        and 180000 <= row["version"] < 190000
        and row["superuser"] is True,
        "Exact owned PostgreSQL 18 admin connection required",
    )
    identity = (row["system_id"], row["started"])
    require(
        all(type(x) is str and x for x in identity),
        "Original PostgreSQL service identity required",
    )
    require(
        original is None or identity == original,
        "Original PostgreSQL service changed; cleanup refused",
    )
    return identity


@dataclass(repr=False)
class Admin:
    service: Service
    engine: object
    identity: tuple
    environ: object = None

    def verify(self):
        reverify_service(self.service, self.environ)
        with self.engine.connect() as connection:
            verify_connection(connection, original=self.identity)


@contextmanager
def owned_admin(environ=None, root=None):
    service = capture_service(environ, root)
    engine = make_engine(service.url)
    try:
        with engine.connect() as connection:
            identity = verify_connection(connection)
        yield Admin(service, engine, identity, environ)
    finally:
        engine.dispose()


def database_identity(connection, name):
    return connection.execute(
        text("SELECT oid, datdba FROM pg_database WHERE datname=:name"),
        {"name": name},
    ).one_or_none()


def drop_owned_database(admin, name, expected):
    require(
        re.fullmatch(r"ma032g_latin1_[0-9a-f]{32}", name) is not None,
        "Generated LATIN1 database name required",
    )
    require(
        expected is not None,
        "Captured created database identity required; cleanup refused",
    )
    admin.verify()
    with admin.engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as connection:
        verify_connection(connection, original=admin.identity)
        require(
            database_identity(connection, name) == expected,
            "Created database identity changed; cleanup refused",
        )
        # No FORCE, backend termination, DROP OWNED or arbitrary database names.
        connection.exec_driver_sql(f'DROP DATABASE "{name}"')


@contextmanager
def latin1_database(admin):
    name = "ma032g_latin1_" + uuid4().hex
    created = False
    identity = None
    engine = None
    try:
        admin.verify()
        with admin.engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            verify_connection(connection, original=admin.identity)
            connection.exec_driver_sql(
                f'CREATE DATABASE "{name}" WITH OWNER tradegrad_ci TEMPLATE template0 '
                "ENCODING 'LATIN1' LOCALE_PROVIDER libc LC_COLLATE 'C' LC_CTYPE 'C'"
            )
            created = True
            identity = database_identity(connection, name)
            require(
                identity is not None,
                "Created database identity unavailable; cleanup refused",
            )
        engine = make_engine(admin.service.url.set(database=name))
        with engine.connect() as connection:
            verify_connection(connection, database=name, original=admin.identity)
            require(
                connection.scalar(text("SHOW server_encoding")) == "LATIN1",
                "Real LATIN1 server encoding required",
            )
            require(
                connection.scalar(text("SHOW lc_collate")) == "C",
                "LATIN1 C collation required",
            )
            require(
                connection.scalar(text("SHOW lc_ctype")) == "C",
                "LATIN1 C character classification required",
            )
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            drop_owned_database(admin, name, identity)

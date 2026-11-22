"""Best-effort read-only sampling on the disposable native Qlib CI database.

Only the worker's *already connected* libpq metadata is read in timed code.
One daemon polls independently, requires both sides of each read to be in
the same driver commit, and never sees query text. Samples are not durations;
"active" is not proof of CPU time, and a missing sample says nothing about waits.
"""

from collections import Counter
from contextlib import contextmanager
from re import fullmatch
from threading import Event, Thread

from psycopg import Connection

TARGET_CHAT = 4
INTERVAL = 0.1
MAX_QUERIES = 120
COMMIT_STAGES = frozenset({"reserve_object", "finalize_object"})
CATEGORIES = ("active", "lock", "io", "wal", "no_sample")
# Keep both SQL projection and output vocabulary closed. Some WAL waits use
# IO and some LWLock. Don't count unrecognized LWLock names as heavyweight Lock.
QUERY = """SELECT CASE
    WHEN state IS DISTINCT FROM 'active' THEN 'no_sample'
    WHEN wait_event_type = 'Lock' THEN 'lock'
    WHEN (wait_event_type = 'IO' AND left(wait_event, 3) = 'WAL')
        OR (wait_event_type = 'LWLock'
            AND wait_event IN ('WALWrite', 'WALInsert', 'WALBufMapping')) THEN 'wal'
    WHEN wait_event_type = 'IO' THEN 'io'
    WHEN wait_event_type IS NULL AND wait_event IS NULL THEN 'active'
    ELSE 'no_sample'
END FROM pg_catalog.pg_stat_activity
WHERE pid = %s AND datname = current_database() AND usename = session_user
    AND backend_type = 'client backend' AND pid <> pg_catalog.pg_backend_pid()
"""


class _ReadOnlyConnection(Connection):
    def wait(self, gen, interval=0.1, timeout=None):
        # Bound local socket waiting too: statement_timeout cannot protect a
        # frozen/disconnected server. close() closes this connection only.
        return super().wait(
            gen,
            interval=interval,
            timeout=min(0.15, timeout) if timeout is not None else 0.15,
        )


def _allowed(url):
    return (
        url.drivername == "postgresql+psycopg"
        and url.host == "127.0.0.1"
        and url.port == 5432
        and url.username == "tradegrad_ci"
        and type(url.database) is str
        and fullmatch(r"tradegrad_backup_test_[0-9a-f]{32}", url.database) is not None
        and type(url.password) is str
        and fullmatch(r"[0-9a-f]{64}", url.password) is not None
        and not url.query
    )


def _connect(url):
    from backend.app.operations_backup import PostgresDatabase

    # Use the same rejection of ambient libpq selectors as the owned fixture;
    # don't let a local service/override point the observer elsewhere.
    PostgresDatabase(url.render_as_string(hide_password=False)).environment()
    return _ReadOnlyConnection.connect(
        host=url.host,
        port=url.port,
        dbname=url.database,
        user=url.username,
        password=url.password,
        connect_timeout=2,
        passfile="/dev/null",
        sslmode="disable",
        gssencmode="disable",
        options="-cdefault_transaction_read_only=on -cstatement_timeout=100",
        autocommit=True,  # ensure each read has a fresh pg_stat_activity snapshot
        prepare_threshold=None,
    )


class WaitSampler:
    def __init__(self, url):
        self.url = url
        self.mark = None
        self.rpc_ordinal = None
        self.stop = Event()
        self.thread = Thread(target=self._observe, daemon=True)
        self.status = "unavailable"
        self.queries = self.boundary_discarded = 0
        self.counts = Counter()

    def begin(self, connection, ordinal):
        # Called only after matching the authenticated owner, worker thread,
        # worker engine, neutral transaction and exact publication role/stage.
        if type(ordinal) is not int or not 1 <= ordinal <= 64:
            return None
        raw = connection.driver_connection
        if type(raw) is not Connection:
            return None
        info = raw.info
        if (info.host, info.port, info.dbname, info.user) != (
            self.url.host,
            self.url.port,
            self.url.database,
            self.url.username,
        ):
            return None
        pid = info.backend_pid
        if type(pid) is not int or not 1 <= pid <= 2**31 - 1:
            return None
        # A new tuple object identifies *this* driver invocation. Publishing and
        # clearing it are non-blocking on the CPython worker, with no events/SQL.
        token = (pid, ordinal)
        self.mark = token
        return token

    def end(self, token):
        if token is self.mark:
            self.mark = None

    def _observe(self):
        try:
            # Lazy connect avoids any database work for absent/mismatched targets.
            while not self.stop.wait(INTERVAL) and self.mark is None:
                pass
            if self.stop.is_set():
                return
            with _connect(self.url) as connection:
                self.status = "ready"
                while not self.stop.is_set() and self.queries < MAX_QUERIES:
                    token = self.mark
                    if token is not None:
                        self.queries += 1
                        with connection.execute(QUERY, (token[0],)) as cursor:
                            result = cursor.fetchone()
                        if token is self.mark and not self.stop.is_set():
                            value = (
                                result[0]
                                if type(result) is tuple and len(result) == 1
                                else None
                            )
                            category = (
                                value
                                if type(value) is str and value in CATEGORIES
                                else "no_sample"
                            )
                            self.counts[token[1], category] += 1
                        else:
                            self.boundary_discarded += 1
                    if self.stop.wait(INTERVAL):
                        break
                if self.queries == MAX_QUERIES:
                    self.status = "limit"
        except Exception:  # noqa: BLE001 -- fail closed, never print libpq/SQL errors
            self.status = "unavailable"

    def report(self):
        # Only after teardown. Never read live mutable counts if the observer
        # thread did not stop in its bounded teardown window.
        if self.thread.is_alive():
            return {"status": "shutdown_incomplete", "rpc_ordinal": self.rpc_ordinal}
        return {
            "status": self.status,
            "rpc_ordinal": self.rpc_ordinal,
            "interval_ms": round(INTERVAL * 1000),
            "query_count": self.queries,
            "boundary_discarded": self.boundary_discarded,
            "transactions": [
                {
                    "ordinal": ordinal,
                    **{
                        category: self.counts[ordinal, category]
                        for category in CATEGORIES
                    },
                }
                for ordinal in sorted({ordinal for ordinal, _ in self.counts})
            ],
        }


@contextmanager
def sample_waits(url):
    sampler = None
    try:
        if url is not None and _allowed(url):
            candidate = WaitSampler(url)
            candidate.thread.start()
            sampler = candidate
    except Exception:  # noqa: BLE001, S110 -- keep original worker behavior
        pass
    try:
        yield sampler
    finally:
        if sampler is not None:
            try:
                sampler.stop.set()
                sampler.thread.join(timeout=2.5)
            except Exception:  # noqa: BLE001, S110 -- preserve the worker outcome
                pass

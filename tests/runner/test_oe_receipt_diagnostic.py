"""Fixed selector inventory, privacy, context isolation and transparent forwarding."""

import ast
import importlib.util
import json
import re
from contextlib import contextmanager
from contextvars import Context
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).parents[2]
PRIVATE = "private_sql_identifier_payload_error"


def module(name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / f"tests/integration/{name}.py"
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_transaction_timings_and_receipt_privacy(monkeypatch):
    d = module("oe_receipt_diagnostic")
    recorder = d.Recorder()
    ticks = iter(range(0, 100000, 1000))
    monkeypatch.setattr(d.time, "monotonic_ns", lambda: next(ticks))
    sent = object()
    calls = []

    @contextmanager
    def transaction(*args, **kwargs):
        assert args == (sent,) and kwargs == {"private": PRIVATE}
        calls.append("enter")
        yield sent
        calls.append("commit")

    def receipt(*args, **kwargs):
        assert args[4].kind == "intent"
        assert kwargs == {"private": PRIVATE}
        with recorder.transaction(transaction, "reserve_transaction")(
            sent, private=PRIVATE
        ) as session:
            assert session is sent
            assert d.PHASE.get() == "intent"
            assert Context().run(d.PHASE.get) is None
            assert recorder.file(lambda: sent, "file_publish")() is sent
        return sent

    assert (
        recorder.receipt(receipt)(
            None, None, None, None, NS(kind="intent"), private=PRIVATE
        )
        is sent
    )
    assert d.PHASE.get() is None
    assert calls == ["enter", "commit"]
    row = recorder.records[1]
    assert row["entry_us"] == 1 and row["body_us"] == 3 and row["exit_us"] == 1
    assert PRIVATE not in json.dumps(recorder.records)


@pytest.mark.parametrize("where", ["entry", "body", "exit"])
def test_failure_identity_and_phase_restoration(where):
    d = module("oe_receipt_diagnostic")
    recorder = d.Recorder()
    error = KeyboardInterrupt(PRIVATE)

    @contextmanager
    def transaction():
        if where == "entry":
            raise error
        yield object()
        if where == "exit":
            raise error

    def receipt(*args):
        with recorder.transaction(transaction, "reserve_transaction")():
            if where == "body":
                raise error

    with pytest.raises(KeyboardInterrupt) as caught:
        recorder.receipt(receipt)(None, None, None, None, NS(kind="stopped"))
    assert caught.value is error
    assert d.PHASE.get() is None
    assert PRIVATE not in json.dumps(recorder.records)
    assert recorder.records[0]["entered"] is (where != "entry")


def test_suppression_cap_unknown_and_inactive():
    d = module("oe_receipt_diagnostic")
    recorder = d.Recorder()

    @contextmanager
    def suppress():
        try:
            yield 7
        except ValueError:
            pass

    with recorder.transaction(suppress, "reserve_transaction")() as value:
        assert value == 7
        raise ValueError(PRIVATE)
    assert recorder.records == []
    for _ in range(d.LIMIT + 2):
        recorder.receipt(lambda *a: None)(None, None, None, None, NS(kind=PRIVATE))
    assert len(recorder.records) == d.LIMIT and recorder.overflow
    assert {r["phase"] for r in recorder.records} == {"unknown"}
    assert d.elapsed(None, 2) is None and d.elapsed(2, 1) is None
    assert d.elapsed(0, 10**18) == 600000000


def test_exact_patch_restoration():
    from importlib import import_module

    d = module("oe_receipt_diagnostic")
    selectors = (d.RECEIPT, *d.TRANSACTIONS, *d.FILES)
    original = [getattr(import_module(m), a) for m, a in selectors]
    emitted = []
    error = ValueError(PRIVATE)
    with (
        pytest.raises(ValueError) as caught,
        d.diagnose_receipts(lambda *a: emitted.append(a)),
    ):
        assert all(
            getattr(import_module(m), a) is not old
            for (m, a), old in zip(selectors, original)
        )
        raise error
    assert caught.value is error
    assert all(
        getattr(import_module(m), a) is old for (m, a), old in zip(selectors, original)
    )
    assert PRIVATE not in json.dumps(emitted)


def test_pg_unknown_and_deltas():
    d = module("oe_pg_diagnostic")
    name = d.FUNCTIONS[0]
    assert d._difference(None, {}) == {"status": "unavailable"}
    assert d._difference({}, {})["functions"][0]["status"] == "missing_or_untracked"
    assert d._difference({}, {name: (1, 2, 1)})["functions"][0]["delta"] is None
    assert (
        d._difference({name: (1, 2, 1)}, {name: (1, 2, 1)})["functions"][0]["status"]
        == "unchanged_unknown"
    )
    assert (
        d._difference({name: (1, 2, 1)}, {name: (2, 5, 3)})["functions"][0]["total_ms"]
        == 3
    )
    assert (
        d._difference({name: (2, 2, 1)}, {name: (1, 2, 1)})["status"]
        == "counter_reset_or_invalid"
    )


def test_fixed_inventory_exists_in_frozen_migrations_and_call_sites():
    d = module("oe_pg_diagnostic")
    definitions = set()
    for path in (ROOT / "backend/alembic/versions").glob("*.py"):
        strings = [
            n.value
            for n in ast.walk(ast.parse(path.read_text()))
            if isinstance(n, ast.Constant) and type(n.value) is str
        ]
        for text in strings:
            definitions.update(
                re.findall(
                    r"CREATE (?:OR REPLACE )?FUNCTION (?:@SCHEMA@\.)?(mineagent_\w+)\(",
                    text,
                )
            )
    assert len(d.FUNCTIONS) == len(set(d.FUNCTIONS)) == 76
    assert set(d.FUNCTIONS) <= definitions
    r = module("oe_receipt_diagnostic")
    assert r.TRANSACTIONS == (
        ("backend.app.oe_structured_provider_publication", "neutral_transaction"),
        ("backend.app.research_object_services", "neutral_transaction"),
    )
    assert r.FILES == (
        ("backend.app.research_object_services", "publish_bytes"),
        ("backend.app.research_object_services", "_read_exact"),
    )
    assert r.RECEIPT == (
        "backend.app.oe_structured_provider_broker",
        "retain_physical_receipt",
    )
    workflow = (ROOT / ".github/workflows/mineagent-structured-factor.yml").read_text()
    assert "postgres:18.6 -c track_functions=pl" in workflow
    assert (
        "python -m pytest tests/integration/test_oe_structured_normal_flow.py"
        in workflow
    )
    normal = (ROOT / "tests/integration/test_oe_structured_normal_flow.py").read_text()
    assert normal.index(
        'diagnose_receipt_functions(migrated_engine, stage="normal")'
    ) < normal.index("queue.run_worker(")


@pytest.mark.parametrize(
    "rows",
    [
        [(PRIVATE, 1, 1, 1)],
        [("mineagent_oes_consistent", True, 1, 1)],
        [("mineagent_oes_consistent", 1, float("nan"), 1)],
        [("mineagent_oes_consistent", -1, 1, 1)],
        [("mineagent_oes_consistent", 1, 1, 1)] * 2,
    ],
)
def test_pg_snapshot_validation_read_only(rows):
    d = module("oe_pg_diagnostic")
    commands = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def exec_driver_sql(self, statement):
            commands.append(statement)
            return NS(scalar_one=lambda: "pl")

        def execute(self, query, params):
            assert params == {"names": d.FUNCTIONS}
            assert "pg_catalog.pg_stat_user_functions" in str(query)
            return NS(all=lambda: rows)

    assert d._snapshot(NS(connect=Connection)) is None
    assert commands == ["SET TRANSACTION READ ONLY", "SHOW track_functions"]


def test_pg_boundary_failure_is_original_and_natural_lag_is_unknown(
    monkeypatch, capsys
):
    d = module("oe_pg_diagnostic")
    snapshots = []

    def snapshot(engine):
        snapshots.append(engine)
        return {}

    monkeypatch.setattr(d, "_snapshot", snapshot)
    error = RuntimeError(PRIVATE)
    engine = object()
    with (
        pytest.raises(RuntimeError) as caught,
        d.diagnose_receipt_functions(engine, stage="normal"),
    ):
        assert snapshots == [engine]
        raise error
    assert caught.value is error and snapshots == [engine, engine]
    output = capsys.readouterr().out
    assert PRIVATE not in output
    assert "missing_or_untracked" in output
    assert "sampled_natural_flush_may_lag" in output


def test_recording_failure_preserves_results_errors_and_context(monkeypatch):
    d = module("oe_receipt_diagnostic")
    recorder = d.Recorder()

    def broken(*args):
        raise RuntimeError(PRIVATE)

    monkeypatch.setattr(recorder, "append", broken)
    sentinel = object()
    original_error = ValueError("original")

    @contextmanager
    def transaction():
        yield sentinel

    def receipt(*args):
        with recorder.transaction(transaction, "reserve_transaction")() as value:
            assert value is sentinel
            assert recorder.file(lambda: sentinel, "file_read")() is sentinel
        return sentinel

    arguments = (None, None, None, None, NS(kind="started"))
    assert recorder.receipt(receipt)(*arguments) is sentinel
    assert d.PHASE.get() is None

    def failed(*args):
        with recorder.transaction(transaction, "reserve_transaction")():
            raise original_error

    with pytest.raises(ValueError) as caught:
        recorder.receipt(failed)(*arguments)
    assert caught.value is original_error
    assert d.PHASE.get() is None


def test_pg_allowlist_covers_frozen_upgrade_helper_closure():
    d = module("oe_pg_diagnostic")
    definitions = {}
    for path in sorted((ROOT / "backend/alembic/versions").glob("*.py")):
        for node in ast.parse(path.read_text()).body:
            if not (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and type(node.value.value) is str
                and any(
                    getattr(t, "id", "").endswith(("SQL", "_UP"))
                    and "DOWN" not in getattr(t, "id", "")
                    for t in node.targets
                )
            ):
                continue
            for match in re.finditer(
                r"CREATE (?:OR REPLACE )?FUNCTION (?:@SCHEMA@\.)?(mineagent_\w+)\([^;]*?AS \$\$(.*?)\$\$;",
                node.value.value,
                re.DOTALL,
            ):
                definitions[match[1]] = match[2]
    for name in d.FUNCTIONS:
        called = set(re.findall(r"\b(mineagent_\w+)\s*\(", definitions.get(name, "")))
        assert called <= set(d.FUNCTIONS), (name, called - set(d.FUNCTIONS))
    assert "mineagent_structured_raw_json" in d.FUNCTIONS
    assert "mineagent_structured_raw_commitment" in d.FUNCTIONS

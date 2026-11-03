"""Offline second candidate and strict helper installation preconditions."""

from hashlib import sha256
from types import SimpleNamespace as NS

import pytest

from tests.integration import rd_canonical_reuse_experiment as candidate
from tests.integration.rd_hash_reuse_experiment import _body


def test_canonical_reuse_is_exactly_reversible_and_preserves_guards():
    old, new = candidate.original_sql(), candidate.candidate_sql()
    restored = (
        new.replace(candidate.REUSED_HASH, candidate.HASH)
        .replace(candidate.SPLIT, candidate.BOUND)
        .replace(candidate.LOCALS, candidate.DECLARATION)
    )
    assert restored == old
    assert new.count("mineagent_rd_canonical_json(w,true)") == 1
    assert new.count(candidate.REUSED_HASH) == 1
    assert candidate.REUSED_HASH.startswith(
        "pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to("
    )
    assert new.count("EXCEPTION WHEN OTHERS THEN RETURN false;") == 1
    assert new.index(candidate.SPLIT) > new.index("mineagent_rd_provider_config(base)")
    assert "mineagent_execution_metadata_sha(s)" in new


@pytest.mark.parametrize("key", candidate.HELPER_PINS)
def test_changed_helper_source_fails_closed(monkeypatch, key):
    read = candidate.historical
    monkeypatch.setattr(
        candidate,
        "historical",
        lambda r, n: read(r, n) + ("\n" if (r, n) == key else ""),
    )
    with pytest.raises(ValueError, match="helper SQL changed"):
        candidate.candidate_sql()


def test_later_workflow_config_replacement_is_rejected_even_with_pin(monkeypatch):
    read = candidate.historical
    changed = read("032c", "UPGRADE_SQL") + candidate.original_sql()
    monkeypatch.setattr(
        candidate,
        "historical",
        lambda r, n: changed if (r, n) == ("032c", "UPGRADE_SQL") else read(r, n),
    )
    with pytest.raises(ValueError, match="Unexpected workflow config"):
        candidate.candidate_sql()


def test_layout_drift_rejected(monkeypatch):
    old = candidate.original_sql()
    monkeypatch.setattr(
        candidate,
        "original_sql",
        lambda: old.replace(candidate.HASH, "unknown_helper(w)"),
    )
    with pytest.raises(ValueError, match="layout changed"):
        candidate.candidate_sql()


def helper_rows():
    rows = {}
    for name, sql in candidate.helper_sql().items():
        canonical = name == "mineagent_rd_canonical_json"
        rows[name] = [
            _body(sql.replace("@SCHEMA@", '"public"')),
            "plpgsql" if canonical else "sql",
            "i",
            "u",
            True,
            False,
            False,
            False,
            "f",
            100,
            0,
            ["search_path=pg_catalog"],
            "text",
            "3802 16 23" if canonical else "3802",
            ["v", "ascii_only", "depth"] if canonical else ["v"],
            10,
            None,
            12345,
            "-",
            "v jsonb, ascii_only boolean, depth integer DEFAULT 0"
            if canonical
            else "v jsonb",
            True,
            None,
            None,
        ]
    return rows


def test_exact_helpers_are_accepted_read_only():
    rows = helper_rows()
    connection = NS(execute=lambda q, p: NS(all=lambda: [rows[p["name"]]]))
    assert candidate.verify_installed_helpers(connection) is None


@pytest.mark.parametrize(
    "name", ["mineagent_execution_metadata_sha", "mineagent_rd_canonical_json"]
)
@pytest.mark.parametrize(
    "column", [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 18, 19, 20, 21, 22]
)
def test_installed_helper_body_catalog_or_default_drift_is_rejected(name, column):
    rows = helper_rows()
    rows[name][column] = "drift"
    connection = NS(execute=lambda q, p: NS(all=lambda: [rows[p["name"]]]))
    with pytest.raises(ValueError, match="body or catalog drift"):
        candidate.verify_installed_helpers(connection)


def test_helper_body_pins_are_exact_pure_native_definitions():
    helpers = candidate.helper_sql()
    metadata = helpers["mineagent_execution_metadata_sha"]
    assert (
        "SELECT encode(sha256(convert_to(@SCHEMA@.mineagent_rd_canonical_json(v,true),'UTF8')),'hex')"
        in metadata
    )
    assert "IMMUTABLE STRICT SET search_path=pg_catalog" in metadata
    canonical = helpers["mineagent_rd_canonical_json"]
    for guard in ("server_encoding", "depth>24", "1048576", "integer tokens"):
        assert guard in canonical
    assert all(len(sha256(s.encode()).hexdigest()) == 64 for s in helpers.values())


def config_row():
    return [
        _body(candidate.original_sql()),
        "plpgsql",
        "i",
        "u",
        False,
        False,
        False,
        False,
        "f",
        100,
        0,
        ["search_path=public, pg_catalog"],
        "boolean",
        "3802",
        ["v"],
        10,
        None,
        56789,
        "-",
        "v jsonb",
        True,
        None,
        None,
    ]


@pytest.mark.parametrize(
    "column", [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 18, 19, 20, 21, 22]
)
def test_config_catalog_drift_is_rejected(column):
    row = config_row()
    row[column] = "drift"
    connection = NS(execute=lambda q, p: NS(all=lambda: [row]))
    with pytest.raises(ValueError, match="body or catalog drift"):
        candidate.verify_installed_config(connection, candidate.original_sql())


class FixtureEngine:
    """Deterministic transactional double, not evidence of PostgreSQL execution."""

    def __init__(self, fault=None):
        from sqlalchemy.engine import make_url

        from tests.diagnostics.test_qlib_hash_experiment import catalog_row
        from tests.integration import rd_hash_reuse_experiment as hashes

        self.url = make_url(
            "postgresql+psycopg://localhost:5432/tradegrad_backup_test_" + "a" * 32
        )
        self.rows = helper_rows()
        self.rows[candidate.FUNCTION] = config_row()
        self.rows[hashes.FUNCTION] = list(catalog_row())
        self.fault, self.ddl, self.commits, self.rollbacks = fault, [], 0, 0

    def begin(self):
        from contextlib import contextmanager
        from copy import deepcopy

        @contextmanager
        def transaction():
            before = deepcopy(self.rows)
            try:
                yield self
            except Exception:
                self.rows = before
                self.rollbacks += 1
                raise
            else:
                self.commits += 1

        return transaction()

    def execute(self, query, params):
        return NS(all=lambda: [self.rows[params["name"]]])

    def exec_driver_sql(self, sql, **kwargs):
        from tests.integration import rd_hash_reuse_experiment as hashes

        if sql == "SELECT current_database()":
            return NS(scalar_one=lambda: self.url.database)
        if sql == "SELECT version_num FROM public.alembic_version":
            return NS(scalars=lambda: NS(all=lambda: [hashes.REVISION]))
        for name in (hashes.FUNCTION, candidate.FUNCTION):
            if sql.startswith('CREATE OR REPLACE FUNCTION "public".' + name + "("):
                self.ddl.append(name)
                self.rows[name][0] = _body(sql)
                if name == candidate.FUNCTION:
                    if self.fault == "ddl":
                        raise RuntimeError("Injected second DDL failure")
                    if self.fault == "body":
                        self.rows[name][0] += "\n"
                    if self.fault == "owner":
                        self.rows[name][15] += 1
                return None
        raise AssertionError("Unexpected SQL in installer transaction double")


@pytest.mark.parametrize("fault", ["ddl", "body", "owner"])
def test_second_replacement_failure_rolls_back_both_installed_functions(
    monkeypatch, fault
):
    from copy import deepcopy

    from tests.integration import rd_hash_reuse_experiment as hashes

    monkeypatch.setenv(hashes.ENV, "candidate")
    engine = FixtureEngine(fault)
    before = deepcopy(engine.rows)
    with pytest.raises((RuntimeError, ValueError)):
        hashes.prepare(engine, NS(source=engine.url), policy="default-off-waits")
    assert engine.rows == before
    assert engine.ddl == [hashes.FUNCTION, candidate.FUNCTION]
    assert engine.rollbacks == 1 and engine.commits == 0


@pytest.mark.parametrize("mode", ["control", "candidate"])
def test_installer_keeps_original_control_and_atomically_combines_candidate(
    monkeypatch, mode
):
    from copy import deepcopy

    from tests.integration import rd_hash_reuse_experiment as hashes

    monkeypatch.setenv(hashes.ENV, mode)
    engine = FixtureEngine()
    before = deepcopy(engine.rows)
    receipt = hashes.prepare(engine, NS(source=engine.url), policy="default-off-waits")
    assert engine.commits == 1 and engine.rollbacks == 0
    assert engine.ddl == (
        [] if mode == "control" else [hashes.FUNCTION, candidate.FUNCTION]
    )
    assert receipt["canonical_reuse"] is (mode == "candidate")
    assert (
        receipt["canonical_config_installed_sha256"]
        == candidate.provenance(mode)["canonical_config_installed_sha256"]
    )
    if mode == "control":
        assert engine.rows == before
        assert receipt["schema_state"] == "full032c_unmodified"
    else:
        assert engine.rows[candidate.FUNCTION][0] == _body(candidate.candidate_sql())
        assert engine.rows[hashes.FUNCTION][0] == _body(hashes.candidate_sql())
        assert (
            receipt["schema_state"] == "full032c_experimental_hash_and_canonical_reuse"
        )


def test_helper_drift_prevents_both_replacements(monkeypatch):
    from tests.integration import rd_hash_reuse_experiment as hashes

    monkeypatch.setenv(hashes.ENV, "candidate")
    engine = FixtureEngine()
    engine.rows["mineagent_rd_canonical_json"][19] = (
        "v jsonb, ascii_only boolean, depth integer DEFAULT 1"
    )
    with pytest.raises(ValueError, match="helper body or catalog drift"):
        hashes.prepare(engine, NS(source=engine.url), policy="default-off-waits")
    assert engine.ddl == [] and engine.commits == 0 and engine.rollbacks == 1


@pytest.mark.parametrize(
    "field",
    [
        "canonical_config_original_sha256",
        "canonical_config_installed_sha256",
        "canonical_helpers_body_sha256",
        "canonical_helpers_source_sha256",
        "canonical_reuse",
    ],
)
def test_combined_provenance_report_rejects_drift_or_wrong_types(monkeypatch, field):
    from script.qlib_hash_experiment_report import closed_record
    from tests.integration import rd_hash_reuse_experiment as hashes

    monkeypatch.setenv(hashes.ENV, "candidate")
    engine = FixtureEngine()
    receipt = hashes.prepare(engine, NS(source=engine.url), policy="default-off-waits")
    receipt.pop("started")
    receipt["elapsed_seconds"] = 0.0
    assert closed_record("QLIB_HASH_EXPERIMENT", receipt, "candidate") == receipt
    receipt[field] = 1 if field == "canonical_reuse" else "private-unexpected-value"
    assert closed_record("QLIB_HASH_EXPERIMENT", receipt, "candidate") is None

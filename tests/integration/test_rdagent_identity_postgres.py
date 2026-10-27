"""Real PostgreSQL differential checks for standalone RD identity predicates.

Only installs five functions into the existing isolated migrated schema. No
check/trigger/table is weakened and no engine run is admitted by this fixture.
Local collection is not execution; hosted acceptance must require no skips.
"""

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from backend.app.rdagent_identity_sql import render_sql

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]


def _load_corpus():
    """Fixed repository test files only, without adding arbitrary sys.path roots."""
    name = "test_rdagent_contracts"
    prior = sys.modules.get(name)
    contract_path = ROOT / "tests/rdagent/test_rdagent_contracts.py"
    if prior is not None and Path(prior.__file__).resolve() != contract_path:
        raise RuntimeError("Unexpected contract fixture import identity")
    try:
        if prior is None:
            spec = importlib.util.spec_from_file_location(name, contract_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        spec = importlib.util.spec_from_file_location(
            "_rd_identity_postgres_corpus",
            ROOT / "tests/rdagent/test_rdagent_identity_sql.py",
        )
        corpus = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(corpus)
        return corpus
    finally:
        if prior is None:
            sys.modules.pop(name, None)


CORPUS = _load_corpus()


@pytest.fixture
def identity_db(migrated_engine):
    with migrated_engine.begin() as connection:
        schema = connection.execute(text("SELECT current_schema()")).scalar_one()
        connection.exec_driver_sql(
            render_sql(schema), execution_options={"no_parameters": True}
        )
        count = connection.execute(
            text("""
            SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname=:schema AND p.proname IN (
              'mineagent_rd_json_bytes','mineagent_rd_schema',
              'mineagent_rd_source_config','mineagent_rd_source_context',
              'mineagent_rd_source_request')
        """),
            {"schema": schema},
        ).scalar_one()
        assert count == 5
    return migrated_engine


def sql_accepts(engine, function, value, protocol=None):
    assert function in {"config", "context", "request"}
    suffix = ", :protocol" if function == "request" else ""
    query = f"SELECT mineagent_rd_source_{function}(CAST(:value AS jsonb){suffix})"
    with engine.begin() as connection:
        return connection.execute(
            text(query),
            {
                "value": json.dumps(value, ensure_ascii=False, allow_nan=False),
                "protocol": protocol,
            },
        ).scalar_one()


def test_valid_full_normalized_config(identity_db):
    value = CORPUS.canonical_config()
    assert CORPUS.normalized_config_accepts(value)
    assert sql_accepts(identity_db, "config", value) is True


@pytest.mark.parametrize(("path", "replacement"), CORPUS.CONFIG_CASES)
def test_config_negative_differential(identity_db, path, replacement):
    value = CORPUS.canonical_config()
    node = value
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = replacement
    expected = CORPUS.normalized_config_accepts(value)
    assert expected is False
    assert sql_accepts(identity_db, "config", value) is expected


@pytest.mark.parametrize(
    ("protocol", "value", "expected"), list(CORPUS.request_cases())
)
def test_request_differential(identity_db, protocol, value, expected):
    # The offline corpus independently invokes the actual strict codec.
    CORPUS.test_request_corpus_matches_real_codec(protocol, value, expected)
    assert sql_accepts(identity_db, "request", value, protocol) is expected


def context():
    return {
        "schema_version": "mineagent.rdagent-context.v1",
        "spec_sha256": "a" * 64,
        "input_sha256": "b" * 64,
        "logical_step": 0,
        "task_index": 0,
    }


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("logical_step", 0, True),
        ("logical_step", 7, True),
        ("logical_step", 8, False),
        ("logical_step", -1, False),
        ("logical_step", True, False),
        ("logical_step", 0.0, False),
        ("task_index", False, False),
        ("task_index", 1, False),
        ("task_index", "0", False),
        ("spec_sha256", "A" * 64, False),
        ("input_sha256", None, False),
        ("schema_version", "unknown", False),
    ],
)
def test_context_exact_service_shape(identity_db, field, value, expected):
    item = context()
    item[field] = value
    assert sql_accepts(identity_db, "context", item) is expected


@pytest.mark.parametrize("case", ["missing", "extra", "array", "null", "boolean"])
def test_context_root_and_exact_keys(identity_db, case):
    item = context()
    if case == "missing":
        del item["task_index"]
    elif case == "extra":
        item["operation"] = "chat"
    elif case == "array":
        item = [item]
    elif case == "null":
        item = None
    else:
        item = True
    assert sql_accepts(identity_db, "context", item) is False


@pytest.mark.parametrize("ascii_only", [True, False])
@pytest.mark.parametrize(
    "value",
    [
        {"中文": ["😀", "\x7f", "\t\n\r\b\f", '\\"/', 0, 9007199254740991, None]},
        CORPUS.canonical_config(),
        [],
    ],
)
def test_json_byte_bound_is_python_canonical_length(identity_db, ascii_only, value):
    expected = len(
        json.dumps(
            value,
            ensure_ascii=ascii_only,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    )
    with identity_db.begin() as connection:
        actual = connection.execute(
            text("""
            SELECT mineagent_rd_json_bytes(CAST(:value AS jsonb), :ascii_only)
        """),
            {"value": json.dumps(value), "ascii_only": ascii_only},
        ).scalar_one()
    assert actual == expected


@pytest.mark.parametrize(
    "case",
    [
        "missing_default",
        "extra_nested",
        "fitted_ready",
        "fitted_backdated",
        "locks_reversed",
        "price_drift",
    ],
)
def test_config_nested_semantics(identity_db, case):
    value = CORPUS.canonical_config()
    if case == "missing_default":
        del value["runtime"]["adapter_version"]
    elif case == "extra_nested":
        value["input"]["dataset"]["extra"] = "x"
    elif case.startswith("fitted_"):
        value["input"]["preprocessing"] = {
            "kind": "fitted",
            "transform_id": "fit",
            "code_sha256": "a" * 64,
            "parameters_sha256": "b" * 64,
            "fitted_artifact_sha256": "c" * 64,
            "fit_window": {"start": "2020-01-01", "end": "2020-02-01"},
            "last_input_at": "2020-02-01T00:00:00Z",
            "available_at": "2020-02-01T00:00:01Z"
            if case == "fitted_ready"
            else "2020-01-01T00:00:00Z",
        }
    elif case == "locks_reversed":
        value["runtime"]["dependency_locks"].reverse()
    else:
        value["input"]["input_fields"][0]["price_semantics"] = "total_return"
        value["input"]["dataset"]["price_semantics"] = "unadjusted"
    expected = CORPUS.normalized_config_accepts(copy.deepcopy(value))
    assert expected is (case == "fitted_ready")
    assert sql_accepts(identity_db, "config", value) is expected

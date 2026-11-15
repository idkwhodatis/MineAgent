"""Source/mock proof only; real PostgreSQL and native gates remain mandatory."""

import ast
import copy
import hashlib
import importlib.util
from pathlib import Path

import pytest

from script import generate_schema_keyset_cost_migration as generator
from script import schema_keyset_candidate as candidate

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "backend/alembic/versions/032g_schema_keyset_cost.py"


def migration():
    spec = importlib.util.spec_from_file_location("keyset032g_source", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rows(module, main_sql=None):
    result = []
    for i, (name, sql) in enumerate(sorted(module.FUNCTION_SOURCES.items())):
        schema = name != "mineagent_structured_json_value"
        result.append(
            {
                "oid": 20000 + i,
                "proname": name,
                "pronamespace": 19000,
                "proowner": 17000 + i,
                "prolang": 14000,
                "lanname": "plpgsql",
                "procost": 100.0,
                "prorows": 0.0,
                "provariadic": 0,
                "prosupport": 0,
                "prokind": "f",
                "prosecdef": False,
                "proleakproof": False,
                "proisstrict": False,
                "proretset": False,
                "provolatile": "i",
                "proparallel": "u",
                "pronargs": 4 if schema else 2,
                "pronargdefaults": 1 if schema else 0,
                "prorettype": 16,
                "proargtypes": "3802 3802 3802 23" if schema else "3802 23",
                "proallargtypes": None,
                "proargmodes": None,
                "proargnames": ["v", "rule", "defs", "depth"]
                if schema
                else ["v", "depth"],
                "defaults": "0" if schema else None,
                "protrftypes": None,
                "prosrc": module._body(
                    main_sql if name == candidate.NAME and main_sql else sql,
                    "proof_schema",
                ),
                "probin": None,
                "prosqlbody": None,
                "proconfig": ["search_path=pg_catalog"],
                "proacl": "{owner=X/owner,reader=X/owner}",
                "owner_usable": name == candidate.NAME,
            }
        )
    return result


def test_exact_frozen_generator_and_one_comparison_only():
    module = migration()
    assert PATH.read_text() == generator.document()
    assert module.UPGRADE_SQL == candidate.schema_sql()
    assert module.DOWNGRADE_SQL == candidate.restore_schema_sql()
    assert (
        module.UPGRADE_SQL.replace(candidate.NEW, candidate.OLD) == module.DOWNGRADE_SQL
    )
    assert module.UPGRADE_SQL.count(candidate.NEW) == 1
    assert module.UPGRADE_SQL.count("CREATE OR REPLACE FUNCTION") == 1
    assert set(module.FUNCTION_SOURCES) == {
        candidate.NAME,
        candidate.HELPER,
        "mineagent_structured_json_value",
    }
    assert module.revision == "032g_schema_keyset_cost"
    assert module.down_revision == "032f_shared_validation_cost"
    assert all(sql.isascii() for sql in module.FUNCTION_SOURCES.values())
    assert module.UPGRADE_SQL.isascii()
    assert (
        hashlib.sha256(
            module._body(module.UPGRADE_SQL, "proof_schema")
            .replace('"proof_schema"', "@SCHEMA@")
            .encode()
        ).hexdigest()
        == "c4bb8d7b8744adbf4904b1782abfb5ec9aa39e75778fa148c0c566ec0eb70afe"
    )


def test_complete_predecessor_lock_inventory_preserved():
    module = migration()
    tree = ast.parse(
        (ROOT / "backend/alembic/versions/032f_shared_validation_cost.py").read_text()
    )
    old = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_maintenance"
    )
    tables = next(
        n.value
        for n in ast.walk(old)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "tables" for t in n.targets)
    )
    assert module.TABLES == ast.literal_eval(tables)
    assert len(module.TABLES) == len(set(module.TABLES)) == 187
    assert "pg_try_advisory_xact_lock(1413960260,1129270340)" in PATH.read_text()
    assert " IN ACCESS EXCLUSIVE MODE NOWAIT" in PATH.read_text()


@pytest.mark.parametrize("filename", list(generator.SOURCE_PINS))
def test_generator_rejects_changed_predecessor(filename, monkeypatch):
    monkeypatch.setitem(generator.SOURCE_PINS, filename, "0" * 64)
    with pytest.raises(ValueError, match="Frozen predecessor source differs"):
        generator.document()


def test_separately_owned_unchanged_helpers_supported():
    module = migration()
    before = rows(module)
    module._validate_functions(before, "proof_schema", module.DOWNGRADE_SQL)
    assert [r["owner_usable"] for r in before] == [True, False, False]
    after = rows(module, module.UPGRADE_SQL)
    module._validate_functions(after, "proof_schema", module.UPGRADE_SQL)
    for old, new in zip(before, after, strict=True):
        changed = {key for key in old if old[key] != new[key]}
        assert changed == ({"prosrc"} if old["proname"] == candidate.NAME else set())


@pytest.mark.parametrize(
    "field,value",
    [
        ("lanname", "sql"),
        ("procost", 101.0),
        ("prorows", 1.0),
        ("provariadic", 23),
        ("prosupport", 99),
        ("prokind", "p"),
        ("prosecdef", True),
        ("proleakproof", True),
        ("proisstrict", True),
        ("proretset", True),
        ("provolatile", "s"),
        ("proparallel", "s"),
        ("pronargs", 3),
        ("pronargdefaults", 0),
        ("prorettype", 23),
        ("proargtypes", "3802 3802 3802 20"),
        ("proallargtypes", [3802]),
        ("proargmodes", ["i"]),
        ("proargnames", ["x", "rule", "defs", "depth"]),
        ("defaults", "1"),
        ("protrftypes", [3802]),
        ("prosrc", "BEGIN RETURN true; END"),
        ("probin", "extra"),
        ("prosqlbody", "unexpected"),
        ("proconfig", ["search_path=public, pg_catalog"]),
        ("owner_usable", False),
    ],
)
def test_main_preflight_rejects_definition_attribute_or_ownership_drift(field, value):
    module = migration()
    state = rows(module)
    state[0][field] = value
    with pytest.raises(RuntimeError, match="required") as caught:
        module._validate_functions(state, "proof_schema", module.DOWNGRADE_SQL)
    if field != "owner_usable":
        assert field + " (" + type(value).__name__ + ")" in str(caught.value)


@pytest.mark.parametrize("name", [candidate.HELPER, "mineagent_structured_json_value"])
@pytest.mark.parametrize(
    "field,value", [("prosrc", "different"), ("proconfig", None), ("defaults", "1")]
)
def test_unchanged_helpers_still_require_exact_definitions(name, field, value):
    module = migration()
    state = rows(module)
    next(row for row in state if row["proname"] == name)[field] = value
    with pytest.raises(RuntimeError, match="definition required"):
        module._validate_functions(state, "proof_schema", module.DOWNGRADE_SQL)


@pytest.mark.parametrize("change", ["missing", "duplicate", "unexpected"])
def test_exact_function_inventory(change):
    module = migration()
    state = rows(module)
    if change == "missing":
        state.pop()
    elif change == "duplicate":
        state.append(copy.deepcopy(state[0]))
    else:
        state[-1]["proname"] = "unexpected"
    with pytest.raises(RuntimeError, match="inventory required"):
        module._validate_functions(state, "proof_schema", module.DOWNGRADE_SQL)


class Result:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def scalars(self):
        return self

    def one(self):
        return self.value

    def all(self):
        return self.value

    def scalar_one(self):
        return self.value


class Platform:
    def __init__(self, **changes):
        self.row = {
            "version": 180006,
            "encoding": "UTF8",
            "deterministic": True,
            "types": ["bool", "int4", "jsonb"],
        } | changes
        self.shadows = []
        self.queries = []

    def exec_driver_sql(self, sql, params=None):
        self.queries.append(sql)
        return Result(self.shadows if "pg_my_temp_schema" in sql else self.row)


@pytest.mark.parametrize("encoding", ["UTF8", "LATIN1", "WIN1252", "SQL_ASCII"])
def test_no_unjustified_database_encoding_restriction(encoding):
    connection = Platform(encoding=encoding)
    migration()._platform(connection)
    # Qualified names must use the real catalog type, not the SQL alias.
    assert "::pg_catalog.int4 AS version" in connection.queries[0]
    assert "pg_catalog.integer" not in connection.queries[0]


@pytest.mark.parametrize(
    "changes",
    [
        {"version": 170999},
        {"version": 190000},
        {"deterministic": False},
        {"types": ["bool", "int4", "domain"]},
    ],
)
def test_semantic_platform_assumptions_fail_closed(changes):
    with pytest.raises(RuntimeError, match="PostgreSQL18 semantic catalog"):
        migration()._platform(Platform(**changes))


def test_temporary_declaration_type_shadow_rejected():
    connection = Platform()
    connection.shadows = ["jsonb"]
    with pytest.raises(RuntimeError, match="Temporary declaration"):
        migration()._platform(connection)


@pytest.mark.parametrize(
    "schema", ["public; SELECT 1", 'a"b', "Upper", "a" * 64, None, 1]
)
def test_schema_identifier_rejected(schema):
    with pytest.raises(RuntimeError, match="Exact installation schema"):
        migration()._quote(schema)


class ApplyConnection:
    def __init__(self, expected):
        from types import SimpleNamespace

        self.expected = expected
        self.calls = []
        self.info = SimpleNamespace(transaction_status=2)
        self.connection = SimpleNamespace(
            driver_connection=SimpleNamespace(info=self.info)
        )
        self.failure = None

    def exec_driver_sql(self, sql, parameters=None, **kwargs):
        self.calls.append((sql, parameters))
        if sql.startswith("CREATE OR REPLACE FUNCTION") and self.failure:
            self.info.transaction_status = 3
            raise self.failure
        if "SELECT pg_catalog.current_schema()" == sql:
            return Result("proof_schema")
        if sql == "SHOW search_path":
            return Result('"proof_schema",public')
        if "SELECT version_num" in sql:
            return Result([self.expected])
        return Result(None)


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
@pytest.mark.parametrize("drift", [None, "oid", "proowner", "proacl", "helper_body"])
def test_transition_preserves_metadata_and_search_path(direction, drift, monkeypatch):
    module = migration()
    expected = module.down_revision if direction == "upgrade" else module.revision
    old_sql = module.DOWNGRADE_SQL if direction == "upgrade" else module.UPGRADE_SQL
    new_sql = module.UPGRADE_SQL if direction == "upgrade" else module.DOWNGRADE_SQL
    connection = ApplyConnection(expected)
    before, after = rows(module, old_sql), rows(module, new_sql)
    if drift == "helper_body":
        after[-1]["prosrc"] = "changed helper"
    elif drift == "proacl":
        after[-1][drift] = "changed ACL"
    elif drift:
        after[-1][drift] += 1
    states = iter((before, after))
    monkeypatch.setattr(module.op, "get_bind", lambda: connection)
    monkeypatch.setattr(module, "_platform", lambda value: None)
    monkeypatch.setattr(module, "_snapshot", lambda *args: next(states))
    if drift:
        with pytest.raises(RuntimeError):
            getattr(module, direction)()
    else:
        getattr(module, direction)()
    assert connection.calls[-1] == (
        "SELECT pg_catalog.set_config('search_path',%s,true)",
        ('"proof_schema",public',),
    )
    locks = [sql for sql, _ in connection.calls if sql.startswith("LOCK TABLE")]
    assert len(locks) == 1 and locks[0].count('"proof_schema".') == 187
    assert "SET LOCAL search_path = pg_catalog, pg_temp" in [
        sql for sql, _ in connection.calls
    ]


def test_failed_transaction_does_not_mask_original_exception(monkeypatch):
    module = migration()
    connection = ApplyConnection(module.down_revision)
    error = RuntimeError("original SQLSTATE remains available")
    connection.failure = error
    monkeypatch.setattr(module.op, "get_bind", lambda: connection)
    monkeypatch.setattr(module, "_platform", lambda value: None)
    monkeypatch.setattr(module, "_snapshot", lambda *args: rows(module))
    with pytest.raises(RuntimeError) as caught:
        module.upgrade()
    assert caught.value is error
    assert connection.calls[-1][0].startswith("CREATE OR REPLACE FUNCTION")


def test_wrong_revision_rejected_before_replacement(monkeypatch):
    module = migration()
    connection = ApplyConnection("032e_rd_workflow_cost")
    monkeypatch.setattr(module.op, "get_bind", lambda: connection)
    monkeypatch.setattr(module, "_platform", lambda value: None)
    with pytest.raises(RuntimeError, match="Exact key-set migration revision"):
        module.upgrade()
    assert not any(sql.startswith("CREATE OR REPLACE") for sql, _ in connection.calls)


@pytest.mark.parametrize(
    "tag", ["failure", "error", "skipped", "missing", "duplicate", "wrong"]
)
def test_named_junit_gate_rejects_false_success(tag):
    from xml.etree import ElementTree as ET

    from script.schema_keyset_successor_proof import check_junit

    root = ET.Element("testsuites")
    suite = ET.SubElement(
        root, "testsuite", tests="1", failures="0", errors="0", skipped="0"
    )
    case = ET.SubElement(
        suite, "testcase", classname="tests.example", name="test_case", time="0.1"
    )
    if tag in {"failure", "error", "skipped"}:
        ET.SubElement(case, tag)
    elif tag == "missing":
        suite.remove(case)
    elif tag == "duplicate":
        suite.append(copy.deepcopy(case))
    else:
        case.set("name", "test_other")
    with pytest.raises(AssertionError):
        check_junit(["tests/example.py::test_case"], ET.tostring(root))


def test_named_collection_matches_exact_counts_and_names():
    from script.schema_keyset_successor_proof import check_collection

    expected = ["tests/example.py::test_case"]
    check_collection(expected, expected[0] + "\n1 test collected\n")
    for actual in (
        "",
        expected[0] + "\n" + expected[0],
        "tests/example.py::test_other",
    ):
        with pytest.raises(AssertionError):
            check_collection(expected, actual)


def test_ci_proof_scope_and_existing_native_gates_are_explicit():
    workflow = (
        ROOT / ".github/workflows/mineagent-configured-analysis.yml"
    ).read_text()
    assert "MINEAGENT_KEYSET_ADMIN_PROOF: owned-ci-postgres" in workflow
    provider_step = workflow.split(
        "      - name: Require provider protocol and actual local SDK containment regressions\n",
        1,
    )[1].split("      - name:", 1)[0]
    assert (
        "python -m pytest tests/rdagent_provider -q -p no:cacheprovider"
        in provider_step
    )
    assert "assert len(cases)==285" in provider_step
    assert workflow.count("script.schema_keyset_successor_proof --group ") == 2
    for path in (
        "tests/integration/test_schema_keyset_cost_migration.py",
        "tests/integration/test_schema_keyset_admin_proof.py",
    ):
        assert workflow.count(path) == 2
    assert "test_rdagent_configured_analysis.py" in workflow
    assert "test_rdagent_workflow_user_flow.py" in workflow
    v1 = (ROOT / ".github/workflows/v1-validation.yml").read_text()
    assert v1.count("MINEAGENT_KEYSET_ADMIN_PROOF: owned-v1-postgres") == 1
    assert v1.count("--label io.mineagent.ci=v1-application-postgres") == 1
    assert "--ignore=tests/integration/test_schema_keyset" not in v1

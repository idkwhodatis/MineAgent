"""Offline FK target audit of actual frozen DDL, not PostgreSQL execution."""

import ast
import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import PrimaryKeyConstraint, UniqueConstraint

from backend.app.models import Base
from backend.app.openevolve_persistence_ddl import EXTERNAL_UNIQUES, tables

ROOT = Path("backend/alembic/versions")
NAME = r"(?:@SCHEMA@\.|[a-z_][a-z_0-9]*\.)?([a-z_][a-z_0-9]*)"


def _parts(body):
    """Split only top-level commas, retaining quoted CHECK expressions."""
    depth = 0
    quote = False
    start = 0
    for i, char in enumerate(body):
        if char == "'":
            quote = not quote
        elif not quote:
            depth += (char == "(") - (char == ")")
            if char == "," and depth == 0:
                yield body[start:i].strip()
                start = i + 1
    yield body[start:].strip()


def _body(sql, start):
    depth = 1
    quote = False
    for i in range(start, len(sql)):
        char = sql[i]
        if char == "'":
            quote = not quote
        elif not quote:
            depth += (char == "(") - (char == ")")
            if depth == 0:
                return sql[start:i]
    raise AssertionError("Unclosed table DDL")


def _columns(text):
    columns = tuple(value.strip() for value in text.split(","))
    assert columns and all(re.fullmatch(r"[a-z_][a-z_0-9]*", c) for c in columns), text
    assert len(columns) == len(set(columns)), text
    return columns


def _literals(path):
    """Reviewed static SQL inputs, never execution of migration Python.

    Follow references from upgrade so unused predecessor/downgrade constants do
    not contribute keys. This is a target-eligibility audit, not an interpreter
    of procedural SQL, maintenance guards, triggers or migration execution.
    """
    if path.suffix == ".sql":
        yield path.read_text()
        return
    tree = ast.parse(path.read_text())
    assignments = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    upgrade = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
    )
    resolving = set()

    def strings(node):
        if isinstance(node, ast.Name) and node.id in assignments:
            assert node.id not in resolving, f"Cyclic SQL constant: {node.id}"
            resolving.add(node.id)
            try:
                yield from strings(assignments[node.id])
            finally:
                resolving.remove(node.id)
            return
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if re.search(
                r"CREATE TABLE|ALTER TABLE|DROP TABLE|DROP INDEX|CREATE UNIQUE INDEX",
                node.value,
                re.IGNORECASE,
            ):
                yield node.value
            return
        if isinstance(node, ast.JoinedStr):
            text = ""
            for value in node.values:
                if isinstance(value, ast.Constant):
                    text += value.value
                elif (
                    isinstance(value, ast.FormattedValue)
                    and isinstance(value.value, ast.Name)
                    and value.value.id == "schema"
                ):
                    text += "@SCHEMA@"
                else:
                    # Dynamic trigger names do not declare target keys. Any
                    # dynamic table/key DDL requires explicit parser support.
                    assert not re.search(
                        r"CREATE TABLE|ALTER TABLE|DROP TABLE|DROP INDEX|CREATE UNIQUE INDEX",
                        ast.unparse(node),
                        re.IGNORECASE,
                    ), ast.unparse(node)
                    return
            yield text
            return
        for child in ast.iter_child_nodes(node):
            yield from strings(child)

    yield from strings(upgrade)


def _statements(sql):
    # Ignore semicolons/DDL text inside SQL strings and dollar-quoted routines.
    token = re.compile(
        r"--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'|\$[a-zA-Z_0-9]*\$|;", re.DOTALL
    )
    start = position = 0
    while match := token.search(sql, position):
        value = match[0]
        if value.startswith("$"):
            end = sql.find(value, match.end())
            assert end >= 0, "Unclosed dollar quote"
            position = end + len(value)
        else:
            position = match.end()
            if value == ";":
                yield sql[start : match.start()].strip()
                start = position
    if sql[start:].strip():
        yield sql[start:].strip()


def _catalog(sql, keys, references):
    def constraint(table, text):
        name_match = re.match(r"CONSTRAINT\s+(\w+)\s+", text, re.IGNORECASE)
        name = name_match[1] if name_match else None
        unique = re.search(
            r"(?:PRIMARY KEY|UNIQUE)\s*\(([^()]+)\)", text, re.IGNORECASE
        )
        if unique and not re.search(r"(?<!NOT )\bDEFERRABLE\b", text, re.IGNORECASE):
            keys.setdefault(table, {})[name or unique[0]] = _columns(unique[1])
        inline = re.match(r"(\w+)\s+.*?\bPRIMARY KEY\b", text, re.IGNORECASE)
        if inline and not unique:
            keys.setdefault(table, {})[name or inline[1]] = (inline[1],)
        fk = re.search(
            r"FOREIGN KEY\s*\(([^()]+)\)\s+REFERENCES\s+" + NAME + r"\s*\(([^()]+)\)",
            text,
            re.IGNORECASE,
        )
        if fk:
            reference = (table, _columns(fk[1]), fk[2], _columns(fk[3]))
            references[(table, name or reference)] = reference
        elif re.search(r"\bREFERENCES\b", text, re.IGNORECASE):
            inline_fk = re.match(
                r"(\w+)\s+.*?REFERENCES\s+" + NAME + r"(?:\s*\(([^()]+)\))?",
                text,
                re.IGNORECASE,
            )
            assert inline_fk, text
            reference = (
                table,
                (inline_fk[1],),
                inline_fk[2],
                _columns(inline_fk[3]) if inline_fk[3] else (),
            )
            references[(table, name or reference)] = reference
        inline_unique = re.match(r"(\w+)\s+.*?\bUNIQUE\b(?!\s*\()", text, re.IGNORECASE)
        if (
            inline_unique
            and not unique
            and not re.search(r"(?<!NOT )\bDEFERRABLE\b", text, re.IGNORECASE)
        ):
            keys.setdefault(table, {})[name or inline_unique[1]] = (inline_unique[1],)

    # Only literal top-level DDL contributes keys. Never parse quoted function
    # bodies as if they had executed. Unsupported key-affecting DDL fails closed.
    operations = []
    for position, statement in enumerate(_statements(sql)):
        statement = re.sub(
            r"--[^\n]*|/\*.*?\*/", "", statement, flags=re.DOTALL
        ).strip()
        if not re.match(
            r"(?:CREATE TABLE|ALTER TABLE|DROP TABLE|DROP INDEX|CREATE UNIQUE INDEX)\b",
            statement,
            re.IGNORECASE,
        ):
            continue
        if re.match(r"CREATE UNIQUE INDEX", statement, re.IGNORECASE):
            index = re.fullmatch(
                r"CREATE UNIQUE INDEX\s+(\w+)\s+ON\s+"
                + NAME
                + r"\s*\(([\w\s,]+)\)(?:\s+WHERE\s+.+)?",
                statement,
                re.IGNORECASE | re.DOTALL,
            )
            assert index, statement
            if not re.search(r"\bWHERE\b", statement, re.IGNORECASE):
                operations.append(
                    (position, "index", index[2], (index[1], _columns(index[3])))
                )
            continue
        assert '"' not in statement.split("(", 1)[0], statement
        create = re.match(
            r"CREATE TABLE(?: IF NOT EXISTS)?\s+" + NAME + r"\s*\(",
            statement,
            re.IGNORECASE,
        )
        alter = re.fullmatch(
            r"ALTER TABLE\s+" + NAME + r"\s+(.+)", statement, re.IGNORECASE | re.DOTALL
        )
        drop = re.fullmatch(
            r"DROP TABLE(?: IF EXISTS)?\s+" + NAME, statement, re.IGNORECASE
        )
        drop_index = re.fullmatch(
            r"DROP INDEX(?: IF EXISTS)?\s+(\w+)", statement, re.IGNORECASE
        )
        assert create or alter or drop or drop_index, statement
        if create:
            operations.append(
                (position, "create", create[1], _body(statement, create.end()))
            )
        elif alter:
            operations.append((position, "alter", alter[1], alter[2]))
        elif drop:
            operations.append((position, "drop", drop[1], ""))
        else:
            operations.append((position, "drop_index", drop_index[1], ""))
    for _, kind, table, body in operations:
        if kind == "index":
            name, columns = body
            keys.setdefault(table, {})["index:" + name] = columns
            continue
        if kind == "drop_index":
            for table_keys in keys.values():
                table_keys.pop("index:" + table, None)
            continue
        if kind == "drop":
            keys.pop(table, None)
            for key in list(references):
                if references[key][0] == table:
                    del references[key]
            continue
        if kind == "create":
            keys.setdefault(table, {})
            for part in _parts(body):
                constraint(table, part)
        else:
            # One ALTER can replace multiple keys; apply every action in order.
            for part in _parts(body):
                drop = re.fullmatch(
                    r"DROP CONSTRAINT(?: IF EXISTS)?\s+(\w+)", part, re.IGNORECASE
                )
                if drop:
                    keys.get(table, {}).pop(drop[1], None)
                    references.pop((table, drop[1]), None)
                else:
                    if re.match(r"ADD CONSTRAINT\s+", part, re.IGNORECASE):
                        constraint(
                            table, re.sub(r"^ADD\s+", "", part, flags=re.IGNORECASE)
                        )
                    else:
                        assert not re.search(
                            r"UNIQUE|PRIMARY KEY|REFERENCES|DROP COLUMN|RENAME",
                            part,
                            re.IGNORECASE,
                        ), part


def test_every_024_through_029_frozen_fk_has_exact_nondeferrable_target_key():
    keys, refs = {}, {}
    for number in range(1, 30):
        paths = sorted(ROOT.glob(f"{number:03}_*.sql")) or sorted(
            ROOT.glob(f"{number:03}_*.py")
        )
        assert paths, number
        for path in paths:
            for sql in _literals(path):
                _catalog(sql, keys, refs)
        if number < 24:
            refs.clear()
            continue
        assert refs, number
        for table, local, target, remote in refs.values():
            assert len(local) == len(remote)
            assert any(
                set(remote) == set(key) for key in keys.get(target, {}).values()
            ), (
                number,
                table,
                local,
                target,
                remote,
            )


def _historical_metadata_audit():
    keys, refs = {}, {}
    for number in range(1, 25):
        paths = sorted(ROOT.glob(f"{number:03}_*.sql")) or sorted(
            ROOT.glob(f"{number:03}_*.py")
        )
        for path in paths:
            for sql in _literals(path):
                _catalog(sql, keys, refs)
    for table in tables().values():
        for fk in table.foreign_key_constraints:
            target = fk.elements[0].column.table
            remote = tuple(e.column.name for e in fk.elements)
            metadata_keys = [
                tuple(c.name for c in k.columns)
                for k in target.constraints
                if isinstance(k, (PrimaryKeyConstraint, UniqueConstraint))
                and not k.deferrable
            ]
            if target.name in EXTERNAL_UNIQUES:
                metadata_keys.append(EXTERNAL_UNIQUES[target.name])
            assert any(set(remote) == set(key) for key in metadata_keys), (
                table.name,
                target.name,
                remote,
            )
            assert any(
                set(remote) == set(key) for key in keys.get(target.name, {}).values()
            ), (target.name, remote)
    # Every024 declared FK must actually be emitted, not merely its target.
    for table in tables().values():
        for fk in table.foreign_key_constraints:
            expected = (
                table.name,
                tuple(e.parent.name for e in fk.elements),
                fk.elements[0].column.table.name,
                tuple(e.column.name for e in fk.elements),
            )
            assert expected in refs.values(), expected
    # Reconcile historical migration-only external keys against frozen bytes.
    for number in (25, 26, 27, 28, 29):
        for path in ROOT.glob(f"{number:03}_*.py"):
            for sql in _literals(path):
                _catalog(sql, keys, refs)
    # Frozen CREATE TABLE ownership, not every model imported by models.py
    # (which now also registers 032 normal-note models).
    _assert_metadata_targets(keys, table_names=set(keys))
    assert (
        Base.metadata.tables["strategy_search_epochs"]
        is tables()["strategy_search_epochs"]
    )


def test_audit_rejects_deferrable_or_subset_keys_and_tracks_removed_keys():
    keys, refs = {}, {}
    _catalog(
        "CREATE TABLE a (id UUID PRIMARY KEY, tenant UUID, admission UUID, UNIQUE(tenant,id,admission) DEFERRABLE INITIALLY DEFERRED);",
        keys,
        refs,
    )
    assert ("tenant", "id", "admission") not in keys["a"].values()
    _catalog(
        "ALTER TABLE a ADD CONSTRAINT exact_key UNIQUE(tenant,id,admission);",
        keys,
        refs,
    )
    assert ("tenant", "id", "admission") in keys["a"].values()
    assert ("tenant", "id") not in keys["a"].values()
    _catalog("ALTER TABLE a DROP CONSTRAINT exact_key;", keys, refs)
    assert ("tenant", "id", "admission") not in keys["a"].values()


def _assert_metadata_targets(keys, *, table_names=None):
    for table in Base.metadata.tables.values():
        if table_names is not None and table.name not in table_names:
            continue
        for fk in table.foreign_key_constraints:
            target = fk.elements[0].column.table.name
            remote = tuple(e.column.name for e in fk.elements)
            assert any(
                set(remote) == set(key) for key in keys.get(target, {}).values()
            ), (table.name, target, remote)


def _isolated_audit(function):
    # A fresh interpreter imports only this module's explicit model/DDL imports.
    # Pytest collection in the parent must not widen the historical ORM scope.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"from tests.openevolve.test_foreign_key_targets import {function}; {function}()",
        ],
        cwd=ROOT.parents[2],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_024_metadata_fk_target_is_emitted_not_merely_an_orm_index():
    _isolated_audit("_historical_metadata_audit")


def _current_catalog():
    # Follow Alembic's graph rather than numeric filename globs: 027a/b/c and
    # 032a/b/c are real revisions too. SQL sidecars are the early frozen inputs.
    script = ScriptDirectory(str(ROOT.parent))
    revisions = list(reversed(list(script.walk_revisions())))
    assert len(script.get_heads()) == 1
    keys, refs = {}, {}
    visited = set()
    for revision in revisions:
        for predecessor in (revision.down_revision, revision.dependencies):
            predecessors = (
                predecessor if isinstance(predecessor, tuple) else (predecessor,)
            )
            assert all(p is None or p in visited for p in predecessors), (
                revision.revision
            )
        visited.add(revision.revision)
        path = Path(revision.path)
        sidecar = path.with_suffix(".sql")
        for sql in _literals(sidecar if sidecar.exists() else path):
            _catalog(sql, keys, refs)
    return keys, revisions


def _current_metadata_audit():
    # Register the complete current model set, regardless of collection order.
    for path in sorted(Path("backend/app").glob("*_models.py")):
        importlib.import_module("backend.app." + path.stem)
    keys, _ = _current_catalog()
    _assert_metadata_targets(keys)


def test_current_metadata_targets_follow_actual_alembic_chain():
    _isolated_audit("_current_metadata_audit")
    _, revisions = _current_catalog()
    names = {r.revision for r in revisions}
    assert {
        "027a_contract_repairs",
        "027b_provider_material",
        "027c_seeded_controller_v4",
        "032a_rd_terminal_results",
        "032b_oe_append_run_state",
        "032c_oe_provider_table_checks",
        "032d_rd_validation_cost",
    } <= names


def test_later_model_imports_do_not_change_historical_scope_or_hide_bad_keys():
    from backend.app import declared_upload_models  # noqa: F401

    _isolated_audit("_historical_metadata_audit")
    keys, _ = _current_catalog()
    # The genuine 030 FK must remain checked, not silently excluded to cure the
    # historical collection leak. Removing its target must fail the current audit.
    keys.pop("declared_upload_baskets")
    with pytest.raises(AssertionError, match="declared_upload_baskets"):
        _assert_metadata_targets(keys)


def test_combined_alter_actions_replace_keys_in_source_order():
    keys, refs = {}, {}
    _catalog(
        "CREATE TABLE a (id UUID, tenant UUID, CONSTRAINT old_key UNIQUE(id));",
        keys,
        refs,
    )
    _catalog(
        "ALTER TABLE a DROP CONSTRAINT old_key, ADD CONSTRAINT exact_key UNIQUE(tenant,id), ADD CONSTRAINT temporary_key UNIQUE(id), DROP CONSTRAINT temporary_key;",
        keys,
        refs,
    )
    assert keys["a"] == {"exact_key": ("tenant", "id")}
    _catalog(
        "ALTER TABLE a DROP CONSTRAINT IF EXISTS exact_key, ADD CONSTRAINT exact_key UNIQUE(tenant,id) DEFERRABLE INITIALLY DEFERRED;",
        keys,
        refs,
    )
    assert keys["a"] == {}


@pytest.mark.parametrize("key", [(), ("owner_id",), ("owner_id", "id", "extra")])
def test_current_audit_rejects_missing_subset_superset_and_orm_only_keys(key):
    from backend.app import declared_upload_models  # noqa: F401

    keys, _ = _current_catalog()
    keys["declared_upload_baskets"] = {"mutated": key}
    with pytest.raises(
        AssertionError,
        match="declared_upload_basket_members.*declared_upload_baskets.*owner_id.*id",
    ):
        _assert_metadata_targets(keys, table_names={"declared_upload_basket_members"})


def test_current_audit_accepts_permutation_but_not_deferrable_unique():
    from backend.app import declared_upload_models  # noqa: F401

    keys, _ = _current_catalog()
    keys["declared_upload_baskets"] = {"permuted": ("id", "owner_id")}
    _assert_metadata_targets(keys, table_names={"declared_upload_basket_members"})
    keys["declared_upload_baskets"] = {}
    _catalog(
        "ALTER TABLE declared_upload_baskets ADD CONSTRAINT only_key UNIQUE(owner_id,id) DEFERRABLE;",
        keys,
        {},
    )
    with pytest.raises(AssertionError, match="declared_upload_baskets"):
        _assert_metadata_targets(keys, table_names={"declared_upload_basket_members"})


def test_dropped_foreign_keys_and_tables_leave_no_stale_references():
    keys, refs = {}, {}
    _catalog(
        "CREATE TABLE a (id UUID PRIMARY KEY); CREATE TABLE b (id UUID, CONSTRAINT fk FOREIGN KEY(id) REFERENCES a(id));",
        keys,
        refs,
    )
    assert list(refs.values()) == [("b", ("id",), "a", ("id",))]
    _catalog(
        "ALTER TABLE b DROP CONSTRAINT fk, ADD CONSTRAINT fk FOREIGN KEY(id) REFERENCES a(id);",
        keys,
        refs,
    )
    assert len(refs) == 1
    _catalog("ALTER TABLE b DROP CONSTRAINT fk;", keys, refs)
    assert not refs
    _catalog("DROP TABLE b;", keys, refs)
    assert "b" not in keys


def test_extraction_ignores_unused_constants_and_quoted_routine_ddl(tmp_path):
    path = tmp_path / "migration.py"
    path.write_text("""UNUSED = "CREATE TABLE a (id UUID UNIQUE);"
USED = "CREATE TABLE b (id UUID PRIMARY KEY);"
def upgrade():
    op.execute(USED)
    op.execute("CREATE FUNCTION f() RETURNS void AS $$ BEGIN EXECUTE 'CREATE TABLE a (id UUID UNIQUE)'; END $$ LANGUAGE plpgsql;")
""")
    keys, refs = {}, {}
    for sql in _literals(path):
        _catalog(sql, keys, refs)
    assert set(keys) == {"b"}


def test_historical_024_requires_its_own_keys_before_successor_repair(monkeypatch):
    original = _literals

    def missing_024_key(path):
        for sql in original(path):
            if path.name.startswith("024_"):
                sql = re.sub(
                    r"UNIQUE\s*\([^()]+\)", "CHECK (true)", sql, flags=re.IGNORECASE
                )
            yield sql

    monkeypatch.setattr(sys.modules[__name__], "_literals", missing_024_key)
    with pytest.raises(AssertionError):
        _historical_metadata_audit()


@pytest.mark.parametrize(
    "sql",
    [
        'CREATE TABLE "quoted" (id UUID PRIMARY KEY);',
        "CREATE UNIQUE INDEX expression_key ON a ((lower(id)));",
        "ALTER TABLE a DROP COLUMN id;",
        "ALTER TABLE a RENAME COLUMN id TO other;",
    ],
)
def test_unsupported_key_ddl_requires_explicit_audit_support(sql):
    with pytest.raises(AssertionError):
        _catalog(sql, {}, {})


def test_removing_emitted_basket_unique_fails_even_when_orm_retains_it(monkeypatch):
    from backend.app import declared_upload_models  # noqa: F401

    original = _literals
    changed = []

    def missing_basket_unique(path):
        for sql in original(path):
            if path.name.startswith("030_"):
                match = re.search(
                    r"CREATE TABLE (?:@SCHEMA@\.)?declared_upload_baskets\s*\(", sql
                )
                if match:
                    body = _body(sql, match.end())
                    replacement, count = re.subn(
                        r"UNIQUE\(owner_id,id\)", "CHECK(true)", body
                    )
                    assert count == 1
                    changed.append(path.name)
                    sql = (
                        sql[: match.end()]
                        + replacement
                        + sql[match.end() + len(body) :]
                    )
            yield sql

    monkeypatch.setattr(sys.modules[__name__], "_literals", missing_basket_unique)
    keys, _ = _current_catalog()
    assert changed
    with pytest.raises(
        AssertionError, match="declared_upload_basket_members.*declared_upload_baskets"
    ):
        _assert_metadata_targets(keys, table_names={"declared_upload_basket_members"})


def test_unique_index_source_order_and_partial_index_not_eligible():
    keys, refs = {}, {}
    _catalog(
        "CREATE TABLE a (id UUID); CREATE UNIQUE INDEX exact ON a(id); DROP INDEX exact; CREATE UNIQUE INDEX partial ON a(id) WHERE id IS NOT NULL;",
        keys,
        refs,
    )
    assert keys["a"] == {}
    _catalog("CREATE UNIQUE INDEX exact ON a(id); DROP TABLE a;", keys, refs)
    assert "a" not in keys


def test_standalone_drop_index_in_successor_is_extracted(tmp_path):
    first = tmp_path / "first.py"
    first.write_text(
        'def upgrade():\n    op.execute("CREATE TABLE a (id UUID); CREATE UNIQUE INDEX exact ON a(id);")\n'
    )
    successor = tmp_path / "successor.py"
    successor.write_text('def upgrade():\n    op.execute("DROP INDEX exact;")\n')
    keys, refs = {}, {}
    for sql in _literals(first):
        _catalog(sql, keys, refs)
    assert keys["a"] == {"index:exact": ("id",)}
    assert list(_literals(successor)) == ["DROP INDEX exact;"]
    for sql in _literals(successor):
        _catalog(sql, keys, refs)
    assert keys["a"] == {}


@pytest.mark.parametrize(
    "operations,expected",
    [
        (("DROP_SQL", "CREATE_SQL", "DROP_SQL"), {}),
        (("CREATE_SQL", "DROP_SQL", "CREATE_SQL"), {"index:exact": ("id",)}),
    ],
)
def test_repeated_sql_constant_references_preserve_order(
    tmp_path, operations, expected
):
    statements = {
        "DROP_SQL": "DROP INDEX IF EXISTS exact;",
        "CREATE_SQL": "CREATE UNIQUE INDEX exact ON a(id);",
    }
    path = tmp_path / "migration.py"
    path.write_text(
        "\n".join(f"{name} = {sql!r}" for name, sql in statements.items())
        + "\ndef upgrade():\n"
        + "\n".join(f"    op.execute({name})" for name in operations)
    )
    extracted = list(_literals(path))
    assert extracted == [statements[name] for name in operations]
    keys, refs = {"a": {}}, {}
    for sql in extracted:
        _catalog(sql, keys, refs)
    assert keys["a"] == expected


def test_cyclic_sql_constants_fail_closed(tmp_path):
    path = tmp_path / "cycle.py"
    path.write_text("A = B\nB = A\ndef upgrade():\n    op.execute(A)\n")
    with pytest.raises(AssertionError, match="Cyclic SQL constant: A"):
        list(_literals(path))


def test_dynamic_drop_index_requires_explicit_extractor_support(tmp_path):
    path = tmp_path / "dynamic.py"
    path.write_text('def upgrade():\n    op.execute(f"DROP INDEX {name};")\n')
    with pytest.raises(AssertionError, match="DROP INDEX"):
        list(_literals(path))

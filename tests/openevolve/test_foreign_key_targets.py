"""Offline FK target audit of actual frozen DDL, not PostgreSQL execution."""

import ast
import re
from pathlib import Path

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
    return tuple(value.strip().strip('"') for value in text.split(","))


def _literals(path):
    if path.suffix == ".sql":
        yield path.read_text()
        return
    tree = ast.parse(path.read_text())
    for function in tree.body:
        if isinstance(function, ast.FunctionDef) and function.name == "upgrade":
            for node in ast.walk(function):
                if isinstance(node, ast.JoinedStr):
                    pieces = []
                    for value in node.values:
                        if isinstance(value, ast.Constant):
                            pieces.append(value.value)
                        elif (
                            isinstance(value, ast.FormattedValue)
                            and isinstance(value.value, ast.Name)
                            and value.value.id == "schema"
                        ):
                            pieces.append("@SCHEMA@")
                        else:
                            break
                    else:
                        yield "".join(pieces)
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and re.search(r"CREATE TABLE|ALTER TABLE", node.value)
                ):
                    yield node.value
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [getattr(t, "id", "") for t in node.targets]
        if any("DOWN" in name for name in names):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            continue
        for item in value if isinstance(value, tuple) else (value,):
            if isinstance(item, str) and re.search(r"CREATE TABLE|ALTER TABLE", item):
                yield item


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
            references.append((table, _columns(fk[1]), fk[2], _columns(fk[3])))

    # Source order is significant for explicit dropped/replaced constraints.
    operations = []
    for match in re.finditer(
        r"CREATE TABLE(?: IF NOT EXISTS)?\s+" + NAME + r"\s*\(", sql, re.IGNORECASE
    ):
        operations.append((match.start(), "create", match[1], _body(sql, match.end())))
    for match in re.finditer(
        r"ALTER TABLE\s+"
        + NAME
        + r"\s+(ADD CONSTRAINT\s+[^;]+|DROP CONSTRAINT(?: IF EXISTS)?\s+\w+)",
        sql,
        re.IGNORECASE,
    ):
        operations.append((match.start(), "alter", match[1], match[2]))
    for _, kind, table, body in sorted(operations):
        if kind == "create":
            for part in _parts(body):
                constraint(table, part)
        elif body.upper().startswith("DROP"):
            keys.get(table, {}).pop(body.split()[-1], None)
        else:
            for part in _parts(body):
                constraint(table, part.removeprefix("ADD "))


def test_every_024_through_029_frozen_fk_has_exact_nondeferrable_target_key():
    keys, refs = {}, []
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
        for table, local, target, remote in refs:
            assert len(local) == len(remote)
            assert remote in keys.get(target, {}).values(), (
                number,
                table,
                local,
                target,
                remote,
            )


def test_every_024_metadata_fk_target_is_emitted_not_merely_an_orm_index():
    keys, refs = {}, []
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
            assert remote in metadata_keys, (table.name, target.name, remote)
            assert remote in keys.get(target.name, {}).values(), (target.name, remote)
    # Every024 declared FK must actually be emitted, not merely its target.
    for table in tables().values():
        for fk in table.foreign_key_constraints:
            expected = (
                table.name,
                tuple(e.parent.name for e in fk.elements),
                fk.elements[0].column.table.name,
                tuple(e.column.name for e in fk.elements),
            )
            assert expected in refs, expected
    # Reconcile historical migration-only external keys against frozen bytes.
    for number in (25, 26, 27, 28, 29):
        for path in ROOT.glob(f"{number:03}_*.py"):
            for sql in _literals(path):
                _catalog(sql, keys, refs)
    for table in Base.metadata.tables.values():
        for fk in table.foreign_key_constraints:
            target = fk.elements[0].column.table.name
            remote = tuple(e.column.name for e in fk.elements)
            assert any(
                set(remote) == set(key) for key in keys.get(target, {}).values()
            ), (table.name, target, remote)
    assert (
        Base.metadata.tables["strategy_search_epochs"]
        is tables()["strategy_search_epochs"]
    )


def test_audit_rejects_deferrable_or_subset_keys_and_tracks_removed_keys():
    keys, refs = {}, []
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

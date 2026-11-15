"""Exact maintenance head inherits032, without accepting unknown installations."""

from types import SimpleNamespace

import pytest

from backend.app.normal_oe_note_capability import require_normal_oe_notes
from backend.app.schema_capabilities import capabilities_for
from backend.app.services import DomainError

PREVIOUS = "032_normal_oe_note_pins"
LEGACY = "032a_rd_terminal_results"
OLDER = "032b_oe_append_run_state"
PENULTIMATE = "032c_oe_provider_table_checks"
EARLIER = "032d_rd_validation_cost"
OLD = "032e_rd_workflow_cost"
SHARED = "032f_shared_validation_cost"
CURRENT = "032g_schema_keyset_cost"


def test_maintenance_preserves_every_existing_capability():
    assert (
        capabilities_for(CURRENT) == capabilities_for(SHARED) == capabilities_for(OLD)
    )
    assert (
        capabilities_for(CURRENT).research_input_schema
        == "mineagent.research-inputs.v1"
    )
    assert (
        capabilities_for(CURRENT).workflow_schema
        == "mineagent.research-workflow-closure.v2"
    )


@pytest.mark.parametrize(
    "revision", [PREVIOUS, LEGACY, OLDER, PENULTIMATE, EARLIER, OLD, SHARED, CURRENT]
)
def test_normal_note_gate_accepts_exact_known_maintenance(revision):
    session = SimpleNamespace(scalars=lambda _: SimpleNamespace(all=lambda: [revision]))
    assert require_normal_oe_notes(session) is None


@pytest.mark.parametrize(
    "revisions",
    [
        [],
        ["032b_future"],
        ["032g"],
        [CURRENT + "_future"],
        ["033_safe_workflow_branches"],
        [PREVIOUS, LEGACY],
        [LEGACY, LEGACY],
        [LEGACY, EARLIER],
        [PREVIOUS, EARLIER],
        [EARLIER, EARLIER],
        [EARLIER, OLD],
        [EARLIER, CURRENT],
        [EARLIER, SHARED],
        [LEGACY, OLD],
        [PREVIOUS, OLD],
        [OLD, OLD],
        [OLD, CURRENT],
        [SHARED, SHARED],
        [SHARED, CURRENT],
        [OLD, SHARED],
        [CURRENT, CURRENT],
    ],
)
def test_normal_note_gate_rejects_unknown_and_multiple_heads(revisions):
    session = SimpleNamespace(scalars=lambda _: SimpleNamespace(all=lambda: revisions))
    with pytest.raises(DomainError):
        require_normal_oe_notes(session)


def test_every_backend_predecessor_gate_preserves_exact_append_maintenance():
    """Audit the 49 existing gate files/80 memberships, not revision ordering."""
    import ast
    from pathlib import Path

    found = {}
    for path in Path("backend/app").glob("*.py"):
        if path.name == "operations_schemas.py":
            continue  # Public Literal is checked by generated OpenAPI equality.
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                continue
            values = []
            for item in node.elts:
                if isinstance(item, ast.Constant):
                    values.append(item.value)
                elif (
                    isinstance(item, ast.List)
                    and len(item.elts) == 1
                    and isinstance(item.elts[0], ast.Constant)
                ):
                    values.append([item.elts[0].value])
                else:
                    values.append(None)
            old = [SHARED] if [SHARED] in values else SHARED
            current = [CURRENT] if old == [SHARED] else CURRENT
            if old not in values or len(values) == 1:
                continue
            assert values.count(current) == 1, path
            assert values.index(current) == values.index(old) + 1, path
            assert "033_safe_workflow_branches" not in values, path
            assert ["033_safe_workflow_branches"] not in values, path
            assert [OLD, CURRENT] not in values, path
            assert [SHARED, CURRENT] not in values, path
            assert [CURRENT, CURRENT] not in values, path
            found[path.name] = found.get(path.name, 0) + 1
    assert len(found) == 49
    assert sum(found.values()) == 80
    assert capabilities_for(PREVIOUS) == capabilities_for(LEGACY)
    assert capabilities_for(LEGACY) == capabilities_for(OLD)


def test_maintenance_receipt_and_contract_grammar_agree_exactly():
    import json
    import re
    from pathlib import Path
    from typing import get_args

    from backend.app.operations_backup import REVISIONS
    from backend.app.operations_schemas import BackupReceipt

    actual = get_args(BackupReceipt.model_fields["schema_revision"].annotation)
    assert actual[-2:] == (SHARED, CURRENT)
    assert set(actual) == REVISIONS
    openapi = json.loads(Path("packages/contracts/openapi.json").read_text())
    enum = openapi["components"]["schemas"]["BackupReceipt"]["properties"][
        "schema_revision"
    ]["enum"]
    assert enum == list(actual)
    client = Path("frontend/src/client/types.gen.ts").read_text()
    declaration = next(
        line
        for line in client.splitlines()
        if line.strip().startswith("schema_revision:")
    )
    assert re.findall(r"'([^']+)'", declaration) == list(actual)


@pytest.mark.parametrize("revision", ["032g", CURRENT + "_future", "033_future"])
def test_maintenance_never_infers_future_capabilities_or_receipts(revision):
    from pydantic import TypeAdapter, ValidationError

    from backend.app.operations_schemas import BackupReceipt

    with pytest.raises(ValueError, match="Unsupported application schema revision"):
        capabilities_for(revision)
    with pytest.raises(ValidationError):
        TypeAdapter(
            BackupReceipt.model_fields["schema_revision"].annotation
        ).validate_python(revision)

"""Explicit additive research grammar; no standalone partial closure authority."""

from uuid import uuid4

import pytest

from backend.app import research_backup as backup


@pytest.mark.parametrize(
    "leaf",
    ["output/result.json", "inputs", "inputs/strategy.json", "inputs/market.json"],
)
def test_structured_paths_only_exact_successor(leaf):
    path = f"research-processes/{uuid4()}/{uuid4()}/{leaf}"
    with pytest.raises(ValueError):
        backup.validate_path(path, schema=backup.SCHEMA_V8)
    for schema in (
        backup.SCHEMA_V9,
        backup.SCHEMA_V10,
        backup.SCHEMA_V11,
        backup.SCHEMA_V12,
        backup.SCHEMA_V13,
        backup.SCHEMA_V14,
        backup.SCHEMA_V15,
    ):
        backup.validate_path(path, schema=schema)
    with pytest.raises(ValueError):
        backup.validate_path(path, schema="mineagent.research-restore-closure.v17")


def test_successor_preserves_oe_binary_grammar_without_new_json_assumption():
    path = f"research-processes/{uuid4()}/{uuid4()}/output/native-export.bin"
    for schema in (backup.SCHEMA_V8, backup.SCHEMA_V9):
        backup.validate_path(path, schema=schema)


@pytest.mark.parametrize(
    "schema,pending", [(backup.SCHEMA_V9, False), (backup.SCHEMA_V8, True)]
)
def test_partial_research_validation_never_implies_structured_graph_acceptance(
    schema, pending
):
    with pytest.raises(ValueError, match="structured final links"):
        backup.validate_closure(
            {"schema_version": schema},
            None,
            None,
            10,
            inventory=[],
            structured_links_pending=pending,
        )

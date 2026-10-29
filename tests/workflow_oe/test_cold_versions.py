"""Explicit v2 inventory is not permission to bypass legacy semantic readers."""

import pytest

from backend.app import oe_structured_backup as oe
from backend.app import research_workflow_backup as workflow


class Cursor:
    def __init__(self):
        self.closed = False

    def scalars(self):
        return iter(())

    def close(self):
        self.closed = True


class Connection:
    def __init__(self):
        self.statements = []
        self.cursors = []

    def execution_options(self, **kwargs):
        raise AssertionError("Reader must not mutate connection streaming state")

    def execute(self, statement, params):
        assert statement.get_execution_options() == {
            "stream_results": True,
            "yield_per": 1,
        }
        assert params == {"limit": 101}
        self.statements.append(str(statement))
        cursor = Cursor()
        self.cursors.append(cursor)
        return cursor


@pytest.mark.parametrize("module", [workflow, oe])
def test_old_reader_remains_explicit_and_cursor_options_statement_scoped(module):
    connection = Connection()
    closure = module.read_closure(connection, 100)
    assert closure["schema_version"] == module.SCHEMA
    module.indexes(closure)
    assert all(cursor.closed for cursor in connection.cursors)
    assert all("provider-run.v2" not in sql for sql in connection.statements)
    assert all("oe_workflow_submissions" not in sql for sql in connection.statements)


@pytest.mark.parametrize("module", [workflow, oe])
def test_new_inventory_requires_explicit_parser_old_semantic_entry_rejects(module):
    connection = Connection()
    closure = module.read_closure(connection, 100, schema=module.SCHEMA_V2)
    module.indexes(closure, schema=module.SCHEMA_V2)
    with pytest.raises(ValueError):
        module.indexes(closure)
    if module is workflow:
        with pytest.raises(ValueError):
            module.validate_metadata(closure)
        assert "oe_workflow_submissions" in closure["tables"]
    else:
        assert any("provider-run.v2" in sql for sql in connection.statements)
    assert all(cursor.closed for cursor in connection.cursors)


@pytest.mark.parametrize("module", [workflow, oe])
def test_unknown_schema_rejected_before_sql(module):
    connection = Connection()
    with pytest.raises(ValueError):
        module.read_closure(connection, 100, schema="unknown")
    assert not connection.statements


def test_lineage_inventory_matches_exact_frozen_model_fields():
    from backend.app.oe_workflow_models import OEWorkflowSubmission

    assert set(workflow.FIELDS_V2["oe_workflow_submissions"].split()) == set(
        OEWorkflowSubmission.__table__.columns.keys()
    )


def test_exact_successor_capability_preserves_historical_tuple():
    from backend.app.schema_capabilities import capabilities_for

    old = capabilities_for("030_declared_upload_basket")
    new = capabilities_for("031_workflow_structured_stage")
    assert (
        old.research_closure_schema,
        old.rd_source_closure_schema,
        old.workflow_schema,
        old.oe_structured_closure_schema,
    ) == (
        "mineagent.research-restore-closure.v15",
        "mineagent.rd-source-restore-closure.v10",
        "mineagent.research-workflow-closure.v1",
        oe.SCHEMA,
    )
    assert (
        new.research_closure_schema,
        new.rd_source_closure_schema,
        new.workflow_schema,
        new.oe_structured_closure_schema,
    ) == (
        "mineagent.research-restore-closure.v16",
        "mineagent.rd-source-restore-closure.v11",
        "mineagent.research-workflow-closure.v2",
        oe.SCHEMA_V2,
    )
    changed = {
        "research_closure_schema",
        "rd_source_closure_schema",
        "workflow_schema",
        "oe_structured_closure_schema",
    }
    assert all(
        getattr(old, field) == getattr(new, field)
        for field in old.__dataclass_fields__
        if field not in changed
    )


def test_new_parent_inventory_inherits_without_reinterpreting_old_fields():
    from backend.app import rdagent_backup as source
    from backend.app import research_backup as research

    assert research.closure_fields(research.SCHEMA_V16) == research.closure_fields(
        research.SCHEMA_V15
    )
    assert source.closure_fields(source.SCHEMA_V11) == source.closure_fields(
        source.SCHEMA_V10
    )
    assert source.closure_fields(source.SCHEMA_V6) == source.FIELDS_V5
    with pytest.raises(ValueError):
        source.closure_fields("mineagent.rd-source-restore-closure.v12")
    with pytest.raises(ValueError):
        research.closure_fields("mineagent.research-restore-closure.v17")


@pytest.mark.parametrize(
    "revision",
    [
        "029_research_workflow",
        "030_declared_upload_basket",
        "031_workflow_structured_stage",
    ],
)
def test_operation_envelopes_have_exact_revision_pair(revision):
    from backend.app import oe_structured_operations as oe_ops
    from backend.app import research_workflow_operations as wf_ops
    from backend.app.schema_capabilities import capabilities_for

    caps = capabilities_for(revision)
    assert wf_ops.enabled(caps)
    assert oe_ops._schema(caps) == caps.oe_structured_closure_schema
    connection = Connection()
    closure = workflow.read_closure(
        connection, 100, schema=wf_ops.serialized_schema(caps)
    )
    from backend.app.research_input_backup import SCHEMA as input_schema

    snapshot = {
        "workflow_closure": closure,
        "research_input_closure": {"schema_version": input_schema},
    }
    assert wf_ops.closures_for(snapshot, caps)[0] is closure


@pytest.mark.parametrize(
    "field",
    [
        "research_closure_schema",
        "rd_source_closure_schema",
        "workflow_schema",
        "oe_structured_closure_schema",
    ],
)
def test_partial_successor_capability_is_not_sufficient(field):
    from dataclasses import replace

    from backend.app import oe_structured_operations as oe_ops
    from backend.app import research_workflow_operations as wf_ops
    from backend.app.schema_capabilities import capabilities_for

    old = capabilities_for("030_declared_upload_basket")
    new = capabilities_for("031_workflow_structured_stage")
    mixed = replace(new, **{field: getattr(old, field)})
    validate = (
        oe_ops._schema if field == "oe_structured_closure_schema" else wf_ops.enabled
    )
    with pytest.raises(ValueError):
        validate(mixed)


@pytest.mark.parametrize("version", [13, 14, 15, 16])
def test_scoped_result_reader_agrees_with_historical_schema_capability(version):
    from backend.app.oe_structured_result_history import _closure_schema_for_research

    assert _closure_schema_for_research(
        f"mineagent.research-restore-closure.v{version}"
    ) == (oe.SCHEMA_V2 if version == 16 else oe.SCHEMA)


@pytest.mark.parametrize("version", [12, 17, "16.0", "016"])
def test_scoped_result_reader_rejects_unknown_or_alias_research_schema(version):
    from backend.app.oe_structured_result_history import _closure_schema_for_research

    with pytest.raises(ValueError):
        _closure_schema_for_research(f"mineagent.research-restore-closure.v{version}")

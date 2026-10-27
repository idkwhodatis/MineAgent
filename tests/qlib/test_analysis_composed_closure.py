"""Empty exact022 component composition, not database or physical acceptance."""

import pytest

from backend.app import execution_snapshot_backup as execution
from backend.app import factor_analysis_backup as analysis
from backend.app import factor_backup as factors
from backend.app import factor_native_backup as native
from backend.app import rdagent_backup as rd
from backend.app import research_backup as research
from backend.app.rdagent_execution_links import validate_execution_generation_links


class EmptyConnection:
    def execute(self, *args, **kwargs):
        return self

    def scalars(self):
        return self

    def all(self):
        return []

    def __iter__(self):
        return iter(())

    def close(self):
        pass


def test_all_empty022_closures_compose_without_historical_projection():
    connection = EmptyConnection()
    revision = "022_factor_analysis"
    f = factors.read_closure(connection, 100, revision=revision)
    r = research.read_closure(connection, 100, revision=revision)
    d = rd.read_closure(connection, 100, revision=revision)
    n = native.read_closure(connection, 100, schema=native.SCHEMA_V3)
    e = execution.read_closure(connection, 100, schema=execution.SCHEMA_V2)
    a = analysis.read_closure(connection, 100)

    def absent(*args):
        raise AssertionError("Empty closure tried reading bytes")

    research.validate_closure(
        r, absent, absent, 100, inventory=[], read_bytes=absent, rd_source_closure=d
    )
    rd.validate_closure(
        d,
        r,
        f,
        absent,
        100,
        execution_links_pending=True,
        configured_inputs_pending=True,
    )
    native.validate_closure(
        n, absent, absent, 100, inventory=[], research_closure=r, rd_source_closure=d
    )
    execution.validate_closure(
        e,
        absent,
        absent,
        100,
        inventory_rows=[],
        research_closure=r,
        rd_source_closure=d,
        native_closure=n,
        direct_analysis_closure=a,
    )
    analysis.validate_closure(
        a,
        research_closure=r,
        rd_source_closure=d,
        execution_closure=e,
        factor_closure=f,
        read_bytes=absent,
        maximum_rows=100,
    )
    validate_execution_generation_links(d, e, r, absent, 100, direct_analysis_closure=a)
    assert analysis.manifest_keys(a) == set()


def test_old_path_grammar_rejects_qlib_analysis_output():
    path = "research-processes/00000000-0000-0000-0000-000000000001/00000000-0000-0000-0000-000000000002/output/analysis.json"
    for schema in (
        research.SCHEMA,
        research.SCHEMA_V2,
        research.SCHEMA_V3,
        research.SCHEMA_V4,
        research.SCHEMA_V5,
        research.SCHEMA_V6,
    ):
        with pytest.raises(ValueError):
            research.validate_path(path, schema=schema)
    research.validate_path(path, schema=research.SCHEMA_V7)

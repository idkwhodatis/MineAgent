"""Exact028/029 dependency dispatch; empty closure never proves a process ran."""

import pytest

from backend.app import execution_snapshot_backup as execution
from backend.app import factor_analysis_backup as analysis
from backend.app import factor_native_backup as native
from backend.app import rdagent_backup as rd
from backend.app.rdagent_execution_links import validate_execution_generation_links
from backend.app.research_backup import FIELDS as RESEARCH_FIELDS


def closure(schema, fields):
    return {"schema_version": schema, "tables": {name: [] for name in fields}}


def validate(kind, research_version, rd_version):
    research = closure(
        "mineagent.research-restore-closure." + research_version, RESEARCH_FIELDS
    )
    source = closure(
        "mineagent.rd-source-restore-closure." + rd_version,
        rd.closure_fields("mineagent.rd-source-restore-closure." + rd_version),
    )
    source["external_bindings"] = {"runs": []}
    n = closure(native.SCHEMA_V3, native.FIELDS)
    ex = closure(execution.SCHEMA_V2, execution.FIELDS)
    direct = closure(analysis.SCHEMA, analysis.FIELDS)

    def no_bytes(*args):
        pytest.fail("Empty retained graph must not invent physical bytes")

    if kind == "native":
        native.validate_closure(
            n,
            no_bytes,
            no_bytes,
            100,
            inventory=[],
            research_closure=research,
            rd_source_closure=source,
        )
    elif kind == "execution":
        execution.validate_closure(
            ex,
            no_bytes,
            no_bytes,
            100,
            inventory_rows=[],
            research_closure=research,
            rd_source_closure=source,
            native_closure=n,
            direct_analysis_closure=direct,
        )
    elif kind == "analysis":
        analysis.validate_closure(
            direct,
            research_closure=research,
            rd_source_closure=source,
            execution_closure=ex,
            factor_closure={"tables": {"factor_objects": []}},
            read_bytes=no_bytes,
            maximum_rows=100,
        )
    else:
        assert validate_execution_generation_links(
            source, ex, research, no_bytes, 100, direct_analysis_closure=direct
        )


@pytest.mark.parametrize("kind", ["native", "execution", "analysis", "final_links"])
@pytest.mark.parametrize(
    "pair", [("v10", "v5"), ("v11", "v6"), ("v12", "v7"), ("v13", "v8")]
)
def test_exact_inherited_pair_accepts_empty_without_execution_claim(kind, pair):
    validate(kind, *pair)


@pytest.mark.parametrize("kind", ["native", "execution", "analysis", "final_links"])
@pytest.mark.parametrize(
    "pair",
    [
        ("v11", "v5"),
        ("v10", "v6"),
        ("v12", "v6"),
        ("v11", "v7"),
        ("v13", "v7"),
        ("v12", "v8"),
    ],
)
def test_mixed_or_unknown_pair_is_not_relabelled(kind, pair):
    with pytest.raises(ValueError):
        validate(kind, *pair)

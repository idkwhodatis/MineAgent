"""Build genuine pre-014 archive fixtures through their actual table projection.

This is fixture construction only. It neither changes a database nor pretends
that the current application can run against an unmigrated schema. Archive
verification first compares all real historical rows/bytes; current application
reads happen only after the restored database has been upgraded normally.
"""

from contextlib import ExitStack, contextmanager
from functools import wraps
from importlib import import_module
from unittest.mock import patch

from sqlalchemy import MetaData, Table, text
from sqlalchemy.orm import registry

HISTORICAL_REVISIONS = frozenset(
    {
        "007_research_specs",
        "008_comparisons",
        "009_independent_evaluations",
        "010_research_notes",
        "011_note_pin_trigger",
        "012_factor_library",
        "013_factor_import",
    }
)
# Exact fixture-builder call graph. Never intercept emitted SQL or replace a
# Session/connection, and never enable this projection in production code.
FIXTURE_MODULES = (
    "backend.app.models",
    "backend.app.services",
    "backend.app.workbench_services",
    "backend.app.research_note_services",
    "backend.app.comparison_services",
    "backend.app.independent_services",
    "backend.app.experiment_services",
    "backend.app.research_services",
    "backend.app.portable_services",
    "backend.app.trace_services",
)


@contextmanager
def historical_run_projection(engine):
    from backend.app.models import ResearchRun

    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        if revision not in HISTORICAL_REVISIONS:
            raise ValueError(
                "Historical fixture projection requires an exact old revision"
            )
        metadata = MetaData()
        table = Table("research_runs", metadata, autoload_with=connection)
    if {"purpose", "engine"} & set(table.c.keys()):
        raise ValueError("Historical fixture must not contain new identity columns")
    # Preserve the old Python-side constructor defaults for columns actually in
    # this deployed table. No new column, default or constraint is invented.
    for column in table.c:
        current = ResearchRun.__table__.c.get(column.name)
        if current is not None and current.default is not None:
            column.default = current.default._copy()

    class HistoricalResearchRun:
        purpose = "legacy_strategy"
        engine = "legacy"

        @property
        def execution_mode(self):
            return self.config.get("execution_mode", "seed_evaluation")

    mapper = registry()
    mapper.map_imperatively(HistoricalResearchRun, table)
    modules = [import_module(name) for name in FIXTURE_MODULES]
    try:
        with ExitStack() as stack:
            for module in modules:
                if getattr(module, "ResearchRun", None) is ResearchRun:
                    stack.enter_context(
                        patch.object(module, "ResearchRun", HistoricalResearchRun)
                    )
            yield
    finally:
        mapper.dispose()


def historical_builder(function):
    @wraps(function)
    def wrapped(engine, *args, **kwargs):
        with historical_run_projection(engine):
            return function(engine, *args, **kwargs)

    return wrapped

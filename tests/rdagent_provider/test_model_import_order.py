"""Fresh interpreters catch registry cycles hidden by aggregate test collection."""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "entry",
    [
        "backend.app.models",
        "backend.app.research_broker_models",
        "backend.app.research_broker_services",
        "backend.app.rdagent_provider_models",
        "backend.app.rdagent_models",
        "backend.app.rdagent_provider_broker",
    ],
)
def test_provider_registration_is_independent_of_first_import(entry):
    code = f"""
import importlib
importlib.import_module({entry!r})
from backend.app.models import Base
from backend.app.rdagent_provider_models import RDProviderTransportReceipt
from backend.app.research_broker_models import bound_constraints, bound_fk, object_fk
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

table = RDProviderTransportReceipt.__table__
assert Base.metadata.tables['rd_provider_transport_receipts'] is table
assert set(table.columns.keys()) == set('id owner_id run_id created_at attempt_id generation transport_id kind receipt_object_id receipt_object_kind receipt_sha256 size_bytes'.split())
def shape(constraint):
    if isinstance(constraint, ForeignKeyConstraint):
        return ('fk', tuple(c.parent.name for c in constraint.elements), tuple(c.target_fullname for c in constraint.elements))
    if isinstance(constraint, UniqueConstraint):
        return ('unique', tuple(constraint.columns.keys()))
    if isinstance(constraint, CheckConstraint):
        return ('check', str(constraint.sqltext))
    return ('primary',)
expected = [*bound_constraints(), bound_fk('transport_id', 'research_model_transports'), object_fk('receipt')]
# Bind temporary constraints to a temporary table so their local columns resolve.
from sqlalchemy import MetaData, Table, Column, String
probe = Table('probe', MetaData(), *(Column(name, String) for name in table.columns.keys()), *expected)
assert set(map(shape, expected)) <= set(map(shape, table.constraints))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

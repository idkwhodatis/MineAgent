"""Offline inactive-schema checks; real PostgreSQL activation is a later gate."""

from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from backend.app.rdagent_models import (
    FactorCutoffExecution,
    FactorGenerationOutcome,
    FactorResearchSpec,
)
from backend.app.rdagent_services import bind_research_attempt
from backend.app.services import DomainError
from packages.research_runtime.fences import AttemptFence


def test_cutoff_and_outcome_bind_exact_generation_source_tuple():
    expected = {"owner_id", "run_id", "evaluated_source_id", "factor_version_id"}
    for model in (FactorCutoffExecution, FactorGenerationOutcome):
        keys = [
            fk
            for fk in model.__table__.foreign_key_constraints
            if fk.referred_table.name == "factor_generation_sources"
            and "evaluated_source_id" in fk.column_keys
        ]
        assert len(keys) == 1 and set(keys[0].column_keys) == expected
        assert "FOREIGN KEY" in str(
            CreateTable(model.__table__).compile(dialect=postgresql.dialect())
        )


def test_spec_has_no_strategy_or_legacy_checkpoint_binding():
    assert set(FactorResearchSpec.__table__.columns.keys()) == {
        "id",
        "owner_id",
        "run_id",
        "created_at",
        "config",
        "config_sha256",
        "input_sha256",
    }


def test_no_session_or_process_authority_can_be_fabricated():
    class BadSession:
        def __init__(self):
            self.info = {}

        def get(self, *args):
            raise AssertionError("must reject before database access")

    with pytest.raises(DomainError):
        bind_research_attempt(
            BadSession(),
            AttemptFence(UUID(int=1), UUID(int=2), UUID(int=3), 1, UUID(int=4)),
        )


def test_source_binds_complete_response_assembly_not_last_response():
    from backend.app.rdagent_models import FactorGenerationSource

    table = FactorGenerationSource.__table__
    assert "model_operation_id" not in table.columns
    assembly = next(
        fk
        for fk in table.foreign_key_constraints
        if fk.referred_table.name == "factor_response_assemblies"
    )
    assert {"call_id", "assembly_id", "start_id", "context_id"} <= set(
        assembly.column_keys
    )
    factor = next(
        fk
        for fk in table.foreign_key_constraints
        if fk.referred_table.name == "factor_versions"
    )
    assert set(factor.column_keys) == {"owner_id", "factor_version_id", "source_sha256"}


def test_assembly_parts_and_request_predecessors_cannot_cross_calls():
    from backend.app.rdagent_models import (
        FactorGenerationModelOperation,
        FactorResponseAssemblyPart,
    )

    for model, target in (
        (FactorGenerationModelOperation, "factor_generation_model_operations"),
        (FactorResponseAssemblyPart, "factor_response_assemblies"),
        (FactorResponseAssemblyPart, "factor_generation_model_operations"),
    ):
        fk = next(
            fk
            for fk in model.__table__.foreign_key_constraints
            if fk.referred_table.name == target
        )
        assert "call_id" in fk.column_keys


def test_checkpoint_and_typed_native_attachments_have_exact_tuple_bindings():
    from backend.app.rdagent_models import FactorCutoffOutcome, FactorResearchCheckpoint

    checkpoint = next(
        fk
        for fk in FactorResearchCheckpoint.__table__.foreign_key_constraints
        if fk.referred_table.name == "factor_generation_outcomes"
    )
    assert "completed_step" in checkpoint.column_keys
    attachments = [
        fk
        for fk in FactorCutoffOutcome.__table__.foreign_key_constraints
        if fk.referred_table.name == "factor_objects"
    ]
    assert len(attachments) == 2
    assert all(len(fk.column_keys) == 4 for fk in attachments)
    ddl = str(
        CreateTable(FactorCutoffOutcome.__table__).compile(dialect=postgresql.dialect())
    )
    assert "native_hdf" in ddl and "values_partition" in ddl and "IS TRUE" in ddl


@pytest.mark.parametrize("operation", ["begin_generation", "open_upstream_call"])
def test_operational_drafts_reject_missing_transaction_authority(operation):
    from backend.app import rdagent_services

    class NoAuthority:
        def __init__(self):
            self.info = {}

        def scalar(self, *args):
            raise AssertionError("must reject before touching domain data")

    fence = AttemptFence(UUID(int=1), UUID(int=2), UUID(int=3), 1, UUID(int=4))
    kwargs = (
        {
            "logical_step": 0,
            "context_id": UUID(int=5),
            "mutation_reservation_id": UUID(int=6),
        }
        if operation == "begin_generation"
        else {
            "start_id": UUID(int=5),
            "call_id": UUID(int=6),
            "phase": "generation",
            "kind": "chat",
        }
    )
    with pytest.raises(DomainError):
        getattr(rdagent_services, operation)(NoAuthority(), fence, **kwargs)


def test_mutation_authority_has_database_one_to_one_binding():
    from sqlalchemy import UniqueConstraint

    from backend.app.rdagent_models import FactorGenerationStart

    assert any(
        isinstance(constraint, UniqueConstraint)
        and tuple(constraint.columns.keys()) == ("mutation_reservation_id",)
        for constraint in FactorGenerationStart.__table__.constraints
    )


def test_call_exact_replay_precedes_terminal_write_guard(monkeypatch):
    """Mock transaction sequencing only; not a substitute for real PG tests."""
    from types import SimpleNamespace

    from backend.app import rdagent_services as service
    from backend.app.rdagent_models import FactorGenerationStart

    fence = AttemptFence(UUID(int=1), UUID(int=2), UUID(int=3), 1, UUID(int=4))
    start = SimpleNamespace(id=UUID(int=5), context_id=UUID(int=6))
    existing = SimpleNamespace(
        **service._scope(fence),
        id=UUID(int=7),
        start_id=start.id,
        context_id=start.context_id,
        phase="generation",
        kind="chat",
    )
    monkeypatch.setattr(service, "require_research_spec", lambda *args: (None, None))
    monkeypatch.setattr(
        service,
        "_scope_row",
        lambda session, model, *args, **kwargs: (
            start if model is FactorGenerationStart else None
        ),
    )

    class ReplayOnly:
        def get(self, *args):
            return existing

        def scalar(self, *args):
            raise AssertionError(
                "exact immutable replay must not reach new-write terminal check"
            )

    assert (
        service.open_upstream_call(
            ReplayOnly(),
            fence,
            start_id=start.id,
            call_id=existing.id,
            phase="generation",
            kind="chat",
        )
        is existing
    )
    with pytest.raises(DomainError) as changed:
        service.open_upstream_call(
            ReplayOnly(),
            fence,
            start_id=start.id,
            call_id=existing.id,
            phase="critique",
            kind="chat",
        )
    assert changed.value.code == "factor_call_drift"

    class NewAfterTerminal(ReplayOnly):
        def get(self, *args):
            return None

        def scalar(self, *args):
            return object()

    with pytest.raises(DomainError) as closed:
        service.open_upstream_call(
            NewAfterTerminal(),
            fence,
            start_id=start.id,
            call_id=UUID(int=8),
            phase="generation",
            kind="chat",
        )
    assert closed.value.code == "factor_generation_closed"


def test_cutoff_requires_versioned_prior_plus_current_prefix():
    ddl = str(
        CreateTable(FactorCutoffExecution.__table__).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "mineagent.factor-prefix-input.v2" in ddl
    assert "mineagent.factor-prefix-input.v1" not in ddl

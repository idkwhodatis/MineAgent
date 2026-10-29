"""Real strict codecs with explicit row/session doubles, NOT PostgreSQL execution."""

from datetime import date
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app.oe_structured_identity import (
    decode_candidate,
    decode_spec,
    require_run_identity,
)
from backend.app.oe_structured_models import (
    OEStructuredCandidate,
    OEStructuredExecution,
    OEStructuredProviderExchange,
    OEStructuredSpec,
)
from backend.app.services import DomainError
from backend.app.structured_factor_models import StructuredStrategyVersion
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.candidate import (
    CandidateDocument,
    TypedFrozenCandidateDomain,
)
from packages.openevolve_structured.contracts import StructuredSearchSpec
from packages.openevolve_structured.provider_run import (
    FixedExecutionInputs,
    ProviderStructuredRunConfig,
    StructuredRuntimePins,
)
from tests.openevolve.test_structured_provider_submission import binding, request


def graph():
    user, run_id, version_id = uuid4(), uuid4(), uuid4()
    req = request()
    program = req.seed_parameters.program(
        {"start": date(2024, 3, 13), "end": date(2024, 3, 14)}
    )
    fixed = FixedExecutionInputs(
        snapshot_id=req.snapshot_id,
        snapshot_manifest_sha256="a" * 64,
        snapshot_input_sha256="b" * 64,
        snapshot_output_sha256="c" * 64,
        factor_version_id=uuid4(),
        dataset_version_id=uuid4(),
        dataset_sha256="d" * 64,
        source_policy={"acknowledged_unverified": True},
        market_sha256="e" * 64,
        market_size_bytes=100,
        release_receipt_ids=(uuid4(),),
    )
    search = StructuredSearchSpec(
        owner_id=user,
        run_id=run_id,
        execution_binding_id=run_id,
        execution_binding_sha256=digest(fixed),
        domain=TypedFrozenCandidateDomain(seed_program=program),
        seed_candidate_sha256=digest(CandidateDocument(program=program)),
        objective=req.objective,
        maximum_mutation_slots=1,
    )
    pins = StructuredRuntimePins(
        controller_image_id="sha256:" + "1" * 64,
        controller_installed_parent_image_id="sha256:" + "2" * 64,
        controller_inventory_sha256="3" * 64,
        engine_image_id="sha256:" + "4" * 64,
        engine_installed_parent_image_id="sha256:" + "5" * 64,
        engine_dependency_lock_sha256="6" * 64,
    )
    config = ProviderStructuredRunConfig(
        search=search,
        fixed_inputs=fixed,
        model_binding=binding(),
        runtime=pins,
        budget=req.budget,
        authenticated_request_sha256=req.sha256,
    )
    run = NS(
        id=run_id,
        owner_id=user,
        purpose="strategy_search",
        engine="openevolve",
        strategy_version_id=None,
        dataset_version_id=fixed.dataset_version_id,
        generation=1,
        request_sha256=digest(config),
        config=config.model_dump(mode="json"),
    )
    spec = OEStructuredSpec(
        run_id=run_id,
        owner_id=user,
        submission_id=uuid4(),
        request=req.model_dump(mode="json"),
        request_canonical=canonical_bytes(req).decode(),
        request_sha256=req.sha256,
        preflight_sha256="f" * 64,
        config=config.model_dump(mode="json"),
        config_canonical=canonical_bytes(config).decode(),
        config_sha256=digest(config),
        snapshot_id=req.snapshot_id,
        dataset_id=fixed.dataset_version_id,
        seed_program_version_id=version_id,
    )
    version = StructuredStrategyVersion(
        id=version_id,
        owner_id=user,
        parent_version_id=None,
        program=program.model_dump(mode="json"),
        program_canonical=canonical_bytes(program).decode(),
        program_sha256=digest(program),
    )
    candidate = OEStructuredCandidate(
        id=uuid4(),
        owner_id=user,
        run_id=run_id,
        attempt_id=uuid4(),
        generation=1,
        epoch_id=uuid4(),
        upstream_program_id=uuid4(),
        ordinal=0,
        parent_candidate_id=None,
        provider_exchange_id=None,
        program_version_id=version_id,
        candidate_canonical=canonical_bytes(
            CandidateDocument(program=program)
        ).decode(),
        candidate_sha256=search.seed_candidate_sha256,
        program_sha256=digest(program),
        origin="authenticated_user_seed",
    )
    rows = {
        (OEStructuredSpec, run_id): spec,
        (StructuredStrategyVersion, version_id): version,
    }
    return (
        NS(get=lambda kind, key: rows.get((kind, key))),
        run,
        spec,
        version,
        candidate,
        rows,
    )


def test_jsonb_dates_and_uuid_are_decoded_in_strict_json_mode():
    session, run, spec, version, candidate, _ = graph()
    assert require_run_identity(run).search.run_id == run.id
    assert decode_spec(session, run)[0] is spec
    assert (
        decode_candidate(session, run, candidate).program_sha256
        == version.program_sha256
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("purpose", "factor_strategy"),
        ("engine", "structured"),
        ("strategy_version_id", uuid4()),
        ("generation", 2),
        ("owner_id", uuid4()),
        ("request_sha256", "0" * 64),
    ],
)
def test_old_run_identity_or_changed_binding_rejected(field, value):
    session, run, *_ = graph()
    setattr(run, field, value)
    with pytest.raises(DomainError):
        decode_spec(session, run)


@pytest.mark.parametrize(
    "field,value",
    [
        ("origin", "model_proposal"),
        ("parent_candidate_id", uuid4()),
        ("provider_exchange_id", uuid4()),
        ("candidate_sha256", "0" * 64),
        ("program_sha256", "0" * 64),
    ],
)
def test_seed_does_not_gain_proposal_origin_or_detached_hash(field, value):
    session, run, _, _, candidate, _ = graph()
    setattr(candidate, field, value)
    with pytest.raises(DomainError):
        decode_candidate(session, run, candidate)


def test_request_raw_canonical_tamper_rejected_even_same_json_value():
    session, run, spec, *_ = graph()
    spec.request_canonical += " "
    with pytest.raises(DomainError):
        decode_spec(session, run)


def test_execution_graph_is_distinct_and_allows_same_snapshot_children():
    table = OEStructuredExecution.__table__
    targets = {
        element.target_fullname
        for fk in table.foreign_key_constraints
        for element in fk.elements
    }
    assert "oe_structured_candidates.id" in targets
    assert not any(
        "strategy_search_specs" in name or "factor_strategy_executions" in name
        for name in targets
    )
    unique = {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("candidate_id",) in unique
    assert ("run_id", "snapshot_id") not in unique


def test_mutation_cannot_use_cross_attempt_response_or_wrong_program_parent():
    session, run, _spec, seed_version, seed, rows = graph()
    child_program = seed_version.program.copy()
    child_program["threshold"] = 0.1
    import json

    from packages.structured_factor_strategy.contracts import StructuredProgram

    program = StructuredProgram.model_validate_json(json.dumps(child_program))
    version = StructuredStrategyVersion(
        id=uuid4(),
        owner_id=run.owner_id,
        parent_version_id=seed_version.id,
        program=program.model_dump(mode="json"),
        program_canonical=canonical_bytes(program).decode(),
        program_sha256=digest(program),
    )
    exchange = OEStructuredProviderExchange(
        id=uuid4(),
        owner_id=run.owner_id,
        run_id=run.id,
        attempt_id=seed.attempt_id,
        generation=1,
        epoch_id=seed.epoch_id,
        parent_candidate_id=seed.id,
    )
    child = OEStructuredCandidate(
        id=uuid4(),
        owner_id=run.owner_id,
        run_id=run.id,
        attempt_id=seed.attempt_id,
        generation=1,
        epoch_id=seed.epoch_id,
        ordinal=1,
        parent_candidate_id=seed.id,
        provider_exchange_id=exchange.id,
        program_version_id=version.id,
        candidate_canonical=canonical_bytes(
            CandidateDocument(program=program)
        ).decode(),
        candidate_sha256=digest(CandidateDocument(program=program)),
        program_sha256=digest(program),
        origin="model_proposal",
    )
    rows.update(
        {
            (StructuredStrategyVersion, version.id): version,
            (OEStructuredCandidate, seed.id): seed,
            (OEStructuredProviderExchange, exchange.id): exchange,
        }
    )
    assert decode_candidate(session, run, child).program == program
    exchange.attempt_id = uuid4()
    with pytest.raises(DomainError):
        decode_candidate(session, run, child)
    exchange.attempt_id = seed.attempt_id
    version.parent_version_id = None
    with pytest.raises(DomainError):
        decode_candidate(session, run, child)

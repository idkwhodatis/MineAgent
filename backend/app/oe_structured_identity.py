"""027 persisted declaration/candidate decoding, never an execution grant.

Callers separately acquire the current027 fenced transaction. These helpers do
not widen old024 Python identity or old025 acceptance-run identity.
"""

from backend.app.oe_structured_models import (
    OEStructuredCandidate,
    OEStructuredProviderExchange,
    OEStructuredSpec,
)
from backend.app.oe_structured_provider_submission import (
    ProviderStructuredSearchRequest,
)
from backend.app.services import DomainError
from backend.app.structured_factor_models import StructuredStrategyVersion
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.candidate import (
    CandidateDocument,
    project_candidate,
)
from packages.openevolve_structured.workflow_run import (
    WorkflowStructuredRunConfig,
    parse_provider_run,
    submission_request_sha256,
)
from packages.structured_factor_strategy.contracts import StructuredProgram


def fail():
    raise DomainError(
        "oe_structured_binding",
        "Exact persisted structured search binding required",
        409,
    )


def require_run_identity(run):
    try:
        # Config JSONB is transport JSON, not a Python-mode UUID/date instance.
        config = parse_provider_run(canonical_bytes(run.config))
        if (
            (run.purpose, run.engine) != ("strategy_search", "openevolve")
            or run.strategy_version_id is not None
            or (run.owner_id, run.id) != (config.search.owner_id, config.search.run_id)
            or run.dataset_version_id != config.fixed_inputs.dataset_version_id
            or run.generation != 1
            or run.request_sha256 != digest(config)
        ):
            fail()
        return config
    except (TypeError, ValueError, AttributeError):
        fail()


def decode_spec(session, run):
    config = require_run_identity(run)
    if (
        config.fixed_inputs.schema_version
        == "mineagent.oe-structured-retained-inputs.v2"
    ):
        from backend.app.declared_upload_services import (
            require_revision,
            verify_basket_relations,
        )
        from backend.app.models import DatasetVersion

        require_revision(session)
        dataset = session.get(DatasetVersion, config.fixed_inputs.dataset_version_id)
        if dataset is None:
            fail()
        verify_basket_relations(session, run.owner_id, dataset)
    row = session.get(OEStructuredSpec, run.id)
    if row is None or row.owner_id != run.owner_id:
        fail()
    try:
        if type(config) is WorkflowStructuredRunConfig:
            from backend.app.oe_workflow_submission_schemas import (
                WorkflowStructuredSearchRequest,
            )
            from backend.app.research_workflow_structured_public import (
                require_structured_workflow_schema,
            )

            require_structured_workflow_schema(session)
            request = WorkflowStructuredSearchRequest.model_validate_json(
                row.request_canonical
            )
        else:
            request = ProviderStructuredSearchRequest.model_validate_json(
                row.request_canonical
            )
        request.require_binding(config.model_binding)
        version = session.get(StructuredStrategyVersion, row.seed_program_version_id)
        if version is None or version.owner_id != run.owner_id:
            fail()
        seed = StructuredProgram.model_validate_json(version.program_canonical)
        if (
            config.search.execution_binding_id != row.run_id
            or row.config != config.model_dump(mode="json")
            or row.config_canonical != canonical_bytes(config).decode("ascii")
            or row.config_sha256 != digest(config)
            or row.request != request.model_dump(mode="json")
            or row.request_canonical != canonical_bytes(request).decode("ascii")
            or row.request_sha256 != request.sha256
            or request.sha256 != submission_request_sha256(config)
            or row.snapshot_id != request.snapshot_id
            or row.snapshot_id != config.fixed_inputs.snapshot_id
            or row.dataset_id != config.fixed_inputs.dataset_version_id
            or request.objective != config.search.objective
            or request.maximum_mutation_slots != config.search.maximum_mutation_slots
            or request.budget != config.budget
            or seed != config.search.domain.seed_program
            or seed != request.seed_parameters.program(seed.signal_window)
            or version.program != seed.model_dump(mode="json")
            or version.program_canonical != canonical_bytes(seed).decode("ascii")
            or version.program_sha256 != digest(seed)
            or version.parent_version_id is not None
        ):
            fail()
        if type(config) is WorkflowStructuredRunConfig:
            from backend.app.oe_workflow_metadata import require_attachment

            require_attachment(session, run, row, config)
        return row, config
    except (TypeError, ValueError, AttributeError):
        fail()


def decode_candidate(session, run, candidate):
    """Validate stored canonical program/version linkage; not provider provenance.

    Mutation authorization additionally needs fresh immutable response/SDK-stop/
    controller evidence from the live broker resolver. This function deliberately
    cannot register a candidate or launch it merely from caller-supplied bytes.
    """
    spec, config = decode_spec(session, run)
    try:
        if (
            (candidate.owner_id, candidate.run_id) != (run.owner_id, run.id)
            or candidate.generation != run.generation
            or not 0 <= candidate.ordinal <= config.search.maximum_mutation_slots
        ):
            fail()
        projected = project_candidate(
            candidate.candidate_canonical.encode("ascii"), config.search.domain
        )
        version = session.get(StructuredStrategyVersion, candidate.program_version_id)
        if version is None or version.owner_id != run.owner_id:
            fail()
        if (
            (candidate.candidate_sha256, candidate.program_sha256)
            != (projected.candidate_sha256, projected.program_sha256)
            or version.program_sha256 != projected.program_sha256
            or version.program_canonical.encode("ascii") != projected.program_bytes
            or version.program != projected.program.model_dump(mode="json")
        ):
            fail()
        if candidate.ordinal == 0 and (
            candidate.origin
            != (
                "workflow_seed"
                if type(config) is WorkflowStructuredRunConfig
                else "authenticated_user_seed"
            )
            or candidate.parent_candidate_id is not None
            or candidate.provider_exchange_id is not None
            or candidate.program_version_id != spec.seed_program_version_id
            or candidate.candidate_canonical.encode("ascii")
            != canonical_bytes(
                CandidateDocument(program=config.search.domain.seed_program)
            )
        ):
            fail()
        if candidate.ordinal > 0 and (
            candidate.origin != "model_proposal"
            or candidate.parent_candidate_id is None
            or candidate.parent_candidate_id == candidate.id
            or candidate.provider_exchange_id is None
        ):
            fail()
        if candidate.ordinal > 0:
            parent = session.get(OEStructuredCandidate, candidate.parent_candidate_id)
            exchange = session.get(
                OEStructuredProviderExchange, candidate.provider_exchange_id
            )
            scope = (
                candidate.owner_id,
                candidate.run_id,
                candidate.attempt_id,
                candidate.generation,
            )
            if (
                parent is None
                or exchange is None
                or any(
                    tuple(
                        getattr(row, name)
                        for name in ("owner_id", "run_id", "attempt_id", "generation")
                    )
                    != scope
                    for row in (parent, exchange)
                )
                or (
                    not 0 <= parent.ordinal < candidate.ordinal
                    or parent.epoch_id != candidate.epoch_id
                    or exchange.epoch_id != candidate.epoch_id
                    or exchange.parent_candidate_id != parent.id
                    or version.parent_version_id != parent.program_version_id
                )
            ):
                fail()
        return projected
    except (TypeError, ValueError, AttributeError, UnicodeError):
        fail()

"""Flush-only search spec persistence; current legacy-only DB cannot admit it.

These helpers do not create runs, spawn code, widen neutral fencing or commit.
The activation migration/dispatcher/closure must be accepted before use.
"""

import hashlib

from sqlalchemy import select

from backend.app.models import DatasetVersion, ResearchRun, StrategyVersion
from backend.app.openevolve_models import StrategySearchSpec
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.services import DomainError
from packages.openevolve_adapter.contracts import SearchSpec
from packages.research_runtime.broker_contracts import digest


def _current_search(session, fence):
    require_neutral_transaction(session, fence)
    run = session.get(ResearchRun, fence.run_id)
    if (
        run is None
        or run.owner_id != fence.owner_id
        or (run.purpose, run.engine) != ("strategy_search", "openevolve")
    ):
        raise DomainError("search_identity", "A fenced OpenEvolve run is required", 409)
    return run


def bind_search_spec(session, fence, spec: SearchSpec):
    """No production route calls this until engine-specific lifecycle activation."""
    run = _current_search(session, fence)
    if type(spec) is not SearchSpec or (spec.owner_id, spec.run_id) != (
        fence.owner_id,
        fence.run_id,
    ):
        raise DomainError("search_identity", "Search specification scope differs", 409)
    if run.config != spec.model_dump(mode="json") or digest(run.config) != spec.sha256:
        raise DomainError(
            "search_identity",
            "Run config differs from immutable search specification",
            409,
        )
    if (
        run.strategy_version_id != spec.preparation.prepared.strategy_version_id
        or run.dataset_version_id != spec.execution.dataset_version_id
    ):
        raise DomainError("search_identity", "Search input identity differs", 409)
    for source in (spec.preparation.original, spec.preparation.prepared):
        row = session.get(StrategyVersion, source.strategy_version_id)
        if (
            row is None
            or row.owner_id != fence.owner_id
            or row.content_sha256 != source.source_sha256
            or hashlib.sha256(row.code.encode("utf-8")).hexdigest()
            != source.source_sha256
            or digest(row.manifest) != source.manifest_sha256
        ):
            raise DomainError("search_identity", "Search source identity differs", 409)
    dataset = session.get(DatasetVersion, spec.execution.dataset_version_id)
    if (
        dataset is None
        or dataset.owner_id != fence.owner_id
        or dataset.content_sha256 != spec.execution.dataset_sha256
    ):
        raise DomainError("search_identity", "Search dataset identity differs", 409)
    existing = session.scalar(
        select(StrategySearchSpec).where(StrategySearchSpec.run_id == fence.run_id)
    )
    if existing is not None:
        if (
            existing.owner_id != fence.owner_id
            or existing.spec_sha256 != spec.sha256
            or existing.spec != spec.model_dump(mode="json")
        ):
            raise DomainError(
                "search_identity", "Immutable search specification drift", 409
            )
        return existing
    row = StrategySearchSpec(
        owner_id=fence.owner_id,
        run_id=fence.run_id,
        spec_sha256=spec.sha256,
        spec=spec.model_dump(mode="json"),
        original_strategy_id=spec.preparation.original.strategy_version_id,
        original_source_sha256=spec.preparation.original.source_sha256,
        prepared_strategy_id=spec.preparation.prepared.strategy_version_id,
        prepared_source_sha256=spec.preparation.prepared.source_sha256,
        dataset_id=spec.execution.dataset_version_id,
        dataset_sha256=spec.execution.dataset_sha256,
        objective_sha256=digest(spec.objective),
        execution_sha256=digest(spec.execution),
    )
    session.add(row)
    session.flush()
    return row


# Draft event persistence. These functions are not registered as RPC handlers.
# They never launch, commit, rollback, publish files or widen neutral authority.
def _scope(fence):
    return {
        name: getattr(fence, name)
        for name in ("owner_id", "run_id", "attempt_id", "generation")
    }


def _owned_row(session, model, identity, fence):
    row = session.get(model, identity)
    if row is None or any(
        getattr(row, key) != value for key, value in _scope(fence).items()
    ):
        raise DomainError("search_identity", "Search evidence scope differs", 409)
    return row


def _locked_epoch(session, fence, epoch_id):
    from backend.app.openevolve_models import SearchEpochAbort, StrategySearchEpoch

    _current_search(session, fence)
    epoch = session.scalar(
        select(StrategySearchEpoch)
        .where(StrategySearchEpoch.id == epoch_id)
        .with_for_update()
    )
    if (
        epoch is None
        or any(getattr(epoch, key) != value for key, value in _scope(fence).items())
        or epoch.ordinal != 1
        or epoch.reason != "initial"
    ):
        raise DomainError(
            "search_epoch", "Only the fenced initial search epoch is supported", 409
        )
    if (
        session.scalar(
            select(SearchEpochAbort).where(SearchEpochAbort.epoch_id == epoch_id)
        )
        is not None
    ):
        raise DomainError("search_epoch_aborted", "Search population is untrusted", 409)
    from backend.app.openevolve_models import SearchResolvedExecutionProfile
    from packages.openevolve_adapter.execution_inputs import ResolvedNativeInputs
    from packages.openevolve_adapter.observer_transport import canonical

    spec_row = session.get(StrategySearchSpec, fence.run_id)
    profile_row = session.get(SearchResolvedExecutionProfile, fence.run_id)
    if (
        spec_row is None
        or spec_row.spec_sha256 != epoch.spec_sha256
        or profile_row is None
        or profile_row.owner_id != fence.owner_id
    ):
        raise DomainError(
            "search_profile", "Immutable resolved execution profile is required", 409
        )
    spec = SearchSpec.model_validate_json(canonical(spec_row.spec))
    raw_profile = profile_row.profile_canonical.encode("utf-8")
    profile = ResolvedNativeInputs.model_validate_json(raw_profile)
    if (
        canonical(profile.model_dump(mode="json")) != raw_profile
        or profile.model_dump(mode="json") != profile_row.profile
    ):
        raise DomainError(
            "search_profile", "Canonical native profile bytes differ", 409
        )
    profile.validate_plan(spec.execution)
    if profile_row.execution_sha256 != digest(
        spec.execution
    ) or profile_row.profile_sha256 != digest(profile):
        raise DomainError(
            "search_profile", "Resolved execution profile digest differs", 409
        )
    if spec.preparation.kind != "none" or spec.factors.kind != "none":
        raise DomainError(
            "search_capability",
            "Parity/factor activation is not implemented in this initial host draft",
            409,
        )
    from packages.openevolve_adapter.runtime_profile import PROFILE

    if epoch.runtime_profile_sha256 != PROFILE.sha256:
        raise DomainError(
            "search_profile",
            "Epoch runtime limits differ from fixed initial profile",
            409,
        )
    from backend.app.research_process_models import ResearchProcessAdmission

    unit = _owned_row(session, ResearchProcessAdmission, epoch.admission_id, fence)
    if (
        unit.role != "openevolve_orchestrator"
        or unit.image_id != spec.runtime_image
        or unit.input_sha256 != epoch.bootstrap_sha256
    ):
        raise DomainError(
            "search_process", "Epoch unit image/bootstrap identity differs", 409
        )
    return epoch


def _read_evidence(session, fence, reference, kind, settings):
    import json

    from backend.app.research_object_services import read_research_object
    from packages.openevolve_adapter.observer_transport import unique

    raw = read_research_object(
        session, fence, reference.id, kind, reference.sha256, settings
    )
    return json.loads(
        raw,
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(
            ValueError("Nonfinite evidence")
        ),
    )


def register_candidate_intent(session, fence, command, settings):
    from uuid import uuid4

    from backend.app.openevolve_models import (
        SearchCandidate,
        SearchCandidateGrant,
        SearchIterationIntent,
    )
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
        ResearchProcessState,
    )
    from packages.openevolve_adapter.host_contracts import (
        CandidateIntentCommand,
        EvidenceRef,
        SourceEnvelope,
    )
    from packages.openevolve_adapter.observer_transport import canonical
    from packages.openevolve_adapter.source import region, validate_descendant

    command = CandidateIntentCommand.model_validate(command.model_dump(mode="python"))
    epoch = _locked_epoch(session, fence, command.epoch_id)
    if command.admission_id != epoch.admission_id:
        raise DomainError(
            "search_process",
            "Candidate event belongs to another orchestrator unit",
            409,
        )
    existing = session.scalar(
        select(SearchCandidate).where(
            SearchCandidate.epoch_id == command.epoch_id,
            SearchCandidate.upstream_program_id == command.upstream_program_id,
        )
    )
    if existing is not None:
        grant = session.scalar(
            select(SearchCandidateGrant).where(
                SearchCandidateGrant.candidate_id == existing.id
            )
        )
        if (
            grant is None
            or grant.request_sha256 != command.request_sha256
            or grant.ticket_sha256 != command.ticket_sha256
            or existing.source_object_sha256 != command.source.sha256
            or existing.kind != command.phase
            or existing.source_object_id != command.source.id
            or grant.intent_id != command.intent_id
            or grant.admission_id != command.admission_id
        ):
            raise DomainError(
                "search_replay", "Conflicting candidate intent replay", 409
            )
        return grant
    admission = _owned_row(
        session, ResearchProcessAdmission, command.admission_id, fence
    )
    process_state = session.get(ResearchProcessState, admission.id)
    if (
        process_state is None
        or process_state.status != "running"
        or admission.role != "openevolve_orchestrator"
    ):
        raise DomainError(
            "search_process", "Running typed orchestrator admission required", 409
        )
    source = SourceEnvelope.model_validate_json(
        canonical(
            _read_evidence(session, fence, command.source, "search_source", settings)
        )
    )
    spec_row = session.get(StrategySearchSpec, fence.run_id)
    spec = SearchSpec.model_validate_json(canonical(spec_row.spec))
    parent = None
    proposal = None
    if command.phase == "seed":
        if source.source_sha256 != spec.preparation.prepared.source_sha256:
            raise DomainError(
                "search_source", "Seed differs from admitted prepared source", 409
            )
        region(source.source.encode())
        if (
            session.scalar(
                select(SearchCandidate).where(
                    SearchCandidate.epoch_id == command.epoch_id,
                    SearchCandidate.kind == "seed",
                )
            )
            is not None
        ):
            raise DomainError("search_seed", "Initial epoch already has a seed", 409)
    else:
        intent = _owned_row(session, SearchIterationIntent, command.intent_id, fence)
        if intent.epoch_id != command.epoch_id:
            raise DomainError("search_identity", "Mutation intent epoch differs", 409)
        from backend.app.openevolve_models import (
            SearchControlTransition,
            SearchProposalSource,
        )

        sampling = session.scalar(
            select(SearchControlTransition).where(
                SearchControlTransition.sampling_intent_id == intent.id
            )
        )
        if (
            sampling is None
            or sampling.epoch_id != command.epoch_id
            or sampling.kind != "sampling"
        ):
            raise DomainError(
                "search_cursor",
                "Mutation intent lacks acknowledged sampling receipt",
                409,
            )
        proposal = session.scalar(
            select(SearchProposalSource).where(
                SearchProposalSource.intent_id == intent.id,
                SearchProposalSource.stage == "enforced",
            )
        )
        if (
            proposal is None
            or proposal.evidence != "complete_source"
            or proposal.source_sha256 != source.source_sha256
        ):
            raise DomainError(
                "search_proposal",
                "Candidate lacks preserved exact enforced proposal source",
                409,
            )
        parent = _owned_row(session, SearchCandidate, intent.parent_candidate_id, fence)
        parent_source = SourceEnvelope.model_validate_json(
            canonical(
                _read_evidence(
                    session,
                    fence,
                    EvidenceRef(
                        id=parent.source_object_id, sha256=parent.source_object_sha256
                    ),
                    "search_source",
                    settings,
                )
            )
        )
        if (
            parent_source.source_sha256 != parent.source_sha256
            or parent.source_sha256 != intent.parent_source_sha256
        ):
            raise DomainError("search_source", "Mutation parent bytes differ", 409)
        validate_descendant(parent_source.source.encode(), source.source.encode())
    candidate = SearchCandidate(
        id=uuid4(),
        **_scope(fence),
        epoch_id=command.epoch_id,
        upstream_program_id=command.upstream_program_id,
        source_object_id=command.source.id,
        source_object_sha256=command.source.sha256,
        source_kind="search_source",
        source_sha256=source.source_sha256,
        kind=command.phase,
        seed_strategy_id=spec.preparation.prepared.strategy_version_id
        if parent is None
        else None,
        parent_candidate_id=parent.id if parent else None,
        parent_source_sha256=parent.source_sha256 if parent else None,
    )
    grant = SearchCandidateGrant(
        id=uuid4(),
        **_scope(fence),
        epoch_id=command.epoch_id,
        candidate_id=candidate.id,
        source_sha256=candidate.source_sha256,
        intent_id=command.intent_id,
        proposal_source_id=proposal.id if proposal else None,
        proposal_stage="enforced" if proposal else None,
        proposal_evidence="complete_source" if proposal else None,
        admission_id=admission.id,
        phase=command.phase,
        ticket_sha256=command.ticket_sha256,
        request_sha256=command.request_sha256,
    )
    session.add(candidate)
    session.add(grant)
    session.flush()
    return grant


def acknowledge_objective(session, fence, command):
    import math
    from uuid import uuid4

    from backend.app.openevolve_models import (
        SearchCandidateGrant,
        SearchObjectiveAcknowledgement,
        SearchObjectiveReceipt,
    )
    from packages.openevolve_adapter.host_contracts import ObjectiveAckCommand

    command = ObjectiveAckCommand.model_validate(command.model_dump(mode="python"))
    _locked_epoch(session, fence, command.epoch_id)
    grant = _owned_row(session, SearchCandidateGrant, command.grant_id, fence)
    receipt = _owned_row(
        session, SearchObjectiveReceipt, command.objective_receipt_id, fence
    )
    if grant.epoch_id != command.epoch_id or (
        receipt.epoch_id,
        receipt.candidate_id,
        receipt.source_sha256,
    ) != (grant.epoch_id, grant.candidate_id, grant.source_sha256):
        raise DomainError(
            "search_identity", "Objective acknowledgement scope differs", 409
        )
    eligible = receipt.status == "eligible"
    if (
        command.eligible != eligible
        or command.combined_score != receipt.combined_score
        or (
            eligible
            and (
                type(command.combined_score) not in (int, float)
                or not math.isfinite(command.combined_score)
            )
        )
        or (not eligible and command.combined_score is not None)
    ):
        raise DomainError(
            "search_objective", "Actual metrics differ from durable objective", 409
        )
    existing = session.scalar(
        select(SearchObjectiveAcknowledgement).where(
            SearchObjectiveAcknowledgement.objective_receipt_id == receipt.id
        )
    )
    if existing is not None:
        if (
            existing.request_sha256 != command.request_sha256
            or existing.grant_id != grant.id
        ):
            raise DomainError(
                "search_replay", "Conflicting objective acknowledgement", 409
            )
        return existing
    row = SearchObjectiveAcknowledgement(
        id=uuid4(),
        **_scope(fence),
        epoch_id=grant.epoch_id,
        candidate_id=grant.candidate_id,
        source_sha256=grant.source_sha256,
        objective_receipt_id=receipt.id,
        grant_id=grant.id,
        request_sha256=command.request_sha256,
    )
    session.add(row)
    session.flush()
    return row


def record_worker_outcome(session, fence, command, settings):
    from uuid import uuid4

    from backend.app.openevolve_models import (
        SearchCandidateGrant,
        SearchIterationIntent,
        SearchObjectiveAcknowledgement,
        SearchObjectiveReceipt,
        SearchWorkerOutcome,
    )
    from packages.openevolve_adapter.host_contracts import WorkerOutcomeCommand

    command = WorkerOutcomeCommand.model_validate(command.model_dump(mode="python"))
    epoch = _locked_epoch(session, fence, command.epoch_id)
    intent = _owned_row(session, SearchIterationIntent, command.intent_id, fence)
    if (intent.epoch_id, intent.native_iteration) != (
        command.epoch_id,
        command.native_iteration,
    ):
        raise DomainError("search_identity", "Worker intent differs", 409)
    evidence = _read_evidence(
        session, fence, command.evidence, "search_worker_outcome", settings
    )
    expected = command.model_dump(mode="json", exclude={"evidence"}) | {
        "schema_version": "mineagent.search-worker-outcome.v1"
    }
    from backend.app.research_process_models import ResearchProcessAdmission
    from packages.openevolve_adapter.observer_transport import canonical
    from runtimes.openevolve_operational.profile import PROFILE

    admission = session.get(ResearchProcessAdmission, epoch.admission_id)
    observed = None
    if admission is not None and admission.profile == PROFILE:
        from packages.openevolve_adapter.worker_outcome_evidence import validate

        validate(evidence)
        observed = evidence["observation"]
        comparison = evidence["projection"] | {
            "schema_version": "mineagent.search-worker-outcome.v1"
        }
    else:
        comparison = evidence
    if canonical(comparison) != canonical(expected):
        raise DomainError("search_evidence", "Worker outcome object differs", 409)
    ack = None
    if command.objective_ack_id is not None:
        ack = _owned_row(
            session, SearchObjectiveAcknowledgement, command.objective_ack_id, fence
        )
        receipt = _owned_row(
            session, SearchObjectiveReceipt, ack.objective_receipt_id, fence
        )
        grant = _owned_row(session, SearchCandidateGrant, ack.grant_id, fence)
        if (
            ack.epoch_id != command.epoch_id
            or grant.intent_id != intent.id
            or (receipt.status == "eligible") != (command.status == "evaluated")
        ):
            raise DomainError(
                "search_objective",
                "Worker result lacks matching objective acknowledgement",
                409,
            )
    if observed is not None and command.status == "evaluated":
        from backend.app.openevolve_models import SearchCandidate
        from packages.openevolve_adapter.worker_outcome_evidence import source_sha256

        candidate = _owned_row(session, SearchCandidate, ack.candidate_id, fence)
        parent = _owned_row(session, SearchCandidate, intent.parent_candidate_id, fence)
        child = observed["child"]
        if (
            observed["parent_id"] != str(parent.upstream_program_id)
            or observed["target_island"] != intent.target_island
            or child["id"] != str(candidate.upstream_program_id)
            or source_sha256(evidence) != candidate.source_sha256
            or child["metrics"] != {"combined_score": receipt.combined_score}
            or child["generation"] != (1 if parent.kind == "seed" else 2)
            or candidate.parent_candidate_id != parent.id
        ):
            raise DomainError("search_evidence", "Actual returned child differs", 409)
    existing = session.scalar(
        select(SearchWorkerOutcome).where(SearchWorkerOutcome.intent_id == intent.id)
    )
    if existing is not None:
        if existing.evidence_sha256 != command.evidence.sha256:
            raise DomainError("search_replay", "Conflicting worker outcome", 409)
        return existing
    row = SearchWorkerOutcome(
        id=uuid4(),
        **_scope(fence),
        epoch_id=command.epoch_id,
        intent_id=intent.id,
        native_iteration=command.native_iteration,
        status=command.status,
        candidate_id=ack.candidate_id if ack else None,
        source_sha256=ack.source_sha256 if ack else None,
        objective_ack_id=ack.id if ack else None,
        evidence_object_id=command.evidence.id,
        evidence_kind="search_worker_outcome",
        evidence_sha256=command.evidence.sha256,
    )
    session.add(row)
    session.flush()
    return row


def _validated_population(session, fence, epoch_id, frontier, reference, settings):
    from backend.app.openevolve_models import (
        SearchCandidate,
        SearchObjectiveAcknowledgement,
        SearchObjectiveReceipt,
    )
    from packages.openevolve_adapter.checkpoint_native import _normalize, state_sha256
    from packages.openevolve_adapter.runtime_profile import validate_source_bound

    state = _read_evidence(session, fence, reference, "search_population", settings)
    if type(state) is not dict or set(state) != {"metadata", "programs"}:
        raise DomainError("search_state", "Native state schema differs", 409)
    normalized = _normalize(state["metadata"], state["programs"])
    if (
        state_sha256(normalized) != reference.sha256
        or normalized["metadata"]["last_iteration"] != frontier
    ):
        raise DomainError("search_state", "Native frontier projection differs", 409)
    for program_id, program in normalized["programs"].items():
        from uuid import UUID

        source = program["code"].encode()
        validate_source_bound(source)
        candidate = session.scalar(
            select(SearchCandidate).where(
                SearchCandidate.epoch_id == epoch_id,
                SearchCandidate.upstream_program_id == UUID(program_id),
            )
        )
        if (
            candidate is None
            or any(
                getattr(candidate, key) != value for key, value in _scope(fence).items()
            )
            or candidate.source_sha256 != hashlib.sha256(source).hexdigest()
        ):
            raise DomainError(
                "search_state", "Native population includes unregistered source", 409
            )
        from backend.app.openevolve_models import (
            SearchCandidateGrant,
            SearchIterationIntent,
        )

        if candidate.kind == "seed":
            expected_iteration, expected_parent, expected_generation = 0, None, 0
        else:
            grant = session.scalar(
                select(SearchCandidateGrant).where(
                    SearchCandidateGrant.candidate_id == candidate.id
                )
            )
            if grant is None or grant.intent_id is None:
                raise DomainError(
                    "search_state",
                    "Population candidate has no originating intent",
                    409,
                )
            intent = _owned_row(session, SearchIterationIntent, grant.intent_id, fence)
            parent = _owned_row(
                session, SearchCandidate, candidate.parent_candidate_id, fence
            )
            expected_iteration, expected_parent = (
                intent.native_iteration,
                str(parent.upstream_program_id),
            )
            # Initial profile permits only two mutations; ancestry remains exact.
            expected_generation = 1 if parent.kind == "seed" else 2
            if (
                intent.epoch_id != epoch_id
                or intent.parent_candidate_id != parent.id
                or intent.parent_source_sha256 != parent.source_sha256
            ):
                raise DomainError(
                    "search_state", "Population ancestry intent differs", 409
                )
        if (
            program["iteration_found"],
            program["parent_id"],
            program["generation"],
        ) != (
            expected_iteration,
            expected_parent,
            expected_generation,
        ) or expected_iteration > frontier:
            raise DomainError(
                "search_state",
                "Population ancestry/iteration differs from admitted candidate",
                409,
            )
        ack = session.scalar(
            select(SearchObjectiveAcknowledgement).where(
                SearchObjectiveAcknowledgement.candidate_id == candidate.id
            )
        )
        if ack is None:
            raise DomainError(
                "search_state", "Native population lacks objective acknowledgement", 409
            )
        receipt = _owned_row(
            session, SearchObjectiveReceipt, ack.objective_receipt_id, fence
        )
        if (
            receipt.status != "eligible"
            or program["metrics"] != {"combined_score": receipt.combined_score}
            or type(program["metrics"].get("combined_score")) not in (int, float)
        ):
            raise DomainError(
                "search_state", "Native population contains unverified score", 409
            )
        import json

        from packages.openevolve_adapter.observer_transport import canonical, unique

        if (
            receipt.feedback is None
            or hashlib.sha256(receipt.feedback.encode()).hexdigest()
            != receipt.feedback_sha256
            or program["artifacts_json"] is None
        ):
            raise DomainError(
                "search_state", "Native program lacks exact host-issued feedback", 409
            )
        artifacts = json.loads(program["artifacts_json"], object_pairs_hook=unique)
        if canonical(artifacts) != canonical({"native_feedback": receipt.feedback}):
            raise DomainError(
                "search_state",
                "Native program feedback differs from objective receipt",
                409,
            )
    return normalized


def acknowledge_terminal_application(session, fence, command, settings):
    from uuid import uuid4

    from backend.app.openevolve_models import (
        SearchCandidate,
        SearchObjectiveAcknowledgement,
        SearchObjectiveReceipt,
        SearchTerminalApplication,
        SearchWorkerOutcome,
    )
    from packages.openevolve_adapter.host_contracts import TerminalCommand

    command = TerminalCommand.model_validate(command.model_dump(mode="python"))
    _locked_epoch(session, fence, command.epoch_id)
    existing = session.scalar(
        select(SearchTerminalApplication).where(
            SearchTerminalApplication.epoch_id == command.epoch_id,
            SearchTerminalApplication.frontier == command.frontier,
        )
    )
    if existing is not None:
        if (
            existing.state_sha256 != command.state.sha256
            or existing.outcome != command.outcome
            or existing.worker_outcome_id != command.worker_outcome_id
            or existing.seed_objective_ack_id != command.seed_objective_ack_id
        ):
            raise DomainError(
                "search_replay", "Conflicting native terminal boundary", 409
            )
        return existing
    previous = session.scalar(
        select(SearchTerminalApplication)
        .where(SearchTerminalApplication.epoch_id == command.epoch_id)
        .order_by(SearchTerminalApplication.frontier.desc())
        .limit(1)
    )
    if command.frontier != (previous.frontier + 1 if previous else 0):
        raise DomainError(
            "search_frontier", "Noncontiguous native terminal boundary", 409
        )
    seed = None
    if command.frontier == 0:
        ack = _owned_row(
            session,
            SearchObjectiveAcknowledgement,
            command.seed_objective_ack_id,
            fence,
        )
        seed = _owned_row(session, SearchCandidate, ack.candidate_id, fence)
        objective = _owned_row(
            session, SearchObjectiveReceipt, ack.objective_receipt_id, fence
        )
        if (
            ack.epoch_id != command.epoch_id
            or seed.kind != "seed"
            or objective.status != "eligible"
        ):
            raise DomainError(
                "search_seed", "Seed terminal lacks eligible exact objective", 409
            )
    else:
        worker = _owned_row(
            session, SearchWorkerOutcome, command.worker_outcome_id, fence
        )
        if (worker.epoch_id, worker.native_iteration) != (
            command.epoch_id,
            command.frontier,
        ) or (worker.status == "worker_error") != (command.outcome == "error"):
            raise DomainError(
                "search_outcome", "Native terminal differs from worker outcome", 409
            )
    control = (
        None
        if previous is None
        else _control_baseline(session, command.epoch_id, previous)
    )
    state = _validated_population(
        session, fence, command.epoch_id, command.frontier, command.state, settings
    )
    if command.frontier == 0 and set(state["programs"]) != {
        str(seed.upstream_program_id)
    }:
        raise DomainError(
            "search_seed", "Seed population contains unrelated candidates", 409
        )
    if command.frontier > 0:
        import copy

        from packages.openevolve_adapter.host_contracts import EvidenceRef
        from packages.openevolve_adapter.observer_transport import canonical

        before = _validated_population(
            session,
            fence,
            command.epoch_id,
            control.frontier,
            EvidenceRef(id=control.state_object_id, sha256=control.state_sha256),
            settings,
        )
        if command.outcome in {"error", "rejected"}:
            unchanged = copy.deepcopy(before)
            unchanged["metadata"]["last_iteration"] = command.frontier
            if canonical(state) != canonical(unchanged):
                raise DomainError(
                    "search_state",
                    "Failed/rejected work changed native population state",
                    409,
                )
        else:
            if worker.candidate_id is None:
                raise DomainError(
                    "search_state",
                    "Accepted terminal lacks current worker candidate",
                    409,
                )
            admitted = _owned_row(session, SearchCandidate, worker.candidate_id, fence)
            new_ids = set(state["programs"]) - set(before["programs"])
            if not new_ids <= {str(admitted.upstream_program_id)}:
                raise DomainError(
                    "search_state",
                    "Accepted terminal inserted another intent's candidate",
                    409,
                )
            for identity in set(state["programs"]) & set(before["programs"]):
                if canonical(state["programs"][identity]) != canonical(
                    before["programs"][identity]
                ):
                    raise DomainError(
                        "search_state",
                        "Accepted application mutated an existing native program",
                        409,
                    )
    row = SearchTerminalApplication(
        id=uuid4(),
        **_scope(fence),
        epoch_id=command.epoch_id,
        frontier=command.frontier,
        outcome=command.outcome,
        previous_id=previous.id if previous else None,
        previous_frontier=previous.frontier if previous else None,
        worker_outcome_id=command.worker_outcome_id,
        seed_candidate_id=seed.id if seed else None,
        seed_source_sha256=seed.source_sha256 if seed else None,
        seed_objective_ack_id=command.seed_objective_ack_id,
        state_object_id=command.state.id,
        state_kind="search_population",
        state_sha256=command.state.sha256,
    )
    from backend.app.openevolve_models import SearchControlTransition

    session.add(row)
    session.add(
        SearchControlTransition(
            id=uuid4(),
            **_scope(fence),
            epoch_id=command.epoch_id,
            sequence=control.sequence + 1 if control else 1,
            previous_id=control.id if control else None,
            previous_sequence=control.sequence if control else None,
            kind="terminal" if control else "seed",
            frontier=command.frontier,
            terminal_id=row.id,
            sampling_intent_id=None,
            state_object_id=command.state.id,
            state_kind="search_population",
            state_sha256=command.state.sha256,
            request_sha256=digest(command),
        )
    )
    session.flush()
    return row


def prepare_checkpoint(session, fence, command):
    from uuid import uuid4

    from backend.app.openevolve_models import (
        SearchCheckpointIntent,
        SearchTerminalApplication,
    )
    from packages.openevolve_adapter.host_contracts import CheckpointIntentCommand

    command = CheckpointIntentCommand.model_validate(command.model_dump(mode="python"))
    _locked_epoch(session, fence, command.epoch_id)
    existing = session.scalar(
        select(SearchCheckpointIntent).where(
            SearchCheckpointIntent.epoch_id == command.epoch_id,
            SearchCheckpointIntent.request_sha256 == command.request_sha256,
        )
    )
    if existing is not None:
        if (
            existing.terminal_id,
            existing.frontier,
            existing.state_sha256,
            existing.save_ordinal,
        ) != (
            command.terminal_id,
            command.frontier,
            command.state_sha256,
            command.save_ordinal,
        ):
            raise DomainError("search_replay", "Checkpoint request conflicts", 409)
        return existing
    if command.save_ordinal is not None:
        prior = session.scalars(
            select(SearchCheckpointIntent)
            .where(SearchCheckpointIntent.epoch_id == command.epoch_id)
            .order_by(SearchCheckpointIntent.save_ordinal)
        ).all()
        if [row.save_ordinal for row in prior] != list(range(1, command.save_ordinal)):
            raise DomainError("search_replay", "Checkpoint save ordinal conflicts", 409)
    terminal = _owned_row(
        session, SearchTerminalApplication, command.terminal_id, fence
    )
    latest = session.scalar(
        select(SearchTerminalApplication)
        .where(SearchTerminalApplication.epoch_id == command.epoch_id)
        .order_by(SearchTerminalApplication.frontier.desc())
        .limit(1)
    )
    control = _latest_control(session, command.epoch_id)
    if (
        latest is None
        or latest.id != terminal.id
        or control is None
        or (terminal.epoch_id, terminal.frontier, control.state_sha256)
        != (command.epoch_id, command.frontier, command.state_sha256)
    ):
        raise DomainError(
            "search_checkpoint", "Checkpoint is not current acknowledged frontier", 409
        )
    row = SearchCheckpointIntent(
        id=uuid4(),
        **_scope(fence),
        epoch_id=command.epoch_id,
        save_ordinal=command.save_ordinal,
        frontier=command.frontier,
        terminal_id=terminal.id,
        control_transition_id=control.id,
        state_sha256=command.state_sha256,
        request_sha256=command.request_sha256,
    )
    session.add(row)
    session.flush()
    return row


def publish_checkpoint_snapshot(session, fence, command, settings):
    return _checkpoint_snapshot(
        session, fence, command, settings, require_existing=False
    )


def revalidate_current_checkpoint(session, fence, command, settings):
    """Fresh-read current already-published complete graph/files; never appends."""
    return _checkpoint_snapshot(
        session, fence, command, settings, require_existing=True
    )


def _checkpoint_snapshot(session, fence, command, settings, *, require_existing):
    """Append a stable snapshot, never a resumable pointer/positive stop claim."""
    from uuid import uuid4

    from backend.app import openevolve_models as m
    from packages.openevolve_adapter.checkpoint_evidence import (
        validate_checkpoint_evidence,
    )
    from packages.openevolve_adapter.host_contracts import (
        CheckpointInventory,
        CheckpointPublicationCommand,
        InitialCheckpointClosure,
    )
    from packages.openevolve_adapter.observer_transport import canonical

    command = CheckpointPublicationCommand.model_validate_json(
        command.model_dump_json()
    )
    epoch = _locked_epoch(session, fence, command.epoch_id)
    intent = _owned_row(session, m.SearchCheckpointIntent, command.intent_id, fence)
    if intent.epoch_id != epoch.id:
        raise DomainError("search_checkpoint", "Snapshot intent epoch differs", 409)
    existing = session.scalar(
        select(m.SearchCheckpointPublication).where(
            m.SearchCheckpointPublication.intent_id == intent.id
        )
    )
    if require_existing and existing is None:
        raise DomainError(
            "search_checkpoint", "No published snapshot to revalidate", 409
        )
    if existing is not None:
        if (
            existing.inventory_object_id != command.inventory.id
            or existing.closure_object_id != command.closure.id
            or existing.inventory_sha256 != command.inventory.sha256
            or existing.closure_sha256 != command.closure.sha256
        ):
            raise DomainError(
                "search_replay", "Conflicting checkpoint publication", 409
            )
        _owned_row(session, m.SearchCheckpointPublication, existing.id, fence)
        if not require_existing:
            return existing
    latest = session.scalar(
        select(m.SearchTerminalApplication)
        .where(m.SearchTerminalApplication.epoch_id == epoch.id)
        .order_by(m.SearchTerminalApplication.frontier.desc())
        .limit(1)
    )
    if intent.epoch_id != epoch.id or latest is None or latest.id != intent.terminal_id:
        raise DomainError(
            "search_checkpoint",
            "Snapshot no longer matches current acknowledged state",
            409,
        )
    control = _latest_control(session, epoch.id)
    if control is None or control.id != intent.control_transition_id:
        raise DomainError(
            "search_checkpoint",
            "Native control cursor changed during checkpoint serialization",
            409,
        )
    inventory = CheckpointInventory.model_validate_json(
        canonical(
            _read_evidence(
                session,
                fence,
                command.inventory,
                "search_checkpoint_inventory",
                settings,
            )
        )
    )
    closure = InitialCheckpointClosure.model_validate_json(
        canonical(
            _read_evidence(
                session, fence, command.closure, "search_checkpoint_closure", settings
            )
        )
    )
    if (
        closure.epoch_id,
        closure.frontier,
        closure.spec_sha256,
        closure.state_sha256,
    ) != (epoch.id, intent.frontier, epoch.spec_sha256, intent.state_sha256):
        raise DomainError(
            "search_checkpoint", "Checkpoint closure identity differs", 409
        )
    if closure.control_sequence != control.sequence:
        raise DomainError(
            "search_checkpoint", "Checkpoint control sequence differs", 409
        )
    from backend.app.openevolve_event_models import SearchControllerEventReceipt
    from backend.app.openevolve_worker_models import (
        SearchWorkerAssignment,
        SearchWorkerAssignmentClosure,
        SearchWorkerExpectedIdentity,
        SearchWorkerTicket,
    )
    from packages.openevolve_adapter.worker_checkpoint_closure import (
        validate_worker_closure,
    )

    tables = {
        "controller_event_receipt_ids": SearchControllerEventReceipt,
        "worker_ticket_ids": SearchWorkerTicket,
        "worker_expected_identity_ids": SearchWorkerExpectedIdentity,
        "worker_assignment_ids": SearchWorkerAssignment,
        "worker_assignment_closure_ids": SearchWorkerAssignmentClosure,
        "terminal_ids": m.SearchTerminalApplication,
        "control_transition_ids": m.SearchControlTransition,
        "candidate_ids": m.SearchCandidate,
        "objective_receipt_ids": m.SearchObjectiveReceipt,
        "objective_ack_ids": m.SearchObjectiveAcknowledgement,
        "intent_ids": m.SearchIterationIntent,
        "worker_outcome_ids": m.SearchWorkerOutcome,
        "rendered_context_ids": m.SearchRenderedContext,
        "proposal_response_ids": m.SearchProposalResponse,
        "proposal_source_ids": m.SearchProposalSource,
        "evaluation_ids": m.SearchEvaluation,
    }
    rows = {}
    for name, model in tables.items():
        rows[name] = list(
            session.scalars(select(model).where(model.epoch_id == epoch.id)).all()
        )
        if any(
            any(getattr(row, key) != value for key, value in _scope(fence).items())
            for row in rows[name]
        ) or set(getattr(closure, name)) != {row.id for row in rows[name]}:
            raise DomainError(
                "search_checkpoint",
                "Checkpoint omits or substitutes durable epoch evidence",
                409,
            )
    _validate_control_chain(
        rows["control_transition_ids"],
        rows["intent_ids"],
        rows["terminal_ids"],
        control,
    )
    validate_worker_closure(
        epoch,
        rows,
        set(closure.pending_intent_ids),
        set(closure.open_worker_assignment_ids),
    )
    from backend.app.openevolve_checkpoint_events import (
        validate_checkpoint_event_objects,
    )

    validate_checkpoint_event_objects(session, fence, epoch, rows, settings)
    if set(closure.sampling_transition_ids) != {
        row.id for row in rows["control_transition_ids"] if row.kind == "sampling"
    }:
        raise DomainError(
            "search_checkpoint", "Checkpoint sampling receipt closure differs", 409
        )
    if set(closure.pending_intent_ids) != _pending_intent_ids(
        rows["intent_ids"], rows["worker_outcome_ids"], rows["terminal_ids"]
    ):
        raise DomainError("search_checkpoint", "Prefetched intent tail differs", 409)
    outcomes = list(
        session.scalars(
            select(m.SearchEvaluationOutcome).where(
                m.SearchEvaluationOutcome.evaluation_id.in_(closure.evaluation_ids)
            )
        ).all()
    )
    if set(closure.evaluation_outcome_ids) != {row.id for row in outcomes}:
        raise DomainError(
            "search_checkpoint", "Physical evaluation outcomes differ", 409
        )
    from backend.app.research_object_services import read_research_object

    files = {
        item.name: read_research_object(
            session,
            fence,
            item.object.id,
            "search_checkpoint_file",
            item.object.sha256,
            settings,
        )
        for item in inventory.files
    }
    validate_checkpoint_evidence(
        inventory, files, expected_state_sha256=intent.state_sha256
    )
    if require_existing:
        return existing
    row = m.SearchCheckpointPublication(
        id=uuid4(),
        **_scope(fence),
        epoch_id=epoch.id,
        intent_id=intent.id,
        frontier=intent.frontier,
        state_sha256=intent.state_sha256,
        inventory_object_id=command.inventory.id,
        inventory_kind="search_checkpoint_inventory",
        inventory_sha256=command.inventory.sha256,
        closure_object_id=command.closure.id,
        closure_kind="search_checkpoint_closure",
        closure_sha256=command.closure.sha256,
    )
    session.add(row)
    for item in inventory.files:
        session.add(
            m.SearchCheckpointFile(
                id=uuid4(),
                **_scope(fence),
                publication_id=row.id,
                name=item.name,
                file_sha256=item.file_sha256,
                size_bytes=item.size_bytes,
                object_id=item.object.id,
                object_kind="search_checkpoint_file",
                object_sha256=item.object.sha256,
            )
        )
    session.flush()
    return row


def record_physical_outcome(session, fence, command, settings):
    from uuid import uuid4

    from backend.app.openevolve_models import SearchEvaluation, SearchEvaluationOutcome
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
        ResearchProcessReceipt,
        ResearchProcessState,
    )
    from backend.app.research_process_services import as_admission
    from packages.openevolve_adapter.execution_binding import (
        NativeEvaluationBinding,
        bound_native_observation,
    )
    from packages.openevolve_adapter.host_contracts import PhysicalOutcomeCommand
    from packages.tradegrad_adapter.results import canonical_json_bytes, sha256_bytes
    from runtimes.research_supervisor.supervisor import (
        operation_path,
        validate_stopped_journal,
    )

    command = PhysicalOutcomeCommand.model_validate(command.model_dump(mode="python"))
    _locked_epoch(session, fence, command.epoch_id)
    evaluation = _owned_row(session, SearchEvaluation, command.evaluation_id, fence)
    admission = _owned_row(
        session, ResearchProcessAdmission, evaluation.process_admission_id, fence
    )
    stop = _owned_row(session, ResearchProcessReceipt, command.stop_receipt_id, fence)
    process = session.get(ResearchProcessState, admission.id)
    if (
        evaluation.epoch_id != command.epoch_id
        or stop.admission_id != admission.id
        or stop.kind != "stop"
        or process is None
        or process.status != "stopped"
    ):
        raise DomainError(
            "search_stop", "Physical evaluator has no exact positive stop evidence", 409
        )
    from runtimes.openevolve_evaluator.contracts import (
        PROFILE as protected_native_profile,
    )

    protected_evidence = None
    if admission.profile == protected_native_profile:
        from backend.app.openevolve_native_evidence import read_native_evidence

        actual_stop_id, protected_evidence = read_native_evidence(
            session, fence, admission.id, settings
        )
        if actual_stop_id != stop.id:
            raise DomainError("search_stop", "Protected native stop differs", 409)
    else:
        registered = as_admission(admission)
        if (
            validate_stopped_journal(
                operation_path(settings.storage_root, registered), registered
            )
            != stop.payload
        ):
            raise DomainError(
                "search_stop", "Physical stop receipt differs from closed journal", 409
            )
        if (
            admission.role != "native_evaluator"
            or admission.input_sha256 != evaluation.input_manifest_sha256
        ):
            raise DomainError(
                "search_execution", "Physical native evaluator input differs", 409
            )
    from packages.openevolve_adapter.observer_transport import canonical

    spec = SearchSpec.model_validate_json(
        canonical(session.get(StrategySearchSpec, fence.run_id).spec)
    )
    if admission.image_id != spec.execution.evaluator_image:
        raise DomainError("search_execution", "Physical evaluator image differs", 409)
    kind = (
        "search_native_observation"
        if command.status == "observed"
        else "search_native_failure"
    )
    evidence = _read_evidence(session, fence, command.evidence, kind, settings)
    if protected_evidence is not None and canonical(evidence) != canonical(
        protected_evidence
    ):
        raise DomainError(
            "search_evidence",
            "Claimed result differs from actual protected native bytes",
            409,
        )
    bundle_hash = None
    if command.status == "observed":
        if (
            type(evidence) is not dict
            or set(evidence) != {"schema_version", "bundle"}
            or evidence["schema_version"] != "mineagent.native-observation-object.v1"
        ):
            raise DomainError(
                "search_evidence", "Native observation envelope differs", 409
            )
        bundle_hash = sha256_bytes(canonical_json_bytes(evidence["bundle"]))
        window = next(
            (w for w in spec.execution.windows if w.id == evaluation.window_id), None
        )
        if window is None:
            raise DomainError(
                "search_execution", "Physical evaluation window is not admitted", 409
            )
        bound_native_observation(
            evidence["bundle"],
            NativeEvaluationBinding(
                owner_id=fence.owner_id,
                run_id=fence.run_id,
                attempt_id=fence.attempt_id,
                generation=fence.generation,
                evaluation_id=evaluation.id,
                candidate_id=evaluation.candidate_id,
                strategy_version_id=None,
                phase=evaluation.phase,
                source_sha256=evaluation.source_sha256,
                input_manifest_sha256=evaluation.input_manifest_sha256,
                execution_plan_sha256=evaluation.execution_sha256,
                evaluator_image=admission.image_id,
                bundle_sha256=bundle_hash,
                window=window,
            ),
            spec,
        )
    elif (
        type(evidence) is not dict
        or set(evidence) != {"schema_version", "evaluation_id", "failure_code"}
        or evidence["schema_version"] != "mineagent.native-failure-object.v1"
        or evidence["evaluation_id"] != str(evaluation.id)
        or evidence["failure_code"] not in {"engine_error", "invalid_result", "timeout"}
    ):
        raise DomainError("search_evidence", "Native failure envelope differs", 409)
    existing = session.scalar(
        select(SearchEvaluationOutcome).where(
            SearchEvaluationOutcome.evaluation_id == evaluation.id
        )
    )
    if existing is not None:
        if (
            existing.bundle_sha256 != command.evidence.sha256
            or existing.stop_receipt_id != stop.id
            or existing.request_sha256 != command.request_sha256
        ):
            raise DomainError("search_replay", "Conflicting physical outcome", 409)
        return existing
    row = SearchEvaluationOutcome(
        id=uuid4(),
        **_scope(fence),
        evaluation_id=evaluation.id,
        admission_id=admission.id,
        stop_receipt_id=stop.id,
        stop_kind="stop",
        status=command.status,
        bundle_object_id=command.evidence.id,
        bundle_kind=kind,
        bundle_sha256=command.evidence.sha256,
        native_bundle_sha256=bundle_hash,
        input_manifest_sha256=evaluation.input_manifest_sha256,
        request_sha256=command.request_sha256,
    )
    session.add(row)
    session.flush()
    return row


def record_objective_receipt(session, fence, command, settings):
    from uuid import uuid4

    from backend.app.openevolve_models import (
        SearchCandidate,
        SearchEvaluation,
        SearchEvaluationOutcome,
        SearchObjectiveEvaluation,
        SearchObjectiveReceipt,
    )
    from packages.openevolve_adapter.host_contracts import (
        EvidenceRef,
        ObjectiveReceiptCommand,
    )
    from packages.openevolve_adapter.objective import native_observation, score
    from packages.openevolve_adapter.observer_transport import canonical

    command = ObjectiveReceiptCommand.model_validate(command.model_dump(mode="python"))
    _locked_epoch(session, fence, command.epoch_id)
    candidate = _owned_row(session, SearchCandidate, command.candidate_id, fence)
    if candidate.epoch_id != command.epoch_id:
        raise DomainError("search_identity", "Objective candidate epoch differs", 409)
    spec = SearchSpec.model_validate_json(
        canonical(session.get(StrategySearchSpec, fence.run_id).spec)
    )
    if len(command.evaluation_ids) != len(spec.objective.windows):
        raise DomainError(
            "search_objective", "Every frozen objective window requires an outcome", 409
        )
    observations, evaluations, outcomes = [], [], []
    for identity, window in zip(
        command.evaluation_ids, spec.objective.windows, strict=True
    ):
        evaluation = _owned_row(session, SearchEvaluation, identity, fence)
        outcome = session.scalar(
            select(SearchEvaluationOutcome).where(
                SearchEvaluationOutcome.evaluation_id == identity
            )
        )
        if outcome is None or (
            evaluation.epoch_id,
            evaluation.candidate_id,
            evaluation.source_sha256,
            evaluation.window_id,
            evaluation.execution_sha256,
        ) != (
            candidate.epoch_id,
            candidate.id,
            candidate.source_sha256,
            window.id,
            digest(spec.execution),
        ):
            raise DomainError(
                "search_objective", "Physical observation window/scope differs", 409
            )
        evaluations.append(evaluation)
        outcomes.append(outcome)
        if outcome.status == "observed":
            evidence = _read_evidence(
                session,
                fence,
                EvidenceRef(id=outcome.bundle_object_id, sha256=outcome.bundle_sha256),
                "search_native_observation",
                settings,
            )
            observations.append(
                native_observation(
                    evidence["bundle"],
                    window_id=window.id,
                    evaluation_id=identity,
                    expected_bundle_sha256=outcome.native_bundle_sha256,
                )
            )
    if any(outcome.status == "failed" for outcome in outcomes):
        status, value, reason = "execution_failed", None, "native_error"
    else:
        computed = score(spec.objective, tuple(observations))
        status = (
            "eligible"
            if computed.eligible
            else (
                "constraint_failed"
                if computed.reason == "explicit_trade_constraint"
                else "undefined_objective"
            )
        )
        value, reason = computed.combined_score, computed.reason
    feedback = canonical(
        {"status": status, "combined_score": value, "reason": reason}
    ).decode("utf-8")
    feedback_sha = hashlib.sha256(feedback.encode()).hexdigest()
    expected = {
        "schema_version": "mineagent.search-objective-object.v1",
        "candidate_id": str(candidate.id),
        "source_sha256": candidate.source_sha256,
        "objective_sha256": digest(spec.objective),
        "evaluation_ids": [str(x) for x in command.evaluation_ids],
        "status": status,
        "combined_score": value,
        "reason": reason,
        "feedback": feedback,
        "feedback_sha256": feedback_sha,
    }
    if canonical(
        _read_evidence(session, fence, command.evidence, "search_objective", settings)
    ) != canonical(expected):
        raise DomainError(
            "search_objective",
            "Objective evidence was not computed from exact physical observations",
            409,
        )
    existing = session.scalar(
        select(SearchObjectiveReceipt).where(
            SearchObjectiveReceipt.candidate_id == candidate.id
        )
    )
    if existing is not None:
        if existing.evidence_sha256 != command.evidence.sha256:
            raise DomainError("search_replay", "Conflicting objective evidence", 409)
        return existing
    row = SearchObjectiveReceipt(
        id=uuid4(),
        **_scope(fence),
        epoch_id=command.epoch_id,
        candidate_id=candidate.id,
        source_sha256=candidate.source_sha256,
        objective_sha256=digest(spec.objective),
        evidence_object_id=command.evidence.id,
        evidence_kind="search_objective",
        evidence_sha256=command.evidence.sha256,
        status=status,
        feedback=feedback,
        feedback_sha256=feedback_sha,
        combined_score=value,
    )
    session.add(row)
    for ordinal, evaluation in enumerate(evaluations):
        session.add(
            SearchObjectiveEvaluation(
                id=uuid4(),
                **_scope(fence),
                objective_receipt_id=row.id,
                epoch_id=candidate.epoch_id,
                candidate_id=candidate.id,
                source_sha256=candidate.source_sha256,
                evaluation_id=evaluation.id,
                outcome_id=outcomes[ordinal].id,
                window_id=evaluation.window_id,
                execution_sha256=evaluation.execution_sha256,
                ordinal=ordinal,
            )
        )
    session.flush()
    return row


def _persist_iteration_intent(session, fence, command, settings):
    from uuid import UUID, uuid4

    from backend.app.openevolve_models import (
        SearchCandidate,
        SearchIterationIntent,
        SearchTerminalApplication,
    )
    from backend.app.research_broker_models import ResearchBudgetReservation
    from packages.openevolve_adapter.host_contracts import IterationIntentCommand
    from packages.research_runtime.budget_contracts import BudgetVector

    command = IterationIntentCommand.model_validate(command.model_dump(mode="python"))
    _locked_epoch(session, fence, command.epoch_id)
    existing = session.scalar(
        select(SearchIterationIntent).where(
            SearchIterationIntent.epoch_id == command.epoch_id,
            SearchIterationIntent.native_iteration == command.native_iteration,
        )
    )
    if existing is not None:
        if (
            existing.selection_sha256 != command.selection.sha256
            or existing.mutation_reservation_id != command.mutation_reservation_id
            or existing.parent_candidate_id != command.parent_candidate_id
            or existing.parent_source_sha256 != command.parent_source_sha256
        ):
            raise DomainError("search_replay", "Conflicting iteration intent", 409)
        return existing
    parent = _owned_row(session, SearchCandidate, command.parent_candidate_id, fence)
    reservation = _owned_row(
        session, ResearchBudgetReservation, command.mutation_reservation_id, fence
    )
    if (
        parent.epoch_id != command.epoch_id
        or parent.source_sha256 != command.parent_source_sha256
    ):
        raise DomainError("search_parent", "Selected parent source differs", 409)
    if (
        reservation.purpose != "engine_mutation"
        or reservation.state != "reserved"
        or reservation.amounts != BudgetVector(mutation_slots=1).model_dump(mode="json")
    ):
        raise DomainError(
            "search_budget", "Exact preexisting mutation reservation required", 409
        )
    latest = session.scalar(
        select(SearchTerminalApplication)
        .where(SearchTerminalApplication.epoch_id == command.epoch_id)
        .order_by(SearchTerminalApplication.frontier.desc())
        .limit(1)
    )
    if latest is None:
        raise DomainError(
            "search_seed", "Seed frontier must be acknowledged before submission", 409
        )
    evidence = _read_evidence(
        session, fence, command.selection, "search_context", settings
    )
    if (
        type(evidence) is not dict
        or set(evidence)
        != {
            "schema_version",
            "native_iteration",
            "parent_id",
            "inspiration_ids",
            "snapshot",
        }
        or evidence["schema_version"] != "mineagent.native-selection.v1"
        or type(evidence["native_iteration"]) is not int
        or evidence["native_iteration"] != command.native_iteration
        or evidence["parent_id"] != str(parent.upstream_program_id)
        or type(evidence["inspiration_ids"]) is not list
    ):
        raise DomainError(
            "search_selection", "Actual native selection evidence differs", 409
        )
    import json

    from packages.openevolve_adapter.host_contracts import EvidenceRef
    from packages.openevolve_adapter.observer_transport import canonical, unique

    snapshot = evidence["snapshot"]
    previous_state = _validated_population(
        session,
        fence,
        command.epoch_id,
        latest.frontier,
        EvidenceRef(id=latest.state_object_id, sha256=latest.state_sha256),
        settings,
    )
    expected_artifacts = {}
    for program_id, program in previous_state["programs"].items():
        if program["artifacts_json"]:
            artifacts = json.loads(program["artifacts_json"], object_pairs_hook=unique)
            if artifacts:
                expected_artifacts[program_id] = artifacts
    if (
        type(snapshot) is not dict
        or set(snapshot)
        != {
            "programs",
            "islands",
            "current_island",
            "feature_dimensions",
            "artifacts",
            "sampling_island",
        }
        or type(snapshot["current_island"]) is not int
        or snapshot["current_island"] != 0
        or type(snapshot["sampling_island"]) is not int
        or snapshot["sampling_island"] != 0
        or snapshot["feature_dimensions"] != ["complexity", "diversity"]
        or canonical(snapshot["programs"]) != canonical(previous_state["programs"])
        or canonical(snapshot["artifacts"]) != canonical(expected_artifacts)
        or type(snapshot["islands"]) is not list
        or len(snapshot["islands"]) != 1
        or type(snapshot["islands"][0]) is not list
        or sorted(snapshot["islands"][0]) != previous_state["metadata"]["islands"][0]
        or str(parent.upstream_program_id) not in snapshot["programs"]
        or any(
            type(x) is not str or str(UUID(x)) != x or x not in snapshot["programs"]
            for x in evidence["inspiration_ids"]
        )
    ):
        raise DomainError(
            "search_selection",
            "Actual worker snapshot differs from acknowledged native state",
            409,
        )
    previous = session.scalar(
        select(SearchIterationIntent)
        .where(SearchIterationIntent.run_id == fence.run_id)
        .order_by(SearchIterationIntent.mutation_slot.desc())
        .limit(1)
    )
    slot = previous.mutation_slot + 1 if previous else 1
    spec = SearchSpec.model_validate_json(
        canonical(session.get(StrategySearchSpec, fence.run_id).spec)
    )
    if command.native_iteration != slot or slot > min(2, spec.maximum_mutation_slots):
        raise DomainError(
            "search_budget", "Initial epoch mutation frontier differs", 409
        )
    row = SearchIterationIntent(
        id=uuid4(),
        **_scope(fence),
        epoch_id=command.epoch_id,
        native_iteration=command.native_iteration,
        mutation_slot=slot,
        parent_candidate_id=parent.id,
        parent_source_sha256=parent.source_sha256,
        mutation_reservation_id=reservation.id,
        selection_object_id=command.selection.id,
        selection_kind="search_context",
        selection_sha256=command.selection.sha256,
        target_island=0,
    )
    session.add(row)
    session.flush()
    return row


def bind_resolved_execution_profile(session, fence, profile):
    from backend.app.openevolve_models import SearchResolvedExecutionProfile
    from packages.openevolve_adapter.execution_inputs import ResolvedNativeInputs
    from packages.openevolve_adapter.observer_transport import canonical

    _current_search(session, fence)
    profile = ResolvedNativeInputs.model_validate(profile.model_dump(mode="python"))
    spec_row = session.get(StrategySearchSpec, fence.run_id)
    if spec_row is None or spec_row.owner_id != fence.owner_id:
        raise DomainError("search_profile", "Persisted exact SearchSpec required", 409)
    spec = SearchSpec.model_validate_json(canonical(spec_row.spec))
    profile.validate_plan(spec.execution)
    existing = session.get(SearchResolvedExecutionProfile, fence.run_id)
    if existing is not None:
        if (
            existing.profile_sha256 != digest(profile)
            or existing.profile != profile.model_dump(mode="json")
            or existing.profile_canonical
            != canonical(profile.model_dump(mode="json")).decode("utf-8")
        ):
            raise DomainError(
                "search_replay", "Resolved execution profile is immutable", 409
            )
        return existing
    row = SearchResolvedExecutionProfile(
        owner_id=fence.owner_id,
        run_id=fence.run_id,
        execution_sha256=digest(spec.execution),
        profile_sha256=digest(profile),
        profile=profile.model_dump(mode="json"),
        profile_canonical=canonical(profile.model_dump(mode="json")).decode("utf-8"),
    )
    session.add(row)
    session.flush()
    return row


def _pending_intent_ids(intents, worker_outcomes, terminals):
    applied_workers = {
        row.worker_outcome_id for row in terminals if row.worker_outcome_id is not None
    }
    applied_intents = {
        row.intent_id for row in worker_outcomes if row.id in applied_workers
    }
    return {row.id for row in intents if row.id not in applied_intents}


def register_rendered_context(session, fence, command, settings):
    from uuid import uuid4

    from backend.app.openevolve_models import (
        SearchCandidate,
        SearchIterationIntent,
        SearchRenderedContext,
    )
    from backend.app.research_broker_models import ResearchOperationContext
    from packages.openevolve_adapter.contracts import SearchOperationContext
    from packages.openevolve_adapter.host_contracts import (
        EvidenceRef,
        RenderedContextCommand,
        RenderedProjection,
    )
    from packages.openevolve_adapter.observer_transport import canonical
    from packages.research_runtime.engine_broker_contracts import (
        parse_request,
        request_sha256,
    )

    command = RenderedContextCommand.model_validate(command.model_dump(mode="python"))
    _locked_epoch(session, fence, command.epoch_id)
    intent = _owned_row(session, SearchIterationIntent, command.intent_id, fence)
    context_row = _owned_row(
        session, ResearchOperationContext, command.context_id, fence
    )
    context = SearchOperationContext.model_validate_json(canonical(context_row.context))
    projection = RenderedProjection.model_validate_json(
        canonical(
            _read_evidence(
                session, fence, command.projection, "search_rendered_prompt", settings
            )
        )
    )
    if (
        (
            context.owner_id,
            context.run_id,
            context.epoch_id,
            context.iteration_intent_id,
            context.native_iteration,
            context.parent_candidate_id,
            context.parent_source_sha256,
            context.selection_snapshot_sha256,
            context.rendered_context_sha256,
            context.operation_ordinal,
        )
        != (
            fence.owner_id,
            fence.run_id,
            command.epoch_id,
            intent.id,
            intent.native_iteration,
            intent.parent_candidate_id,
            intent.parent_source_sha256,
            intent.selection_sha256,
            command.projection.sha256,
            0,
        )
        or context_row.context_sha256 != digest(context)
        or projection.current_source_sha256 != intent.parent_source_sha256
    ):
        raise DomainError(
            "search_context", "Rendered prompt context differs from exact intent", 409
        )
    selection = _read_evidence(
        session,
        fence,
        EvidenceRef(id=intent.selection_object_id, sha256=intent.selection_sha256),
        "search_context",
        settings,
    )
    from packages.openevolve_adapter.host_contracts import initial_prompt_references

    if projection.references != initial_prompt_references(selection):
        raise DomainError(
            "search_context",
            "Renderer-reported ordered exposure differs from fixed native selection structure",
            409,
        )
    programs = selection["snapshot"]["programs"]
    for reference in projection.references:
        program = programs.get(str(reference.upstream_program_id))
        expected_repr = (
            "summary_without_source_code"
            if reference.role == "previous_summary"
            else "source_code"
        )
        if (
            program is None
            or reference.source_sha256
            != hashlib.sha256(program["code"].encode()).hexdigest()
            or reference.representation != expected_repr
        ):
            raise DomainError(
                "search_context",
                "Actual prompt reference differs from selected native snapshot",
                409,
            )
        candidate = session.scalar(
            select(SearchCandidate).where(
                SearchCandidate.epoch_id == command.epoch_id,
                SearchCandidate.upstream_program_id == reference.upstream_program_id,
            )
        )
        if candidate is None or candidate.source_sha256 != reference.source_sha256:
            raise DomainError(
                "search_context", "Prompt reference has no registered source", 409
            )
    request = parse_request(
        "mineagent.openevolve-chat.v1",
        {
            "kind": "chat",
            "model": "openevolve-fixture.v1",
            "messages": [
                {"role": "system", "content": projection.system},
                {"role": "user", "content": projection.user},
            ],
            "max_output_tokens": 32768,
            "temperature_millionths": 0,
        },
    )
    request_hash = request_sha256("mineagent.openevolve-chat.v1", request)
    existing = session.scalar(
        select(SearchRenderedContext).where(
            SearchRenderedContext.intent_id == intent.id
        )
    )
    if existing is not None:
        if (
            existing.context_id != context_row.id
            or existing.rendered_sha256 != command.projection.sha256
            or existing.request_sha256 != request_hash
        ):
            raise DomainError("search_replay", "Conflicting rendered context", 409)
        return existing
    row = SearchRenderedContext(
        id=uuid4(),
        **_scope(fence),
        epoch_id=command.epoch_id,
        intent_id=intent.id,
        context_id=context_row.id,
        rendered_object_id=command.projection.id,
        rendered_kind="search_rendered_prompt",
        rendered_sha256=command.projection.sha256,
        request_sha256=request_hash,
    )
    session.add(row)
    session.flush()
    return row


def record_proposal_response(session, fence, command, settings):
    from uuid import uuid4

    from backend.app.openevolve_models import (
        SearchProposalResponse,
        SearchRenderedContext,
    )
    from backend.app.research_broker_models import (
        ResearchModelOperation,
        ResearchModelOutcome,
        ResearchModelTransport,
    )
    from packages.openevolve_adapter.host_contracts import ProposalResponseCommand

    command = ProposalResponseCommand.model_validate(command.model_dump(mode="python"))
    _locked_epoch(session, fence, command.epoch_id)
    rendered = session.scalar(
        select(SearchRenderedContext).where(
            SearchRenderedContext.intent_id == command.intent_id
        )
    )
    outcome = _owned_row(session, ResearchModelOutcome, command.model_outcome_id, fence)
    transport = _owned_row(session, ResearchModelTransport, outcome.transport_id, fence)
    operation = _owned_row(
        session, ResearchModelOperation, transport.operation_id, fence
    )
    if (
        rendered is None
        or rendered.epoch_id != command.epoch_id
        or operation.context_id != rendered.context_id
        or operation.request_sha256 != rendered.request_sha256
        or operation.purpose != "chat"
        or operation.ordinal != 0
        or outcome.outcome != "completed"
        or outcome.response_sha256 != command.response_sha256
    ):
        raise DomainError(
            "search_proposal", "Proposal lacks exact completed model operation", 409
        )
    from packages.openevolve_adapter.host_contracts import EvidenceRef
    from packages.research_runtime.engine_broker_contracts import (
        OEChatResponse,
        parse_request,
        validate_response,
    )

    request = parse_request("mineagent.openevolve-chat.v1", operation.request)
    response = _read_evidence(
        session,
        fence,
        EvidenceRef(id=outcome.response_object_id, sha256=outcome.response_sha256),
        "model_response",
        settings,
    )
    validated = validate_response(
        "mineagent.openevolve-chat.v1", request, OEChatResponse.model_validate(response)
    )
    if validated.content != command.response_text:
        raise DomainError(
            "search_proposal",
            "Actual upstream response differs from registered model bytes",
            409,
        )
    existing = session.scalar(
        select(SearchProposalResponse).where(
            SearchProposalResponse.intent_id == command.intent_id
        )
    )
    if existing is not None:
        if (
            existing.model_outcome_id != outcome.id
            or existing.response_sha256 != command.response_sha256
        ):
            raise DomainError("search_replay", "Conflicting proposal response", 409)
        return existing
    row = SearchProposalResponse(
        id=uuid4(),
        **_scope(fence),
        epoch_id=command.epoch_id,
        intent_id=command.intent_id,
        rendered_context_id=rendered.id,
        model_outcome_id=outcome.id,
        response_object_id=outcome.response_object_id,
        response_kind="model_response",
        response_sha256=outcome.response_sha256,
    )
    session.add(row)
    session.flush()
    return row


def record_proposal_source(session, fence, command, settings):
    from uuid import uuid4

    from backend.app.openevolve_models import (
        SearchProposalResponse,
        SearchProposalSource,
    )
    from packages.openevolve_adapter.host_contracts import (
        ProposalSourceCommand,
        ProposalSourceEnvelope,
    )
    from packages.openevolve_adapter.observer_transport import canonical

    command = ProposalSourceCommand.model_validate(command.model_dump(mode="python"))
    _locked_epoch(session, fence, command.epoch_id)
    proposal = _owned_row(session, SearchProposalResponse, command.proposal_id, fence)
    if proposal.epoch_id != command.epoch_id or proposal.intent_id != command.intent_id:
        raise DomainError("search_proposal", "Proposal source intent differs", 409)
    source = ProposalSourceEnvelope.model_validate_json(
        canonical(
            _read_evidence(
                session, fence, command.source, "search_proposal_source", settings
            )
        )
    )
    existing = session.scalar(
        select(SearchProposalSource).where(
            SearchProposalSource.intent_id == command.intent_id,
            SearchProposalSource.stage == source.stage,
        )
    )
    if existing is not None:
        if (
            existing.object_sha256 != command.source.sha256
            or existing.proposal_id != proposal.id
        ):
            raise DomainError("search_replay", "Conflicting proposal source", 409)
        return existing
    if (
        source.stage == "enforced"
        and session.scalar(
            select(SearchProposalSource).where(
                SearchProposalSource.intent_id == command.intent_id,
                SearchProposalSource.stage == "applied",
            )
        )
        is None
    ):
        raise DomainError(
            "search_proposal",
            "Enforced source lacks preserved applied-source evidence",
            409,
        )
    row = SearchProposalSource(
        id=uuid4(),
        **_scope(fence),
        epoch_id=command.epoch_id,
        intent_id=command.intent_id,
        proposal_id=proposal.id,
        stage=source.stage,
        source_sha256=source.source_sha256,
        source_bytes=source.source_bytes,
        evidence=source.evidence,
        object_id=command.source.id,
        object_kind="search_proposal_source",
        object_sha256=command.source.sha256,
    )
    session.add(row)
    session.flush()
    return row


def _latest_control(session, epoch_id):
    from backend.app.openevolve_models import SearchControlTransition

    return session.scalar(
        select(SearchControlTransition)
        .where(SearchControlTransition.epoch_id == epoch_id)
        .order_by(SearchControlTransition.sequence.desc())
        .limit(1)
    )


def _control_baseline(session, epoch_id, previous_terminal):
    cursor = _latest_control(session, epoch_id)
    if cursor is None or cursor.frontier != previous_terminal.frontier:
        raise DomainError(
            "search_cursor",
            "Exact acknowledged native control-state cursor required",
            409,
        )
    return cursor


def register_sampling_intent(session, fence, command, settings):
    from uuid import uuid4

    from backend.app.openevolve_models import (
        SearchControlTransition,
        SearchIterationIntent,
    )
    from packages.openevolve_adapter.host_contracts import (
        EvidenceRef,
        SamplingIntentCommand,
    )
    from packages.openevolve_adapter.sampling_contracts import (
        validate_sampling_transition,
    )

    command = SamplingIntentCommand.model_validate(command.model_dump(mode="python"))
    intent_command = command.intent
    _locked_epoch(session, fence, intent_command.epoch_id)
    existing_intent = session.scalar(
        select(SearchIterationIntent).where(
            SearchIterationIntent.epoch_id == intent_command.epoch_id,
            SearchIterationIntent.native_iteration == intent_command.native_iteration,
        )
    )
    if existing_intent is not None:
        receipt = session.scalar(
            select(SearchControlTransition).where(
                SearchControlTransition.sampling_intent_id == existing_intent.id
            )
        )
        if (
            receipt is None
            or receipt.request_sha256 != digest(command)
            or receipt.state_sha256 != command.after_state.sha256
        ):
            raise DomainError(
                "search_replay",
                "Conflicting or incomplete native sampling transition",
                409,
            )
        return existing_intent, receipt
    cursor = _latest_control(session, intent_command.epoch_id)
    if cursor is None or cursor.state_sha256 != command.before_state_sha256:
        raise DomainError(
            "search_cursor",
            "Native sampling started from unacknowledged control state",
            409,
        )
    before = _validated_population(
        session,
        fence,
        intent_command.epoch_id,
        cursor.frontier,
        EvidenceRef(id=cursor.state_object_id, sha256=cursor.state_sha256),
        settings,
    )
    after = _validated_population(
        session,
        fence,
        intent_command.epoch_id,
        cursor.frontier,
        command.after_state,
        settings,
    )
    selection = _read_evidence(
        session, fence, intent_command.selection, "search_context", settings
    )
    validate_sampling_transition(
        before, after, selection["parent_id"], selection["snapshot"]
    )
    intent = _persist_iteration_intent(session, fence, intent_command, settings)
    row = SearchControlTransition(
        id=uuid4(),
        **_scope(fence),
        epoch_id=intent.epoch_id,
        sequence=cursor.sequence + 1,
        previous_id=cursor.id,
        previous_sequence=cursor.sequence,
        kind="sampling",
        frontier=cursor.frontier,
        terminal_id=None,
        sampling_intent_id=intent.id,
        state_object_id=command.after_state.id,
        state_kind="search_population",
        state_sha256=command.after_state.sha256,
        request_sha256=digest(command),
    )
    session.add(row)
    session.flush()
    return intent, row


def _validate_control_chain(transitions, intents, terminals, latest):
    ordered = sorted(transitions, key=lambda row: row.sequence)
    if not ordered or ordered[-1].id != latest.id:
        raise DomainError("search_cursor", "Control cursor closure differs", 409)
    prior = None
    intent_by_id = {row.id: row for row in intents}
    sampled, applied = set(), set()
    for index, row in enumerate(ordered, 1):
        if (
            row.sequence != index
            or row.previous_id != (prior.id if prior else None)
            or row.previous_sequence != (prior.sequence if prior else None)
        ):
            raise DomainError(
                "search_cursor", "Control cursor chain is not contiguous", 409
            )
        if index == 1:
            valid = row.kind == "seed" and row.frontier == 0
        elif row.kind == "sampling":
            intent = intent_by_id.get(row.sampling_intent_id)
            valid = (
                row.frontier == prior.frontier
                and row.sampling_intent_id not in sampled
                and intent is not None
                and intent.native_iteration == len(sampled) + 1
                and intent.native_iteration > row.frontier
            )
        else:
            valid = (
                row.kind == "terminal"
                and row.frontier == prior.frontier + 1
                and row.frontier <= len(sampled)
            )
        if not valid:
            raise DomainError("search_cursor", "Control cursor transition differs", 409)
        if row.kind == "sampling":
            sampled.add(row.sampling_intent_id)
        else:
            applied.add(row.terminal_id)
        prior = row
    if sampled != {row.id for row in intents} or applied != {
        row.id for row in terminals
    }:
        raise DomainError(
            "search_cursor",
            "Sampling intent or terminal lacks exact control receipt",
            409,
        )

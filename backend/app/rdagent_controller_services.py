"""Disabled RD controller identity projection; no listener, launcher or authority mint."""

from sqlalchemy import select

from backend.app.rdagent_models import FactorResearchAttemptBinding
from backend.app.rdagent_services import _scope, require_research_spec
from backend.app.services import DomainError
from packages.rdagent_adapter.provider_bootstrap import build_operational_bootstrap


def controller_profile(admission):
    """Exact explicit successor, never a wildcard orchestrator profile."""
    if admission.profile == "mineagent.rdagent-orchestrator.v1":
        return admission.profile
    if (
        admission.profile
        in (
            "mineagent.rdagent-orchestrator.v2",
            "mineagent.rdagent-orchestrator.v3",
            "mineagent.rdagent-orchestrator.v4",
        )
        and admission.schema_version == "mineagent.process-admission.v2"
        and admission.ipc_policy == "host_ticket"
        and isinstance(admission.ticket_sha256, str)
        and len(admission.ticket_sha256) == 64
    ):
        return admission.profile
    raise DomainError(
        "factor_controller_profile", "Exact RD controller profile required", 409
    )


def expected_operational_bootstrap(session, fence):
    """Derive fixed bootstrap from locked current attempt/spec, never event fields."""
    spec, config = require_research_spec(session, fence)
    binding = session.scalar(
        select(FactorResearchAttemptBinding)
        .filter_by(**_scope(fence))
        .with_for_update()
    )
    if (
        binding is None
        or binding.spec_id != spec.id
        or binding.resumed_checkpoint_id is not None
    ):
        raise DomainError(
            "factor_controller_binding_missing",
            "Fresh RD attempt binding is required",
            409,
        )
    from backend.app.rdagent_workflow_controller import workflow_bootstrap_if_declared

    workflow = workflow_bootstrap_if_declared(
        session, fence, binding=binding, spec=spec, config=config
    )
    if workflow is not None:
        return binding, workflow
    from backend.app.rdagent_seed_controller import seeded_bootstrap_if_declared

    seeded = seeded_bootstrap_if_declared(
        session, fence, binding=binding, spec=spec, config=config
    )
    if seeded is not None:
        return binding, seeded
    bootstrap = build_operational_bootstrap(
        owner_id=fence.owner_id,
        run_id=fence.run_id,
        attempt_id=fence.attempt_id,
        generation=fence.generation,
        spec_id=spec.id,
        attempt_binding_id=binding.id,
        config=config,
        config_sha256=spec.config_sha256,
        input_sha256=spec.input_sha256,
    )
    return binding, bootstrap


def bind_controller_launch(session, fence, *, admission_id):
    """Host-only pre-launch transition; incoming RPC cannot select its own admission."""
    from backend.app.rdagent_models import FactorControllerLaunch
    from backend.app.rdagent_services import _scope_row
    from backend.app.research_process_models import ResearchProcessAdmission

    attempt_binding, bootstrap = expected_operational_bootstrap(session, fence)
    admission = _scope_row(session, ResearchProcessAdmission, admission_id, fence)
    from packages.rdagent_adapter.controller_profile import (
        COMPOSED_CONTROLLER_PROFILE,
        SEEDED_CONTROLLER_PROFILE,
    )

    selected = (
        admission.profile
        if admission.profile in (COMPOSED_CONTROLLER_PROFILE, SEEDED_CONTROLLER_PROFILE)
        else None
    )
    if selected != bootstrap.config.controller_profile:
        raise DomainError(
            "factor_controller_admission_drift",
            "Controller profile differs from immutable config",
            409,
        )
    if (
        admission.role,
        admission.profile,
        admission.image_id,
        admission.input_sha256,
        admission.logical_operation_id,
    ) != (
        "rd_orchestrator",
        controller_profile(admission),
        bootstrap.config.runtime.orchestrator_image,
        bootstrap.sha256,
        attempt_binding.id,
    ):
        raise DomainError(
            "factor_controller_admission_drift",
            "Controller admission differs from fixed bootstrap",
            409,
        )
    current = session.scalar(
        select(FactorControllerLaunch).filter_by(**_scope(fence)).with_for_update()
    )
    expected = (
        attempt_binding.id,
        admission_id,
        bootstrap.model_dump(mode="json"),
        bootstrap.sha256,
    )
    if current is not None:
        if (
            current.attempt_binding_id,
            current.admission_id,
            current.bootstrap,
            current.bootstrap_sha256,
        ) != expected:
            raise DomainError(
                "factor_controller_launch_drift",
                "Attempt controller launch is immutable",
                409,
            )
        return current
    from uuid import uuid4

    current = FactorControllerLaunch(
        id=uuid4(),
        **_scope(fence),
        attempt_binding_id=attempt_binding.id,
        admission_id=admission_id,
        bootstrap=expected[2],
        bootstrap_sha256=bootstrap.sha256,
    )
    session.add(current)
    session.flush()
    return current


def require_rd_controller(session, fence, authorization):
    """Every event revalidates locked immutable launch plus current kernel peer."""
    from runtimes.rdagent_operational.peer import ControllerPeer

    if type(authorization) is ControllerPeer:
        from backend.app.rdagent_protected_controller import (
            require_protected_controller,
        )

        return require_protected_controller(session, fence, authorization)
    from backend.app.engine_peer_services import (
        ControllerBinding,
        require_controller_peer,
    )
    from backend.app.rdagent_models import FactorControllerLaunch

    attempt_binding, bootstrap = expected_operational_bootstrap(session, fence)
    launch = session.scalar(
        select(FactorControllerLaunch).filter_by(**_scope(fence)).with_for_update()
    )
    if launch is None or (
        launch.attempt_binding_id,
        launch.bootstrap,
        launch.bootstrap_sha256,
    ) != (attempt_binding.id, bootstrap.model_dump(mode="json"), bootstrap.sha256):
        raise DomainError(
            "factor_controller_launch_drift",
            "Exact RD controller launch is required",
            409,
        )
    admission = require_controller_peer(
        session,
        fence,
        authorization,
        binding=ControllerBinding(
            admission_id=launch.admission_id,
            role="rd_orchestrator",
            profile="mineagent.rdagent-orchestrator.v1",
            image_id=bootstrap.config.runtime.orchestrator_image,
            bootstrap_sha256=bootstrap.sha256,
        ),
    )
    if admission.logical_operation_id != attempt_binding.id:
        raise DomainError(
            "factor_controller_operation_drift",
            "Controller operation differs from bound attempt",
            409,
        )
    return launch


def authenticated_open_upstream_call(
    session, fence, authorization, *, start_id, call_id, phase, kind
):
    """No call identity from another peer/attempt can enter the domain transition."""
    from backend.app.rdagent_services import open_upstream_call

    require_rd_controller(session, fence, authorization)
    return open_upstream_call(
        session, fence, start_id=start_id, call_id=call_id, phase=phase, kind=kind
    )


def require_recorded_controller(session, fence, admission_id):
    """Historical identity check only; NOT live-peer authorization or event issuance."""
    from backend.app.rdagent_models import FactorControllerLaunch
    from backend.app.rdagent_services import _scope_row
    from backend.app.research_process_models import ResearchProcessAdmission

    attempt_binding, bootstrap = expected_operational_bootstrap(session, fence)
    launch = session.scalar(
        select(FactorControllerLaunch).filter_by(**_scope(fence)).with_for_update()
    )
    if launch is None or (
        launch.admission_id,
        launch.attempt_binding_id,
        launch.bootstrap_sha256,
        launch.bootstrap,
    ) != (
        admission_id,
        attempt_binding.id,
        bootstrap.sha256,
        bootstrap.model_dump(mode="json"),
    ):
        raise DomainError(
            "factor_controller_launch_drift",
            "Recorded receipt producer differs from immutable launch",
            409,
        )
    admission = _scope_row(session, ResearchProcessAdmission, admission_id, fence)
    if (
        admission.logical_operation_id,
        admission.role,
        admission.profile,
        admission.image_id,
        admission.input_sha256,
    ) != (
        attempt_binding.id,
        "rd_orchestrator",
        controller_profile(admission),
        bootstrap.config.runtime.orchestrator_image,
        bootstrap.sha256,
    ):
        raise DomainError(
            "factor_controller_admission_drift",
            "Recorded controller admission differs",
            409,
        )
    return launch


def authenticated_reserve_source(session, fence, authorization, **request):
    from backend.app.rdagent_source_publication import reserve_source

    require_rd_controller(session, fence, authorization)
    return reserve_source(session, fence, **request)


def authenticated_admit_request(session, fence, authorization, **request):
    from backend.app.rdagent_services import admit_atomic_request

    require_rd_controller(session, fence, authorization)
    return admit_atomic_request(session, fence, **request)


def authenticated_close_upstream_call(
    session, fence, authorization, *, call_id, succeeded, settings
):
    """Freeze all request membership only after actual completed response closure.

    Failed exchange with unresolved transport remains unclosed for reconciliation;
    a controller's exception is never non-submission/stop evidence or a refund.
    """
    from uuid import uuid4

    from backend.app.rdagent_models import FactorUpstreamCall, FactorUpstreamCallOutcome
    from backend.app.rdagent_services import (
        _call_members,
        _completed_member_response,
        _scope_row,
    )

    launch = require_rd_controller(session, fence, authorization)
    if type(succeeded) is not bool:
        raise DomainError(
            "factor_call_boundary_invalid",
            "Exact exchange completion boolean required",
            409,
        )
    call = _scope_row(session, FactorUpstreamCall, call_id, fence, lock=True)
    members = _call_members(session, fence, call.id)
    completed = [
        _completed_member_response(session, fence, member, settings)
        for member in members
    ]
    if succeeded and (
        not members
        or (call.kind == "chat" and completed[-1][1].finish_reason != "stop")
    ):
        raise DomainError(
            "factor_call_boundary_incomplete",
            "Successful exchange requires complete response",
            409,
        )
    values = (
        launch.id,
        "completed" if succeeded else "aborted",
        len(members),
        [str(member.id) for member in members],
        [str(outcome.id) for outcome, _ in completed],
    )
    existing = session.scalar(
        select(FactorUpstreamCallOutcome).filter_by(**_scope(fence), call_id=call.id)
    )
    if existing is not None:
        if (
            existing.controller_launch_id,
            existing.status,
            existing.member_count,
            existing.member_ids,
            existing.response_outcome_ids,
        ) != values:
            raise DomainError(
                "factor_call_boundary_drift", "Exchange boundary is immutable", 409
            )
        return existing
    from backend.app.rdagent_models import FactorGenerationOutcome

    if (
        session.scalar(
            select(FactorGenerationOutcome).filter_by(
                **_scope(fence), start_id=call.start_id
            )
        )
        is not None
    ):
        raise DomainError(
            "factor_generation_closed",
            "Terminal generation cannot add exchange boundaries",
            409,
        )
    result = FactorUpstreamCallOutcome(
        id=uuid4(),
        **_scope(fence),
        call_id=call.id,
        controller_launch_id=values[0],
        status=values[1],
        member_count=values[2],
        member_ids=values[3],
        response_outcome_ids=values[4],
    )
    session.add(result)
    session.flush()
    return result


def authenticated_open_evaluation(session, fence, authorization, **request):
    from backend.app.rdagent_evaluation_services import open_generation_evaluation

    require_rd_controller(session, fence, authorization)
    return open_generation_evaluation(session, fence, **request)

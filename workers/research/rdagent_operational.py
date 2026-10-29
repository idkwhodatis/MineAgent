"""Actual maintained single-generation RD controller worker; default dispatch closed.

This internal acceptance entrypoint runs only the fixed synthetic single-day
profile with fixture-model transport. It is not a public user-configurable engine
issuer and never executes source outside the separately contained numeric units.
"""

import hashlib
import json
import secrets
from datetime import date, timedelta
from uuid import UUID, uuid4

from sqlalchemy import null, select, text

from backend.app.core.atomic_storage import provision_directory
from backend.app.core.maintenance import shared_lifecycle
from backend.app.models import (
    DatasetVersion,
    ExecutionAttempt,
    ResearchRun,
    User,
    utcnow,
)
from backend.app.rdagent_complete_evaluation import maintained_calendar
from backend.app.rdagent_controller_process import (
    persist_controller_intent,
    record_controller_child,
    record_controller_stopped,
)
from backend.app.rdagent_controller_services import (
    bind_controller_launch,
    expected_operational_bootstrap,
)
from backend.app.rdagent_models import FactorGenerationOutcome, FactorResearchSpec
from backend.app.rdagent_operational_admission import (
    check_controller_claim,
    claim_controller_launch,
    record_claimed_controller_identity,
)
from backend.app.rdagent_operational_handler import OperationalHandler
from backend.app.rdagent_protected_controller import authenticate_current_controller
from backend.app.rdagent_services import _scope, bind_research_attempt
from backend.app.research_broker_models import (
    ResearchBudgetControl,
    ResearchBudgetReservation,
)
from backend.app.research_broker_services import (
    initialize_budget,
    reserve_budget,
    settle_budget,
)
from backend.app.research_lifecycle import neutral_transaction
from backend.app.research_process_models import ResearchProcessState
from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.controller_profile import (
    controller_profile,
    controller_wall_milliseconds,
    controller_wall_seconds,
)
from packages.rdagent_adapter.provider_bootstrap import RDProviderSeededBootstrap
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.fences import AttemptFence
from packages.research_runtime.process_contracts import ProcessError
from packages.research_runtime.process_declarations_v2 import (
    HostTicketIPC,
    ProcessDeclarationV2,
)
from runtimes.rdagent_operational.process_journal import STORAGE_FLOOR, ControllerIntent
from runtimes.rdagent_operational.rpc import serve_one
from runtimes.rdagent_operational.supervisor import (
    BASE_IMAGE,
    ControllerSupervisor,
    _issue_committed_authority,
    verify_image_inventory,
)
from runtimes.research_supervisor.supervisor import DockerSupervisor

PROVENANCE = "maintained_actual_rdagent_controller_fixture_v1"


def run_registered_rdagent(*_args, **_kwargs):
    raise ProcessError("rdagent_operational_catalog_not_registered")


def require_maintained_revision(session):
    # Successors retain this original fixture protocol; never substitute the
    # configured analysis plan or infer compatibility for an unknown revision.
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        ["021_execution_snapshots"],
        ["022_factor_analysis"],
        ["023_rd_submissions"],
        ["024_openevolve_initial"],
        ["025_structured_factor_strategy"],
        ["026_rd_seeded_submissions"],
        ["027_rd_controller_v3"],
        ["027a_contract_repairs"],
        ["027b_provider_material"],
        ["027c_seeded_controller_v4"],
    ):
        raise ProcessError("rd_controller_revision")


def create_maintained_run(factory, *, owner_id, dataset_id, config, settings):
    config = FactorResearchConfig.model_validate(config)
    if (
        type(owner_id) is not UUID
        or type(dataset_id) is not UUID
        or config.input.dataset.dataset_version_id != dataset_id
    ):
        raise ProcessError("rd_controller_owned_dataset")
    maintained_calendar(config)
    if (
        config.max_iterations != 1
        or config.input.window.start != date(2024, 3, 15)
        or config.input.window.end != date(2024, 3, 16)
        or config.input.lookback_sessions != 2
        or config.input.lag_sessions != 0
    ):
        raise ProcessError("rd_controller_single_declared_day")
    for directory in ("factors", "research-objects", "research-processes"):
        provision_directory(
            settings.storage_root / directory, durable_parent=settings.storage_root
        )
    run_id, attempt_id, token = uuid4(), uuid4(), uuid4()
    fence = AttemptFence(owner_id, run_id, attempt_id, 1, token)
    with factory.begin() as session:
        require_maintained_revision(session)
        dataset = session.get(DatasetVersion, dataset_id)
        if (
            session.get(User, owner_id) is None
            or dataset is None
            or dataset.owner_id != owner_id
            or not dataset.is_synthetic
            or dataset.content_sha256 != config.input.dataset.content_sha256
        ):
            raise ProcessError("rd_controller_owned_dataset")
        run = ResearchRun(
            id=run_id,
            owner_id=owner_id,
            idempotency_key=uuid4().hex,
            request_sha256=config.sha256,
            config=config.model_dump(mode="json"),
            purpose="factor_research",
            engine="rdagent",
            dataset_version_id=dataset_id,
            strategy_version_id=None,
            submission_binding=null(),
            status="running",
            generation=1,
            research_image_digest=config.runtime.orchestrator_image,
        )
        session.add(run)
        session.flush()
        session.add(
            ExecutionAttempt(
                id=attempt_id,
                run_id=run_id,
                generation=1,
                worker_id=PROVENANCE,
                lease_token=token,
                lease_expires_at=utcnow() + timedelta(minutes=10),
                status="running",
                sandbox_id="not-launched:" + attempt_id.hex,
                sandbox_stopped=False,
            )
        )
        session.flush()
        run.active_attempt_id = attempt_id
        session.add(
            FactorResearchSpec(
                id=uuid4(),
                owner_id=owner_id,
                run_id=run_id,
                config=config.model_dump(mode="json"),
                config_sha256=config.sha256,
                input_sha256=config.input.sha256,
            )
        )
        session.add(
            ResearchBudgetControl(
                id=uuid4(),
                owner_id=owner_id,
                run_id=run_id,
                limits=config.budget.model_dump(),
                used=BudgetVector().model_dump(),
                reserved=BudgetVector().model_dump(),
            )
        )
    with neutral_transaction(factory, fence, resource_scope="processes") as session:
        initialize_budget(session, fence, config.budget)
        bind_research_attempt(session, fence)
    return fence


def _image_inventory(image_id):
    cli = DockerSupervisor(image_id)
    base = json.loads(cli._command(["image", "inspect", BASE_IMAGE])[1])[0]
    image = json.loads(cli._command(["image", "inspect", image_id])[1])[0]
    inventory = verify_image_inventory(base, image)
    if inventory["image_id"] != image_id:
        raise ProcessError("rd_controller_image_identity")
    return inventory


def execute_maintained_controller(factory, fence, settings):
    return _execute_controller(
        factory, fence, settings, handler_type=OperationalHandler
    )


def _execute_controller(factory, fence, settings, *, handler_type):
    """Positive completion requires real RPC terminal generation AND whole-unit stop.

    An uncertain create/claim is retained, never recreated. This method deliberately
    does not claim autonomous stale-fence recovery or ACK-loss generation resume.
    """
    with shared_lifecycle(factory.kw["bind"], settings.storage_root):
        with neutral_transaction(factory, fence, resource_scope="processes") as session:
            _, bootstrap = expected_operational_bootstrap(session, fence)
            if controller_profile(
                bootstrap.config
            ) == "mineagent.rdagent-orchestrator.v4" and session.scalars(
                text("SELECT version_num FROM alembic_version")
            ).all() != ["027c_seeded_controller_v4"]:
                raise ProcessError("rd_controller_seeded_revision")
            if controller_profile(
                bootstrap.config
            ) == "mineagent.rdagent-orchestrator.v3" and session.scalars(
                text("SELECT version_num FROM alembic_version")
            ).all() not in (
                ["027_rd_controller_v3"],
                ["027a_contract_repairs"],
                ["027b_provider_material"],
                ["027c_seeded_controller_v4"],
            ):
                raise ProcessError("rd_controller_composed_revision")
        from backend.app.rdagent_seed_controller import reread_bootstrap_parent
        from packages.rdagent_adapter.seeded_bootstrap import (
            RDSeededOperationalBootstrap,
        )

        if isinstance(
            bootstrap, (RDSeededOperationalBootstrap, RDProviderSeededBootstrap)
        ):
            with neutral_transaction(
                factory, fence, resource_scope="factor_numeric"
            ) as session:
                reread_bootstrap_parent(session, fence, bootstrap, settings)
        inventory = _image_inventory(bootstrap.config.runtime.orchestrator_image)
        ticket = secrets.token_hex(32)
        with neutral_transaction(factory, fence, resource_scope="processes") as session:
            binding, current = expected_operational_bootstrap(session, fence)
            if current != bootstrap:
                raise ProcessError("rd_controller_bootstrap_changed")
            held = reserve_budget(
                session,
                fence,
                purpose="process_unit",
                settings=settings,
                logical_key="rd-controller:" + str(binding.id),
                amounts=BudgetVector(
                    process_slots=1,
                    wall_milliseconds=controller_wall_milliseconds(
                        controller_profile(bootstrap.config)
                    ),
                    storage_bytes=STORAGE_FLOOR,
                ),
            )
            admission = ProcessDeclarationV2(
                schema_version="mineagent.process-admission.v2",
                id=uuid4(),
                **_scope(fence),
                role="rd_orchestrator",
                logical_operation_id=binding.id,
                launch_ordinal=1,
                profile=controller_profile(bootstrap.config),
                input_sha256=bootstrap.sha256,
                reservation_id=held.id,
                image_id=bootstrap.config.runtime.orchestrator_image,
                ipc=HostTicketIPC(
                    ipc_policy="host_ticket",
                    ticket_sha256=hashlib.sha256(ticket.encode()).hexdigest(),
                ),
            )
            now = utcnow()
            intent = ControllerIntent(
                schema_version="mineagent.rd-controller-intent.v1",
                id=uuid4(),
                admission=admission,
                bootstrap_sha256=bootstrap.sha256,
                bootstrap_size_bytes=len(canonical_bytes(bootstrap)),
                input_layout="single_bootstrap_json_no_other_mounts",
                issued_at=now,
                not_after=now
                + timedelta(seconds=controller_wall_seconds(admission.profile)),
            )
            persist_controller_intent(session, fence, intent)
            bind_controller_launch(session, fence, admission_id=admission.id)
        with neutral_transaction(factory, fence, resource_scope="processes") as session:
            claimed = claim_controller_launch(session, fence, admission.id)
        with neutral_transaction(factory, fence, resource_scope="processes") as session:
            check_controller_claim(
                session, fence, admission.id, expected_intent=claimed
            )
        authority = _issue_committed_authority(claimed)
        supervisor = ControllerSupervisor(admission.image_id, inventory)

        def identity_observed(_record):
            with neutral_transaction(
                factory, fence, resource_scope="processes"
            ) as session:
                record_claimed_controller_identity(
                    session, fence, admission.id, storage_root=settings.storage_root
                )
                attempt = session.get(ExecutionAttempt, fence.attempt_id)
                attempt.sandbox_id = _record.container_id

        def child_observed(_record):
            with neutral_transaction(
                factory, fence, resource_scope="processes"
            ) as session:
                record_controller_child(
                    session, fence, admission.id, storage_root=settings.storage_root
                )

        def stopped(_record):
            with neutral_transaction(
                factory, fence, resource_scope="processes"
            ) as session:
                record_controller_stopped(
                    session, fence, admission.id, storage_root=settings.storage_root
                )
                held = session.get(ResearchBudgetReservation, admission.reservation_id)
                settle_budget(
                    session,
                    fence,
                    held.id,
                    charge=BudgetVector.model_validate(held.amounts),
                    outcome="conservative_unknown",
                )

        def before_submission():
            with neutral_transaction(
                factory, fence, resource_scope="processes"
            ) as session:
                check_controller_claim(
                    session, fence, admission.id, expected_intent=intent
                )

        running = None
        handler = handler_type(factory, fence, settings)
        failure = None
        try:
            running = supervisor.start(
                settings.storage_root,
                authority,
                canonical_bytes(bootstrap),
                ticket,
                on_identity=identity_observed,
                before_submission=before_submission,
            )
            handler.original_deadline = running.deadline
            channel, binding = running.accept_controller(on_child=child_observed)

            def authorize(channel, supplied_ticket):
                return authenticate_current_controller(
                    factory,
                    fence,
                    channel,
                    admission,
                    binding=binding,
                    supplied_ticket=supplied_ticket,
                    observer_context=running.observer_context,
                )

            sequence = 0
            while not handler.returned:
                sequence = serve_one(
                    channel,
                    sequence=sequence,
                    deadline=running.deadline,
                    authenticate=authorize,
                    handle=handler,
                )
            running.finish(on_stopped=stopped)
        except BaseException as error:  # noqa: BLE001 - retain containment on cancellation too
            failure = error
            try:
                if running is not None:
                    # An abort has no output authority. Detach readers before
                    # forced exact-unit cleanup so daemon logging cannot block.
                    if running.connection is not None:
                        running.connection.close()
                    running.listener.close()
                    running.reader.close()
                _recover_and_record(
                    factory, settings.storage_root, admission, supervisor
                )
                # Only the still-current lease may charge/finish its own attempt.
                # A stale lease has already recorded containment but cannot do so.
                stopped(None)
            except BaseException as cleanup:
                raise error from cleanup
        with neutral_transaction(factory, fence, resource_scope="processes") as session:
            states = list(
                session.scalars(select(ResearchProcessState).filter_by(**_scope(fence)))
            )
            whole_stop = bool(states) and all(s.status == "stopped" for s in states)
            result = session.scalar(
                select(FactorGenerationOutcome).filter_by(
                    **_scope(fence), logical_step=0
                )
            )
            success = (
                failure is None
                and handler.returned
                and running.original_deadline_completion
                and whole_stop
                and result is not None
                and result.outcome == "accepted"
            )
            run = session.get(ResearchRun, fence.run_id)
            attempt = session.get(ExecutionAttempt, fence.attempt_id)
            from backend.app.rdagent_provider_containment import (
                provider_attempt_stopped,
            )

            if not whole_stop or not provider_attempt_stopped(
                session, run, attempt, settings
            ):
                # Docker absence does not prove the separately admitted host SDK
                # groups stopped. Leave the attempt nonterminal for recovery.
                raise ProcessError("rd_provider_containment_unknown")
            run.status = attempt.status = "succeeded" if success else "failed"
            attempt.sandbox_stopped = whole_stop
            # Actual Docker inspect result; stop.v1 itself proves absence only.
            attempt.exit_code = None if running is None else running.measured_exit_code
            attempt.finished_at = utcnow()
            if not success:
                attempt.error = "rd_controller_execution_failed"
            from backend.app.research_lifecycle import _NEUTRAL_KEY

            session.info[_NEUTRAL_KEY]["terminal"] = (
                "rd_controller_succeeded" if success else "rd_controller_failed",
                run.id,
                attempt.id,
                attempt.finished_at,
                attempt.exit_code,
            )
        if failure is not None:
            raise failure
        if not success:
            raise ProcessError("rd_controller_incomplete_generation")
        return fence.run_id


def _recover_and_record(factory, storage_root, admission, supervisor):
    from backend.app.rdagent_controller_recovery import record_controller_containment
    from runtimes.rdagent_operational.recovery import recover_controller

    recover_controller(supervisor, storage_root, admission)
    # No process namespace flock is held during SQL locking. No current lease is
    # required for recording this old unit's positive absence; no outputs imported.
    with factory() as session:
        record_controller_containment(session, admission.id, storage_root=storage_root)
        session.commit()


def recover_controller_unit(factory, storage_root, admission_id):
    """Internal containment-only entry, never re-create/restart or result import."""
    from backend.app.research_process_models import ResearchProcessAdmission
    from packages.research_runtime.process_persistence_codec import (
        ROW_FIELDS,
        decode_row,
    )
    from runtimes.rdagent_operational.process_journal import controller_admission

    with shared_lifecycle(factory.kw["bind"], storage_root):
        with factory() as session:
            row = session.get(ResearchProcessAdmission, admission_id)
            if row is None:
                return False
            admission = controller_admission(
                decode_row({k: getattr(row, k) for k in ROW_FIELDS})
            )
        supervisor = ControllerSupervisor(
            admission.image_id, _image_inventory(admission.image_id)
        )
        _recover_and_record(factory, storage_root, admission, supervisor)
        return True

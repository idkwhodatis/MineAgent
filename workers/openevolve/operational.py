"""Actual protected initial-epoch worker, internal maintained acceptance only.

No default dispatcher/public submission or resume. Caller supplies an already
owned current run/spec/attempt and a fixed host fixture schedule. Actual024 SQL,
backup, Docker and whole-loop gates remain prerequisites to registration.
"""

import json
import secrets
from datetime import timedelta

from sqlalchemy import select

from backend.app.core.atomic_storage import provision_directory
from backend.app.core.maintenance import shared_lifecycle
from backend.app.models import ExecutionAttempt, utcnow
from backend.app.openevolve_broker_rpc import FixtureSchedule
from backend.app.openevolve_controller_process import (
    record_controller_child,
    record_controller_stopped,
)
from backend.app.openevolve_epoch_admission import prepare_initial_epoch
from backend.app.openevolve_epoch_completion import complete_initial_epoch
from backend.app.openevolve_failure_diagnostic import emit_failure
from backend.app.openevolve_models import StrategySearchSpec
from backend.app.openevolve_operational_admission import (
    check_controller_claim,
    claim_controller_launch,
    record_claimed_controller_identity,
)
from backend.app.openevolve_services import _scope
from backend.app.openevolve_socket_server import InitialEpochServer
from backend.app.research_broker_models import ResearchBudgetReservation
from backend.app.research_broker_services import settle_budget
from backend.app.research_lifecycle import neutral_transaction
from backend.app.research_process_models import ResearchProcessState
from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.contracts import SearchSpec
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_contracts import ProcessError
from runtimes.openevolve_operational.supervisor import (
    BASE_IMAGE,
    ControllerSupervisor,
    _issue_committed_authority,
    verify_image_inventory,
)
from runtimes.research_supervisor.supervisor import DockerSupervisor


def run_registered_openevolve(*_args, **_kwargs):
    raise ProcessError("openevolve_operational_catalog_not_registered")


def _image_inventory(image_id):
    cli = DockerSupervisor(image_id)
    base = json.loads(cli._command(["image", "inspect", BASE_IMAGE])[1])[0]
    image = json.loads(cli._command(["image", "inspect", image_id])[1])[0]
    result = verify_image_inventory(base, image)
    if result["image_id"] != image_id:
        raise ProcessError("oe_controller_image_identity")
    return result


def run_initial_epoch(factory, fence, *, runtime, schedule, settings):
    """Returns freshly revalidated stopped checkpoint and terminal run result.

    Terminal run/attempt publication uses the separate fresh retained
    closure validator. Failure preserves discoverable journals/holds and never
    recreates a claimed unit. Stale-fence recovery is not granted here.
    """
    if type(schedule) is not FixtureSchedule:
        raise ValueError("Host-owned fixed fixture schedule required")
    with shared_lifecycle(factory.kw["bind"], settings.storage_root):
        with neutral_transaction(factory, fence, resource_scope="processes") as session:
            row = session.get(StrategySearchSpec, fence.run_id)
            if row is None or row.owner_id != fence.owner_id:
                raise ValueError("Owned persisted search spec required")
            spec = SearchSpec.model_validate_json(canonical(row.spec))
            if len(schedule.responses) != spec.maximum_mutation_slots:
                raise ValueError("Exact complete initial fixture schedule required")
            attempt = session.get(ExecutionAttempt, fence.attempt_id)
            # This first internal maintained issuer does not invent a heartbeat
            # protocol or extend a lease from a worker DTO. Fixture must arrange
            # sufficient bounded lease before the independent600s unit starts.
            if attempt.lease_expires_at < utcnow() + timedelta(seconds=690):
                raise ValueError("Maintained controller needs prior690s current lease")
        # Quota admission locks these namespaces before the first object exists.
        # Keep the existing lifecycle fence and durable no-follow provisioning;
        # never relax object_storage_guard for an uninitialized installation.
        for directory in ("factors", "research-objects", "research-processes"):
            provision_directory(
                settings.storage_root / directory, durable_parent=settings.storage_root
            )
        image_inventory = _image_inventory(spec.runtime_image)
        supervisor = ControllerSupervisor(spec.runtime_image, image_inventory)
        ticket, ticket_key = secrets.token_hex(32), secrets.token_bytes(32)
        with neutral_transaction(factory, fence, resource_scope="processes") as session:
            intent, bootstrap, bootstrap_bytes, seed = prepare_initial_epoch(
                session,
                fence,
                runtime=runtime,
                process_ticket=ticket,
                settings=settings,
            )
        admission = intent.admission
        with neutral_transaction(factory, fence, resource_scope="processes") as session:
            claimed = claim_controller_launch(session, fence, admission.id)
            if claimed != intent:
                raise ProcessError("oe_controller_claim_identity")
        with neutral_transaction(factory, fence, resource_scope="processes") as session:
            check_controller_claim(session, fence, admission.id, expected_intent=intent)
        authority = _issue_committed_authority(intent)

        def before_submission():
            with neutral_transaction(
                factory, fence, resource_scope="processes"
            ) as session:
                check_controller_claim(
                    session, fence, admission.id, expected_intent=intent
                )

        def identity_observed(_record):
            with neutral_transaction(
                factory, fence, resource_scope="processes"
            ) as session:
                record_claimed_controller_identity(
                    session, fence, admission.id, storage_root=settings.storage_root
                )

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

        running = service = None
        try:
            running = supervisor.start(
                settings.storage_root,
                authority,
                bootstrap_bytes,
                seed,
                ticket,
                on_identity=identity_observed,
                before_submission=before_submission,
            )
            _channel, binding = running.accept_controller(on_child=child_observed)
            service = InitialEpochServer(
                factory,
                fence,
                running,
                binding,
                epoch_id=bootstrap.epoch_id,
                final_frontier=spec.maximum_mutation_slots,
                ticket_key=ticket_key,
                schedule=schedule,
                settings=settings,
            )
            result = service.serve()
            running.finish(on_stopped=stopped)
        except BaseException as original:
            emit_failure("before_cleanup", service, running, original)
            if service is not None:
                service.disconnect()
            if running is not None:
                try:
                    # Never reruns Docker after an unresolved prior finish. The
                    # supervisor caches actual positiveStop for callback replay.
                    running.finish(on_stopped=stopped, no_reader=True)
                except BaseException as cleanup:
                    raise original from cleanup
                finally:
                    emit_failure("after_cleanup", service, running, original)
            raise
        if not running.original_deadline_completion:
            raise ProcessError("oe_controller_incomplete_original_deadline")
        with neutral_transaction(factory, fence, resource_scope="processes") as session:
            states = session.scalars(
                select(ResearchProcessState).filter_by(**_scope(fence))
            ).all()
            if not states or any(row.status != "stopped" for row in states):
                raise ProcessError("oe_epoch_has_unresolved_process")
            checkpoint_id = complete_initial_epoch(
                session,
                fence,
                epoch_id=bootstrap.epoch_id,
                frontier=spec.maximum_mutation_slots,
                controller_admission_id=admission.id,
                actual_exit_code=running.measured_exit_code,
                original_deadline_completion=running.original_deadline_completion,
                settings=settings,
            )
        return {
            **result,
            "epoch_id": bootstrap.epoch_id,
            "controller_admission_id": admission.id,
            "actual_exit_code": running.measured_exit_code,
            "positive_stop": True,
            "checkpoint_id": checkpoint_id,
        }

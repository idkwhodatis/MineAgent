"""Normal027 one-shot controller issuer. No fixture schedule/acceptance initializer.

Only persisted user config selects image/bootstrap/seed. Each admission and claim
commits before external create; an existing epoch is never a hidden retry. This
function does not publish run success: caller must use the separate stopped epoch
closure and terminal worker transaction. Uncertain containment remains an error.
"""

import json
import secrets
from datetime import timedelta

from backend.app.core.maintenance import shared_lifecycle
from backend.app.factor_native_io import native_storage_guard
from backend.app.models import ExecutionAttempt, utcnow
from backend.app.oe_structured_controller_process_services import (
    check_controller_claim,
    claim_controller_intent,
    persist_controller_intent,
    record_controller_completion,
    record_controller_identity,
    record_controller_stopped,
    settle_stopped_controller,
)
from backend.app.oe_structured_engine_services import fail
from backend.app.oe_structured_epoch_services import (
    _current,
    expected_controller_bootstrap,
    prepare_controller_epoch,
)
from backend.app.oe_structured_socket_server import serve_controller
from backend.app.research_lifecycle import neutral_transaction
from packages.factors.contracts import canonical_bytes
from runtimes.openevolve_structured_host.supervisor import (
    BASE_IMAGE,
    ControllerSupervisor,
    _issue_committed_authority,
    verify_image_inventory,
)
from runtimes.research_supervisor.supervisor import DockerSupervisor


def image_inventory(runtime):
    cli = DockerSupervisor(runtime.controller_image_id)

    def inspect(ref):
        values = json.loads(cli._command(["image", "inspect", ref])[1])
        if type(values) is not list or len(values) != 1:
            fail("oe_structured_image_inventory")
        return values[0]

    base = inspect(BASE_IMAGE)
    parent = inspect(runtime.controller_installed_parent_image_id)
    image = inspect(runtime.controller_image_id)
    result = verify_image_inventory(base, image)
    parent_layers = parent.get("RootFS", {}).get("Layers", [])
    image_layers = image.get("RootFS", {}).get("Layers", [])
    if (
        parent.get("Id") != runtime.controller_installed_parent_image_id
        or result["image_id"] != runtime.controller_image_id
        or not parent_layers
        or len(image_layers) <= len(parent_layers)
        or image_layers[: len(parent_layers)] != parent_layers
    ):
        fail("oe_structured_image_parent")
    return result


def execute_controller_epoch(factory, fence, settings):
    """Return stopped epoch ID only; no restart/recovery or terminal-result claim."""
    ticket, key = secrets.token_hex(32), secrets.token_bytes(32)
    running = None
    with shared_lifecycle(factory.kw["bind"], settings.storage_root):
        with native_storage_guard(settings.storage_root):
            with neutral_transaction(
                factory, fence, resource_scope="factor_numeric"
            ) as session:
                _, config = _current(session, fence, settings)
                attempt = session.get(ExecutionAttempt, fence.attempt_id)
                if attempt is None or attempt.lease_expires_at < utcnow() + timedelta(
                    seconds=690
                ):
                    fail("oe_structured_original_lease")
                runtime = config.runtime
            inventory = image_inventory(runtime)
            with neutral_transaction(
                factory, fence, resource_scope="factor_numeric"
            ) as session:
                if _current(session, fence, settings)[1].runtime != runtime:
                    fail("oe_structured_runtime_drift")
                epoch, intent, bootstrap, seed = prepare_controller_epoch(
                    session, fence, settings, process_ticket=ticket
                )
                persist_controller_intent(session, fence, intent, settings)
                epoch_id, admission_id = epoch.id, intent.admission.id
            with neutral_transaction(
                factory, fence, resource_scope="factor_numeric"
            ) as session:
                claimed = claim_controller_intent(
                    session, fence, admission_id, settings
                )
                if claimed != intent:
                    fail("oe_structured_claim_drift")

            def current():
                with neutral_transaction(
                    factory, fence, resource_scope="factor_numeric"
                ) as session:
                    check_controller_claim(
                        session, fence, admission_id, settings, expected_intent=intent
                    )
                    _, actual, original_seed = expected_controller_bootstrap(
                        session, fence, epoch_id, settings
                    )
                    if canonical_bytes(actual) != bootstrap or original_seed != seed:
                        fail("oe_structured_bootstrap_drift")

            def identity(_record):
                with neutral_transaction(
                    factory, fence, resource_scope="factor_numeric"
                ) as session:
                    record_controller_identity(
                        session, fence, admission_id, storage_root=settings.storage_root
                    )

            current()
            authority = _issue_committed_authority(intent)
            supervisor = ControllerSupervisor(runtime.controller_image_id, inventory)
            running = supervisor.start(
                settings.storage_root,
                authority,
                bootstrap,
                seed,
                ticket,
                on_identity=identity,
                before_submission=current,
            )

        def stopped(_record):
            # Called only while the issuer owns the native guard; actual bytes reread.
            with neutral_transaction(
                factory, fence, resource_scope="factor_numeric"
            ) as session:
                record_controller_stopped(
                    session, fence, admission_id, storage_root=settings.storage_root
                )
                settle_stopped_controller(
                    session, fence, admission_id, storage_root=settings.storage_root
                )

        try:
            serve_controller(
                factory, fence, epoch_id, settings, running=running, ticket_key=key
            )
            with native_storage_guard(settings.storage_root):
                running.finish(on_stopped=stopped)
                with neutral_transaction(
                    factory, fence, resource_scope="factor_numeric"
                ) as session:
                    record_controller_completion(
                        session, fence, admission_id, storage_root=settings.storage_root
                    )
        except BaseException as original:
            # Failure is never promoted to a metric, checkpoint success or restart.
            # Original error remains visible; cleanup failure means unknown containment.
            try:
                with native_storage_guard(settings.storage_root):
                    running.finish(on_stopped=stopped, no_reader=True)
            except BaseException as cleanup:
                raise original from cleanup
            raise
        return epoch_id

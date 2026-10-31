"""One exact027 controller containment unit; no attempt reconciliation or launch."""

from sqlalchemy import text

from backend.app.core.maintenance import shared_lifecycle
from backend.app.factor_native_io import native_storage_guard
from backend.app.models import ResearchRun
from backend.app.oe_structured_controller_recovery import record_controller_containment
from backend.app.oe_structured_identity import decode_spec
from backend.app.oe_structured_operational_models import OEStructuredEpoch
from backend.app.research_process_models import ResearchProcessAdmission
from packages.research_runtime.process_contracts import ProcessError
from packages.research_runtime.process_persistence_codec import ROW_FIELDS, decode_row
from runtimes.openevolve_structured_host.process_journal import controller_admission
from runtimes.openevolve_structured_host.recovery import recover_controller
from runtimes.openevolve_structured_host.supervisor import ControllerSupervisor
from workers.research.oe_structured_controller import image_inventory


def recover_controller_unit(factory, settings, admission_id):
    """Lifecycle/native guard retained, physical fence released before SQL locks.

    Immutable owned runtime determines the image, never operator enablement or
    model credentials. A disabled model does not prevent containment. Existing
    reconciliation owns later conservative settlement and terminal decisions.
    """
    root = settings.storage_root
    with shared_lifecycle(factory.kw["bind"], root), native_storage_guard(root):
        with factory() as session:
            if session.scalars(
                text("SELECT version_num FROM alembic_version")
            ).all() not in (
                ["028_oe_structured_search"],
                ["029_research_workflow"],
                ["030_declared_upload_basket"],
                ["031_workflow_structured_stage"],
                ["032_normal_oe_note_pins"],
                ["032a_rd_terminal_results"],
                ["032b_oe_append_run_state"],
            ):
                raise ProcessError("oe_structured_controller_recovery_revision")
            row = session.get(ResearchProcessAdmission, admission_id)
            if row is None:
                return False
            admission = controller_admission(
                decode_row({k: getattr(row, k) for k in ROW_FIELDS})
            )
            run = session.get(ResearchRun, admission.run_id)
            if run is None or run.owner_id != admission.owner_id:
                raise ProcessError("oe_structured_controller_recovery_scope")
            _, config = decode_spec(session, run)
            epoch = session.get(OEStructuredEpoch, admission.logical_operation_id)
            if (
                epoch is None
                or epoch.controller_admission_id != admission.id
                or any(
                    getattr(epoch, k) != getattr(admission, k)
                    for k in ("owner_id", "run_id", "attempt_id", "generation")
                )
                or config.runtime.controller_image_id != admission.image_id
            ):
                raise ProcessError("oe_structured_controller_recovery_epoch")
            runtime = config.runtime
        supervisor = ControllerSupervisor(admission.image_id, image_inventory(runtime))
        recover_controller(supervisor, root, admission)
        with factory() as session:
            record_controller_containment(session, admission.id, storage_root=root)
            session.commit()
        return True

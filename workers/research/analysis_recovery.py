"""Contain an existing exact analysis unit; never launch, import or settle."""

from backend.app.analysis_launch_services import record_analysis_containment
from backend.app.analysis_process_services import _persisted
from backend.app.core.maintenance import shared_lifecycle
from backend.app.research_process_models import ResearchProcessAdmission
from packages.research_runtime.analysis_process_journal import AnalysisIntent
from packages.research_runtime.process_contracts import ProcessError, canonical
from runtimes.research_supervisor.analysis_supervisor import AnalysisSupervisor


def _binding(session, admission_id):
    from backend.app.factor_analysis_models import (
        FactorAnalysisPlan,
        FactorDirectAnalysisIntent,
    )

    admission, _state, receipts = _persisted(session, admission_id)
    intent = AnalysisIntent.model_validate_json(
        canonical(receipts["launch_intent"].payload)
    )
    row = session.get(FactorDirectAnalysisIntent, admission.logical_operation_id)
    plan = session.get(FactorAnalysisPlan, row.plan_id) if row is not None else None
    if (
        intent.admission != admission
        or row is None
        or plan is None
        or any(
            getattr(row, key) != getattr(admission, key)
            for key in ("owner_id", "run_id", "attempt_id", "generation")
        )
        or (plan.owner_id, plan.run_id) != (admission.owner_id, admission.run_id)
        or plan.qlib_image_id != admission.image_id
        or (
            row.id,
            row.snapshot_id,
            row.label_source_id,
            row.protocol_sha256,
            row.analysis_input_sha256,
            row.analysis_input_size_bytes,
        )
        != (
            intent.inputs.analysis_id,
            intent.inputs.snapshot_id,
            intent.inputs.label_source_id,
            intent.inputs.protocol_sha256,
            intent.inputs.analysis_input_sha256,
            intent.inputs.analysis_input_size_bytes,
        )
    ):
        raise ProcessError("analysis_recovery_binding")
    return admission, plan.qlib_installed_parent_image_id


def recover_analysis_unit(factory, storage_root, admission_id):
    """No fence grant required for exact containment of an old obligation.

    Missing/unobserved identity remains uncertain. The supervisor may contain
    its exact verified container, but no fabricated stop, extraction, restart,
    budget settlement or outcome publication is permitted here.
    """
    with shared_lifecycle(factory.kw["bind"], storage_root):
        with factory() as session:
            if session.get(ResearchProcessAdmission, admission_id) is None:
                return False
            admission, parent_image = _binding(session, admission_id)
        AnalysisSupervisor(admission.image_id, parent_image).stop_and_verify(
            storage_root, admission
        )
        with factory() as session:
            # Recheck immutable DB identity after external containment; do not
            # require an active lease and do not promote historical result data.
            if _binding(session, admission_id) != (admission, parent_image):
                raise ProcessError("analysis_recovery_binding")
            record_analysis_containment(
                session, admission_id, storage_root=storage_root
            )
            session.commit()
        return True

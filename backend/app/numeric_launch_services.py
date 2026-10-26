"""Private operational numeric issuer persistence; no default launch registration."""

from sqlalchemy import select

from backend.app.numeric_process_services import (
    _persisted,
    _scope,
    as_numeric_admission,
)
from backend.app.research_process_models import ResearchProcessAdmission
from packages.research_runtime.numeric_process_journal import NumericIntent
from packages.research_runtime.process_contracts import ProcessError, digest


def claim_numeric_launch(session, fence, admission_id):
    """Durable one-shot claim; a lost claim is uncertain, never silently relaunched."""
    from backend.app.models import utcnow
    from backend.app.research_lifecycle import require_neutral_transaction
    from packages.research_runtime.process_contracts import canonical

    require_neutral_transaction(session, fence, resource_scope="processes")
    admission, state, receipts = _persisted(session, admission_id)
    _scope(admission, fence)
    if state.status != "launch_intended" or set(receipts) != {"launch_intent"}:
        raise ProcessError("numeric_process_already_claimed")
    intent = NumericIntent.model_validate_json(
        canonical(receipts["launch_intent"].payload)
    )
    if (
        intent.admission != admission
        or not intent.issued_at <= utcnow() < intent.not_after
    ):
        raise ProcessError("numeric_process_intent")
    state.status, state.updated_at = "uncertain", utcnow()
    session.flush()
    return intent


def record_numeric_identity(session, fence, admission_id, *, storage_root):
    from backend.app.models import utcnow
    from backend.app.research_lifecycle import require_neutral_transaction
    from backend.app.research_process_services import _receipt
    from packages.research_runtime.numeric_process_journal import (
        NumericIdentity,
        NumericLaunch,
    )
    from packages.research_runtime.process_contracts import canonical
    from runtimes.research_supervisor.numeric_journal import (
        read_numeric_record as read_record,
    )
    from runtimes.research_supervisor.supervisor import operation_path

    require_neutral_transaction(session, fence, resource_scope="processes")
    admission, state, receipts = _persisted(session, admission_id)
    _scope(admission, fence)
    path = operation_path(storage_root, admission) / "control"
    launch = NumericLaunch.model_validate_json(
        canonical(read_record(path / "launch.json"))
    )
    identity = NumericIdentity.model_validate_json(
        canonical(read_record(path / "identity.json"))
    )
    if (
        state.status not in {"uncertain", "running"}
        or launch.intent.admission != admission
        or receipts["launch_intent"].payload != launch.intent.model_dump(mode="json")
        or identity.admission_sha256 != digest(admission.model_dump(mode="json"))
        or identity.launch_sha256 != digest(launch.model_dump(mode="json"))
        or identity.image_id != admission.image_id
    ):
        raise ProcessError("numeric_process_journal_identity")
    _receipt(session, admission, "launch_identity", identity.model_dump(mode="json"))
    state.status, state.updated_at = "running", utcnow()
    session.flush()


def record_numeric_containment(session, admission_id, *, storage_root):
    """Post-lease exact containment only; never extraction, result or settlement."""
    from backend.app.models import ExecutionAttempt, ResearchRun, utcnow
    from backend.app.research_process_services import _receipt
    from backend.app.services import advisory_lock
    from runtimes.research_supervisor.numeric_journal import read_numeric_journal

    row = session.get(ResearchProcessAdmission, admission_id)
    if row is None:
        raise ProcessError("numeric_process_missing")
    admission = as_numeric_admission(row)
    advisory_lock(session, f"submit:{admission.owner_id}")
    run = session.scalar(
        select(ResearchRun)
        .where(
            ResearchRun.id == admission.run_id,
            ResearchRun.owner_id == admission.owner_id,
        )
        .with_for_update()
    )
    attempt = session.scalar(
        select(ExecutionAttempt)
        .where(
            ExecutionAttempt.id == admission.attempt_id,
            ExecutionAttempt.run_id == admission.run_id,
            ExecutionAttempt.generation == admission.generation,
        )
        .with_for_update()
    )
    if run is None or attempt is None:
        raise ProcessError("numeric_process_scope")
    admission, state, receipts = _persisted(session, admission_id)
    launch, identity, stop, _ = read_numeric_journal(storage_root, admission)
    if receipts["launch_intent"].payload != launch.intent.model_dump(mode="json"):
        raise ProcessError("numeric_process_intent")
    _receipt(session, admission, "launch_identity", identity.model_dump(mode="json"))
    _receipt(session, admission, "stop", stop.model_dump(mode="json"))
    state.status, state.updated_at = "stopped", utcnow()
    session.flush()
    return stop


def check_numeric_claim(session, fence, intent):
    """Fresh current-fence check after commit, immediately before external launch."""
    from backend.app.models import utcnow
    from backend.app.research_lifecycle import require_neutral_transaction

    require_neutral_transaction(session, fence, resource_scope="processes")
    admission, state, receipts = _persisted(session, intent.admission.id)
    _scope(admission, fence)
    if (
        admission != intent.admission
        or state.status != "uncertain"
        or set(receipts) != {"launch_intent"}
        or receipts["launch_intent"].payload != intent.model_dump(mode="json")
        or not intent.issued_at <= utcnow() < intent.not_after
    ):
        raise ProcessError("numeric_process_claim")

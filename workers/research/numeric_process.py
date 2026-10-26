"""Persisted numeric orchestration. Default application registration stays closed.

The internal execution boundary requires upstream durable source/native object
resolution. Its explicit fixed-fixture acceptance caller is separate from the
production entrypoint and never grants arbitrary generated-source execution.
"""

from backend.app.core.maintenance import shared_lifecycle
from backend.app.numeric_launch_services import (
    check_numeric_claim,
    claim_numeric_launch,
    record_numeric_containment,
    record_numeric_identity,
)
from backend.app.numeric_process_services import (
    as_numeric_admission,
    record_numeric_stopped,
)
from backend.app.research_lifecycle import neutral_transaction
from backend.app.research_process_models import ResearchProcessAdmission
from packages.research_runtime.process_contracts import ProcessError
from runtimes.research_supervisor.numeric_supervisor import (
    NumericSupervisor,
    _mint_numeric_authority,
    check_input_bytes,
)


def run_registered_numeric_unit(*_args, **_kwargs):
    """No approved production runtime catalog is installed by this change."""
    raise ProcessError("numeric_process_catalog_not_registered")


def _execute_committed_numeric_unit(
    factory, fence, storage_root, admission_id, *, input_bytes, inventory
):
    """Internal post-resolution boundary; no caller DTO is a launch token.

    Issuing caller must be a reviewed durable-source/native-object resolver,
    never a public request handler. Claim commits before authority is minted;
    a crash after claiming does not authorize a second attempt at this unit.
    """
    with shared_lifecycle(factory.kw["bind"], storage_root):
        with neutral_transaction(factory, fence, resource_scope="processes") as session:
            intent = claim_numeric_launch(session, fence, admission_id)
            check_input_bytes(intent, input_bytes)
            if inventory["image_id"] != intent.admission.image_id:
                raise ProcessError("numeric_process_image")
        _execute_claimed_numeric_unit(
            factory,
            fence,
            storage_root,
            intent,
            input_bytes=input_bytes,
            inventory=inventory,
        )


def _execute_claimed_numeric_unit(
    factory, fence, storage_root, intent, *, input_bytes, inventory, settings=None
):
    # Caller invokes this only immediately following its successful claim commit.
    # This private function is never a request/DTO entrypoint.
    admission_id = intent.admission.id
    scope = (
        "factor_numeric"
        if intent.admission.role == "factor_normalizer"
        else "processes"
    )
    authority = _mint_numeric_authority(intent, input_bytes, inventory)
    supervisor = NumericSupervisor(intent.admission.image_id)

    def identity_observed(_identity):
        with neutral_transaction(factory, fence, resource_scope="processes") as session:
            record_numeric_identity(
                session, fence, admission_id, storage_root=storage_root
            )

    try:
        with neutral_transaction(factory, fence, resource_scope=scope) as session:
            check_numeric_claim(session, fence, intent)
            if scope == "factor_numeric":
                from backend.app.native_normalizer_claim import (
                    require_native_normalizer_claim,
                )

                if settings is None:
                    raise ProcessError("numeric_native_claim_required")
                _identity, native = require_native_normalizer_claim(
                    session, fence, admission_id, settings
                )
                if native != input_bytes["/input/result.h5"]:
                    raise ProcessError("numeric_native_claim_drift")
        supervisor.execute(storage_root, authority, on_identity=identity_observed)
        with neutral_transaction(factory, fence, resource_scope="processes") as session:
            record_numeric_stopped(
                session,
                fence,
                admission_id,
                storage_root=storage_root,
                with_output=True,
            )
    finally:
        # Even a lost lease may record positive whole-unit stop, but this
        # path never imports extraction or gives result/publication authority.
        import sys

        original_error = sys.exception()
        try:
            with factory() as session:
                record_numeric_containment(
                    session, admission_id, storage_root=storage_root
                )
                session.commit()
        except Exception as containment_error:
            if original_error is None:
                raise
            original_error.add_note(
                "Numeric containment persistence unconfirmed: "
                + type(containment_error).__name__
            )


def recover_numeric_unit(factory, storage_root, admission_id):
    """Containment-only recovery cannot launch or publish under a stale fence."""
    with shared_lifecycle(factory.kw["bind"], storage_root):
        with factory() as session:
            row = session.get(ResearchProcessAdmission, admission_id)
            if row is None:
                return False
            admission = as_numeric_admission(row)
        NumericSupervisor(admission.image_id).stop_and_verify(storage_root, admission)
        with factory() as session:
            record_numeric_containment(session, admission_id, storage_root=storage_root)
            session.commit()
        return True

"""One explicit physical fixture submission with durable pre-call accounting.

No retries are implicit. The authenticated whole-unit peer owns the exact model
operation. Unknown outcomes remain charged and cannot be redispatched.
"""

import time
from functools import wraps
from uuid import uuid4

from backend.app.core.maintenance import shared_lifecycle
from backend.app.research_broker_models import (
    ResearchModelOperation,
    ResearchOperationContext,
)
from backend.app.research_broker_services import (
    mark_submitted,
    replay_completed,
    reserve_budget,
    reserve_transport,
    settle_transport,
)
from backend.app.research_lifecycle import neutral_transaction
from packages.research_runtime.broker_contracts import (
    FixtureContext,
    ObjectRef,
    canonical_bytes,
    digest,
    parse_request,
)
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.control import BrokerAbort
from workers.research.fixture_transport import submit_fixture


def _terminal(function):
    @wraps(function)
    def execute(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except BrokerAbort:
            raise
        except Exception as exc:  # noqa: BLE001 -- terminal fail-stop boundary
            code = (
                "fence_lost"
                if getattr(exc, "code", "")
                in {"attempt_fenced", "neutral_transaction_lost", "neutral_busy"}
                else "publication_failed"
            )
            raise BrokerAbort(code) from None

    return execute


@_terminal
def publish_model_object(factory, fence, kind, value, settings):
    from backend.app.research_object_services import (
        publish_reserved_object,
        reserve_object,
    )

    if kind not in {"model_request", "model_response"}:
        raise BrokerAbort("publication_failed")
    content = canonical_bytes(value)
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        reservation = reserve_budget(
            session,
            fence,
            purpose="object_storage",
            logical_key=f"object:{uuid4()}",
            amounts=BudgetVector(storage_bytes=2 * len(content) + 8192),
        )
        publication = reserve_object(
            session, fence, kind, content, reservation.id, settings
        )
        publication_id = publication.id
    object_id = publish_reserved_object(
        factory, fence, publication_id, content, settings
    )
    return ObjectRef(id=object_id, kind=kind, sha256=digest(value))


@_terminal
def execute_fixture_once(
    factory, fence, operation_id, request_sha256, *, authorization, settings
):
    from backend.app.research_process_services import require_running_peer
    from runtimes.research_supervisor.ipc import require_authenticated_peer

    require_authenticated_peer(authorization, fence, logical_operation_id=operation_id)
    with shared_lifecycle(factory.kw["bind"], settings.storage_root):
        with neutral_transaction(factory, fence) as session:
            operation = session.get(ResearchModelOperation, operation_id)
            if operation is not None and operation.state == "completed":
                return replay_completed(session, fence, operation_id, request_sha256)
            require_running_peer(session, fence, authorization, operation_id)
            transport = reserve_transport(session, fence, operation_id, request_sha256)
            transport_id, ordinal = transport.id, transport.ordinal
            request = parse_request(operation.request)
            context = FixtureContext.model_validate(
                session.get(ResearchOperationContext, operation.context_id).context
            )
        # Pre-submission admission is committed separately; if commit succeeds but
        # its acknowledgement is lost, no transport occurs and the hold remains.
        with neutral_transaction(factory, fence) as session:
            require_running_peer(session, fence, authorization, operation_id)
            mark_submitted(session, fence, transport_id)
        require_authenticated_peer(
            authorization, fence, logical_operation_id=operation_id
        )
        try:
            result = submit_fixture(context, request, ordinal)
            response = (
                None
                if result.response is None
                else publish_model_object(
                    factory, fence, "model_response", result.response, settings
                )
            )
            with neutral_transaction(factory, fence) as session:
                settle_transport(
                    session,
                    fence,
                    transport_id,
                    outcome="failed" if response is None else "completed",
                    usage=result.usage,
                    response_object=response,
                    response=result.response,
                )
            return response
        except BrokerAbort:
            raise
        except Exception:  # noqa: BLE001 -- uncertainty must bypass upstream retries
            # No automatic refund, replacement call or outcome fabrication. A
            # durable submitted row conservatively retains its full reservation.
            raise BrokerAbort("publication_failed") from None


@_terminal
def controlled_retry_sleep(
    factory, fence, seconds: float, *, clock=time.monotonic, sleep=time.sleep
):
    """Cancellation/fence loss bypass retry fallbacks, including during backoff."""
    if type(seconds) not in {int, float} or not 0 <= seconds <= 30:
        raise ValueError("Retry sleep exceeds bounded profile")
    deadline = clock() + seconds
    while True:
        with neutral_transaction(factory, fence):
            pass
        remaining = deadline - clock()
        if remaining <= 0:
            return
        sleep(min(0.02, remaining))

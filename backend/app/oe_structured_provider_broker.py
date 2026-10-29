"""One actual SDK call per retained normal027 rendered operation.

No endpoint/secret enters the controller. Durable submitted membership and SDK
intent precede process creation; started evidence commits before the private
pipe receives request/secret. Missing identity/stop/fence remains uncertain.
"""

import math
import os
import time
from dataclasses import asdict
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select

from backend.app.core.maintenance import shared_lifecycle
from backend.app.factor_native_io import native_storage_guard
from backend.app.oe_structured_controller_context import fail, scoped
from backend.app.oe_structured_identity import decode_spec
from backend.app.oe_structured_models import OEStructuredProviderExchange
from backend.app.oe_structured_operational_models import (
    OEStructuredIterationIntent,
    OEStructuredProviderTransport,
)
from backend.app.oe_structured_provider_admission import admit_provider_transport
from backend.app.oe_structured_provider_evidence import (
    read_completed_response,
    read_physical_chain,
)
from backend.app.oe_structured_provider_operation import require_provider_operation
from backend.app.oe_structured_provider_publication import (
    publish_evidence,
    retain_physical_receipt,
)
from backend.app.oe_structured_server_profile import (
    resolve_operator_profile,
    resolve_runtime,
)
from backend.app.research_broker_models import (
    ResearchBudgetReservation,
    ResearchContextAbort,
    ResearchModelOperation,
    ResearchModelOutcome,
    ResearchModelTransport,
)
from backend.app.research_broker_services import settle_budget
from backend.app.research_lifecycle import (
    neutral_transaction,
    require_neutral_transaction,
)
from backend.app.research_object_services import read_research_object
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.provider_accounting import (
    request_reservation,
    validate_completed_usage,
)
from packages.openevolve_structured.provider_contracts import (
    OEChatRequest,
    require_request_binding,
)
from packages.openevolve_structured.provider_receipts import (
    SDKIntent,
    SDKStarted,
    SDKStopped,
)
from packages.openevolve_structured.provider_response import response_bytes
from packages.openevolve_structured.provider_transport import complete_chat
from packages.research_runtime.budget_contracts import BudgetVector


def _settle(
    session, fence, membership_id, settings, *, response_id=None, response=None
):
    run, _ = require_neutral_transaction(
        session, fence, resource_scope="factor_numeric"
    )
    _, config = decode_spec(session, run)
    member = scoped(session.get(OEStructuredProviderTransport, membership_id), fence)
    transport = scoped(session.get(ResearchModelTransport, member.transport_id), fence)
    operation = scoped(session.get(ResearchModelOperation, member.operation_id), fence)
    if (
        transport.operation_id != operation.id
        or transport.ordinal != 1
        or member.request_sha256 != operation.request_sha256
        or operation.purpose != "chat"
        or operation.ordinal != 0
        or operation.request_object_kind != "oe_structured_provider_request"
        or transport.state != "submitted"
        or operation.state != "open"
        or operation.protocol != "mineagent.oe-structured-provider-chat.v1"
    ):
        fail()
    attempts = session.scalars(
        select(ResearchModelTransport).filter_by(operation_id=operation.id)
    ).all()
    if (
        len(attempts) != 1
        or attempts[0].id != transport.id
        or session.scalar(
            select(ResearchContextAbort).filter_by(context_id=operation.context_id)
        )
        is not None
        or session.scalar(
            select(ResearchModelOutcome).filter_by(transport_id=transport.id)
        )
        is not None
    ):
        fail()
    request_raw = read_research_object(
        session,
        fence,
        operation.request_object_id,
        operation.request_object_kind,
        operation.request_sha256,
        settings,
    )
    request = require_request_binding(
        OEChatRequest.model_validate_json(request_raw), config.model_binding
    )
    if (
        canonical_bytes(request) != request_raw
        or request.sha256 != member.request_sha256
        or request.sha256 != operation.request_sha256
        or request.model_dump(mode="json") != operation.request
        or member.model_binding_sha256 != config.model_binding.sha256
    ):
        fail()
    amount = request_reservation(request, config.model_binding)
    reservation = scoped(
        session.get(ResearchBudgetReservation, transport.reservation_id), fence
    )
    if (
        reservation.state != "reserved"
        or reservation.purpose != "model_transport"
        or reservation.logical_key
        != "oe-structured-provider:" + str(member.iteration_intent_id)
        or BudgetVector.model_validate(reservation.amounts) != amount
    ):
        fail()
    read_physical_chain(
        session,
        fence,
        member,
        request,
        config.model_binding,
        settings,
        completed=response is not None,
    )
    if response is not None:
        response, _ = validate_completed_usage(request, response, config.model_binding)
        raw = response_bytes(response)
        if (
            response_id is None
            or read_research_object(
                session,
                fence,
                response_id,
                "oe_structured_provider_response",
                digest(response),
                settings,
            )
            != raw
        ):
            fail()
    elif response_id is not None:
        fail()
    settle_budget(
        session, fence, reservation.id, charge=amount, outcome="conservative_unknown"
    )
    state = "completed" if response is not None else "unknown"
    outcome = ResearchModelOutcome(
        id=uuid4(),
        owner_id=fence.owner_id,
        run_id=fence.run_id,
        attempt_id=fence.attempt_id,
        generation=fence.generation,
        transport_id=transport.id,
        outcome=state,
        usage=amount.model_dump(),
        response_object_id=response_id,
        response_object_kind=None
        if response is None
        else "oe_structured_provider_response",
        response_sha256=None if response is None else digest(response),
    )
    session.add(outcome)
    transport.state = state
    operation.state = state
    if response is None:
        session.flush()
        return None
    intent = scoped(
        session.get(OEStructuredIterationIntent, member.iteration_intent_id), fence
    )
    import json

    exchange = OEStructuredProviderExchange(
        id=uuid4(),
        owner_id=fence.owner_id,
        run_id=fence.run_id,
        attempt_id=fence.attempt_id,
        generation=fence.generation,
        epoch_id=intent.epoch_id,
        iteration_intent_id=intent.id,
        provider_transport_id=member.id,
        parent_candidate_id=intent.parent_candidate_id,
        context=json.loads(intent.context_canonical),
        context_canonical=intent.context_canonical,
        context_sha256=intent.context_sha256,
        request_object_id=operation.request_object_id,
        request_kind=operation.request_object_kind,
        request_sha256=request.sha256,
        response_object_id=response_id,
        response_kind="oe_structured_provider_response",
        response_sha256=digest(response),
    )
    session.add(exchange)
    session.flush()
    read_completed_response(session, fence, exchange, config, settings)
    return exchange.id


def submit_provider_chat(factory, fence, intent_id, wire_request, settings, *, peer):
    """Returns an immutable exchange ID and original response envelope, not candidate authority."""
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root),
    ):
        with neutral_transaction(
            factory, fence, resource_scope="factor_numeric"
        ) as session:
            bound = require_provider_operation(
                session, fence, intent_id, settings, peer=peer
            )
            if type(wire_request) is not bytes or wire_request != canonical_bytes(
                bound.request
            ):
                fail()
            previous = session.scalar(
                select(OEStructuredProviderExchange).filter_by(
                    iteration_intent_id=intent_id
                )
            )
            if previous is not None:
                _, response = read_completed_response(
                    session, fence, previous, bound.config, settings
                )
                return previous.id, response_bytes(response)
            if (
                session.scalar(
                    select(OEStructuredProviderTransport).filter_by(
                        iteration_intent_id=intent_id
                    )
                )
                is not None
            ):
                raise ValueError(
                    "Unknown existing SDK operation cannot be submitted again"
                )
            operator, binding = resolve_operator_profile(
                settings, fence.owner_id, bound.request.model_profile_id
            )
            if (
                binding != bound.config.model_binding
                or resolve_runtime(settings).runtime != bound.config.runtime
            ):
                fail()
        secret = os.environ.get(operator.connection.secret_env)
        if not secret:
            fail()
        key = "oe-provider:" + intent_id.hex
        request_id = publish_evidence(
            factory,
            fence,
            settings,
            "oe_structured_provider_request",
            wire_request,
            key + ":request",
        )
        with neutral_transaction(
            factory, fence, resource_scope="factor_numeric"
        ) as session:
            current = require_provider_operation(
                session, fence, intent_id, settings, peer=peer
            )
            if current != bound:
                fail()
            member, _operation, transport = admit_provider_transport(
                session, fence, intent_id, request_id, wire_request, settings, peer=peer
            )
            membership_id, transport_id = member.id, transport.id
        # peer's daemon observer carries the ORIGINAL trusted controller deadline;
        # a request cannot supply a longer timeout or refresh a started operation.
        original_deadline = peer._context.deadline
        if type(original_deadline) not in (int, float) or not math.isfinite(
            original_deadline
        ):
            fail()
        now = time.monotonic_ns()
        deadline_ns = min(
            int(original_deadline * 1_000_000_000),
            now + binding.request_deadline_ms * 1_000_000,
        )
        if deadline_ns <= now:
            fail()
        deadline = deadline_ns / 1_000_000_000
        if int(deadline * 1_000_000_000) > deadline_ns:
            deadline = math.nextafter(deadline, -math.inf)
        deadline_ns = int(deadline * 1_000_000_000)
        scope = {
            "owner_id": fence.owner_id,
            "run_id": fence.run_id,
            "attempt_id": fence.attempt_id,
            "generation": fence.generation,
            "transport_id": transport_id,
            "request_sha256": bound.request.sha256,
            "model_binding_sha256": binding.sha256,
        }
        intent = SDKIntent(
            **scope,
            admitted_monotonic_ns=now,
            deadline_monotonic_ns=deadline_ns,
            maximum_duration_ms=binding.request_deadline_ms,
            boot_id=UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip()),
        )
        retain_physical_receipt(factory, fence, settings, membership_id, intent, key)

        def physical_boundary(event, evidence):
            if event == "started":
                with neutral_transaction(
                    factory, fence, resource_scope="factor_numeric"
                ) as session:
                    if (
                        require_provider_operation(
                            session, fence, intent_id, settings, peer=peer
                        )
                        != bound
                    ):
                        fail()
                    current_operator, current_binding = resolve_operator_profile(
                        settings, fence.owner_id, binding.profile_id
                    )
                    if current_operator != operator or current_binding != binding:
                        fail()
                document = SDKStarted(**scope, identity=asdict(evidence))
            elif event == "stopped":
                document = SDKStopped(**scope, **asdict(evidence))
            else:
                fail()
            if (
                document.identity.boot_id != intent.boot_id
                or document.identity.deadline_monotonic_ns
                != intent.deadline_monotonic_ns
            ):
                fail()
            retain_physical_receipt(
                factory, fence, settings, membership_id, document, key
            )
            if event == "started":
                with neutral_transaction(
                    factory, fence, resource_scope="factor_numeric"
                ) as session:
                    if (
                        require_provider_operation(
                            session, fence, intent_id, settings, peer=peer
                        )
                        != bound
                    ):
                        fail()

        try:
            completion = complete_chat(
                bound.request,
                binding=binding,
                base_url=operator.connection.base_url,
                secret=secret,
                deadline_monotonic=deadline,
                identity_boundary=physical_boundary,
                python_executable=settings.oe_structured_provider_python,
            )
            response, _ = validate_completed_usage(
                bound.request, completion.response, binding
            )
            raw = response_bytes(response)
            oid = publish_evidence(
                factory,
                fence,
                settings,
                "oe_structured_provider_response",
                raw,
                key + ":response",
            )
            with neutral_transaction(
                factory, fence, resource_scope="factor_numeric"
            ) as session:
                exchange_id = _settle(
                    session,
                    fence,
                    membership_id,
                    settings,
                    response_id=oid,
                    response=response,
                )
            return exchange_id, raw
        except BaseException as original:
            try:
                with neutral_transaction(
                    factory, fence, resource_scope="factor_numeric"
                ) as session:
                    _settle(session, fence, membership_id, settings)
            except Exception:  # noqa: BLE001 - never invent identity/stop or release an uncertain hold
                original.add_note(
                    "Structured provider physical settlement remains uncertain"
                )
            raise

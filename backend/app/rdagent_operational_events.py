"""Protected-controller generation/source events using actual retained bytes.

Only explicit trusted host IPC handlers call these helpers. No HTTP/default worker
route, source execution, snapshot-success shortcut or caller-issued operation ID.
"""

import hashlib
import json
from uuid import uuid4

from sqlalchemy import select

from backend.app.core.maintenance import shared_lifecycle
from backend.app.rdagent_controller_services import require_rd_controller
from backend.app.rdagent_models import FactorUpstreamCall
from backend.app.rdagent_operational_broker import _publish
from backend.app.rdagent_services import (
    _call_members,
    _completed_member_response,
    _scope,
    _scope_row,
    begin_generation,
    require_research_spec,
)
from backend.app.rdagent_source_publication import (
    publish_reserved_source,
    reserve_source,
)
from backend.app.research_broker_models import ResearchOperationContext
from backend.app.research_broker_services import reserve_budget
from backend.app.research_lifecycle import neutral_transaction
from backend.app.services import DomainError
from packages.factors.contracts import digest
from packages.rdagent_adapter.host_evidence import (
    ModelResponsePart,
    ResponseAssemblyDocument,
)
from packages.rdagent_adapter.provider_bootstrap import RDProviderSeededBootstrap
from packages.rdagent_adapter.response_parser import parse_selected_responses
from packages.research_runtime.budget_contracts import BudgetVector


def start_generation(session, fence, peer, *, logical_step):
    launch = require_rd_controller(session, fence, peer)
    _, config = require_research_spec(session, fence)
    if type(logical_step) is not int or logical_step != 0 or config.max_iterations != 1:
        raise DomainError(
            "rd_operational_step", "Initial one-generation profile required", 409
        )
    document = {
        "schema_version": "mineagent.rdagent-context.v1",
        "spec_sha256": config.sha256,
        "input_sha256": config.input.sha256,
        "logical_step": 0,
        "task_index": 0,
    }
    sha = digest(document)
    context = session.scalar(
        select(ResearchOperationContext).filter_by(**_scope(fence), context_sha256=sha)
    )
    if context is None:
        context = ResearchOperationContext(
            id=uuid4(), **_scope(fence), context=document, context_sha256=sha
        )
        session.add(context)
        session.flush()
    elif context.context != document:
        raise DomainError("rd_operational_context", "Immutable context drift", 409)
    mutation = reserve_budget(
        session,
        fence,
        purpose="engine_mutation",
        logical_key="rd-operational-generation:0",
        amounts=BudgetVector(mutation_slots=1),
    )
    from packages.factors.contracts import canonical_bytes
    from packages.rdagent_adapter.seeded_bootstrap import (
        RDSeededOperationalBootstrap,
        parse_bootstrap,
    )

    if config.schema_version == "mineagent.factor-research.v3":
        from backend.app.rdagent_workflow_capability import require_workflow_revision
        from packages.rdagent_adapter.workflow_bootstrap import parse_workflow_bootstrap

        require_workflow_revision(session)
        bootstrap = parse_workflow_bootstrap(canonical_bytes(launch.bootstrap))
    else:
        bootstrap = parse_bootstrap(canonical_bytes(launch.bootstrap))
    seed_args = {}
    if config.schema_version == "mineagent.factor-research.v3":
        from backend.app.rdagent_workflow_models import FactorWorkflowSubmission

        row = session.scalar(
            select(FactorWorkflowSubmission).filter_by(
                owner_id=fence.owner_id, run_id=fence.run_id
            )
        )
        if row is None:
            raise DomainError(
                "workflow_initial_generation",
                "Workflow submission receipt required",
                409,
            )
        seed_args = {
            "workflow_submission_id": row.id,
            "parent_factor_version_id": None
            if bootstrap.seed is None
            else bootstrap.seed.reference.factor_version_id,
        }
    if isinstance(bootstrap, (RDSeededOperationalBootstrap, RDProviderSeededBootstrap)):
        seed_args = {
            "seeded_submission_id": bootstrap.seeded_submission_id,
            "parent_factor_version_id": bootstrap.seed.reference.factor_version_id,
        }
    return begin_generation(
        session,
        fence,
        logical_step=0,
        context_id=context.id,
        mutation_reservation_id=mutation.id,
        **seed_args,
    )


def publish_generated_source(
    factory, fence, peer, *, start_id, task_index, source, source_sha256, settings
):
    """Re-derive selected source from actual completed responses, then publish it.

    Runtime-supplied source text is an equality assertion, not provenance. Source
    publication re-reads/parses the same bytes again inside the existing witness
    path. No fake 'authenticated' controller precursor is inserted here.
    """
    if type(task_index) is not int or task_index != 0 or type(source) is not str:
        raise DomainError("rd_operational_source", "Exact fixed task required", 409)
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        launch = require_rd_controller(session, fence, peer)
        from backend.app.rdagent_models import FactorGenerationStart

        _scope_row(session, FactorGenerationStart, start_id, fence, lock=True)
        calls = list(
            session.scalars(
                select(FactorUpstreamCall)
                .filter_by(
                    **_scope(fence), start_id=start_id, phase="generation", kind="chat"
                )
                .limit(2)
            )
        )
        if len(calls) != 1:
            raise DomainError(
                "rd_operational_generation_call",
                "Exact generation exchange required",
                409,
            )
        call = calls[0]
        members = _call_members(session, fence, call.id)
        if not members:
            raise DomainError(
                "rd_operational_generation_call",
                "Completed response chain required",
                409,
            )
        first = max(
            i for i, member in enumerate(members) if member.relation != "continuation"
        )
        selected = members[first:]
        completed = [
            _completed_member_response(session, fence, member, settings)
            for member in selected
        ]
        _, config = require_research_spec(session, fence)
        provider = config.schema_version in (
            "mineagent.factor-research.v2",
            "mineagent.factor-research.v3",
        )
        expected_protocol = (
            "mineagent.rdagent-provider-chat.v1"
            if provider
            else "mineagent.rdagent-chat.v1"
        )
        if any(member.protocol != expected_protocol for member in selected):
            raise DomainError(
                "rd_operational_source_protocol",
                "Source response protocol differs from the frozen specification",
                409,
            )
        parser = parse_selected_responses
        if provider:
            from packages.rdagent_provider.source_parser import (
                parse_selected_provider_responses,
            )

            parser = parse_selected_provider_responses
        parsed = parser(tuple(response for _, response in completed))
        if (source, source_sha256) != (parsed.source, parsed.source_sha256):
            raise DomainError(
                "rd_operational_source_drift",
                "Runtime source differs from retained responses",
                409,
            )
        document = ResponseAssemblyDocument(
            call_id=call.id,
            orchestrator_admission_id=launch.admission_id,
            parts=tuple(
                ModelResponsePart(
                    member_id=member.id,
                    outcome_id=outcome.id,
                    response_object_id=outcome.response_object_id,
                    response_sha256=outcome.response_sha256,
                )
                for member, (outcome, _) in zip(selected, completed, strict=True)
            ),
            source=parsed.source,
            source_sha256=parsed.source_sha256,
        )
        call_id = call.id
    raw = json.dumps(
        document.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    assembly_id = _publish(
        factory,
        fence,
        settings,
        "factor_response_assembly",
        raw,
        "rd-operational-assembly:" + str(call_id),
    )
    with shared_lifecycle(factory.kw["bind"], settings.storage_root):
        with neutral_transaction(
            factory, fence, resource_scope="factor_objects"
        ) as session:
            require_rd_controller(session, fence, peer)
            publication = reserve_source(
                session,
                fence,
                call_id=call_id,
                assembly_object_id=assembly_id,
                assembly_sha256=hashlib.sha256(raw).hexdigest(),
                settings=settings,
            )
            publication_id = publication.id
        # Truthful durable response/source retention must survive child exit;
        # this existing writer retains current fence and conservative byte quota.
        return publish_reserved_source(
            factory,
            fence,
            publication_id=publication_id,
            content=parsed.source.encode("utf-8"),
            settings=settings,
        )

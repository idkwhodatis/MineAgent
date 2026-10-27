"""Maintained RD source persistence fixture for revision 018, never controller proof.

A real fenced RD run, ledger, immutable object files and source publisher are used.
Only the controller admission and closed-call boundary are controlled SQL/ORM
precursors: no IPC, kernel process, upstream/provider execution or PIT claim is
fabricated. Fixed local response production counts one synthetic fixture token
per response (not a tokenizer, monetary cost or paid-provider usage assertion).
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import null, select

from backend.app.core.atomic_storage import provision_directory
from backend.app.core.maintenance import shared_lifecycle
from backend.app.factor_models import FactorObject, FactorVersion
from backend.app.models import ExecutionAttempt, ResearchRun, utcnow
from backend.app.rdagent_controller_services import (
    bind_controller_launch,
    expected_operational_bootstrap,
)
from backend.app.rdagent_models import (
    FactorGenerationSource,
    FactorResearchSpec,
    FactorUpstreamCallOutcome,
)
from backend.app.rdagent_services import (
    admit_atomic_request,
    begin_generation,
    bind_research_attempt,
    open_upstream_call,
)
from backend.app.rdagent_source_publication import (
    publish_reserved_source,
    reserve_source,
    settle_source_conservatively,
    source_storage_guard,
)
from backend.app.research_broker_models import (
    ResearchBudgetControl,
    ResearchOperationContext,
)
from backend.app.research_broker_services import (
    initialize_budget,
    mark_submitted,
    reserve_budget,
    settle_transport,
)
from backend.app.research_lifecycle import neutral_transaction
from backend.app.research_object_models import ResearchObject
from backend.app.research_object_services import publish_reserved_object, reserve_object
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessState,
)
from packages.factors.contracts import DateWindow, digest, source_sha256
from packages.factors.fixtures import demo_fixture
from packages.rdagent_adapter.contracts import FactorResearchConfig, RuntimePins
from packages.rdagent_adapter.host_evidence import (
    ModelResponsePart,
    ResponseAssemblyDocument,
)
from packages.research_runtime.broker_contracts import ObjectRef
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.engine_broker_contracts import (
    RDChatResponse,
    canonical_bytes,
    parse_request,
)
from packages.research_runtime.fences import AttemptFence

PROTOCOL = "mineagent.rdagent-chat.v1"
PROVENANCE = "maintained_fixture_precursor_not_authenticated_controller_execution"


@dataclass(frozen=True)
class PersistedSource:
    fence: AttemptFence
    source: object
    version: FactorVersion
    codeobject: FactorObject
    spec: FactorResearchSpec
    attemptbinding: object
    publication_id: UUID
    controller_id: UUID
    call_id: UUID
    member_ids: tuple[UUID, ...]
    outcome_ids: tuple[UUID, ...]
    response_object_ids: tuple[UUID, ...]
    provenance: str = PROVENANCE


def _config(
    dataset,
    *,
    runtime: RuntimePins | None = None,
    budget: BudgetVector | None = None,
    window: DateWindow | None = None,
):
    if window is not None:
        if type(window) is not DateWindow:
            raise ValueError("Exact immutable DateWindow required")
        window = DateWindow.model_validate(window.model_dump())
    if runtime is not None:
        if type(runtime) is not RuntimePins:
            raise ValueError("Exact immutable RuntimePins required")
        runtime = RuntimePins.model_validate(runtime)
    if budget is not None:
        if type(budget) is not BudgetVector:
            raise ValueError("Exact immutable BudgetVector required")
        # BudgetVector does not revalidate instances; reconstruct the fields.
        budget = BudgetVector.model_validate(budget.model_dump())

    sample = demo_fixture(
        factor_version_id=uuid4(), dataset_version_id=dataset.id
    ).manifest.input
    dataset_binding = sample.dataset.model_dump(mode="json")
    dataset_binding["content_sha256"] = dataset.content_sha256
    return FactorResearchConfig.model_validate(
        {
            "input": {
                "dataset": dataset_binding,
                "calculation_universe": sample.calculation_universe.model_dump(
                    mode="json"
                ),
                "window": (window or sample.window).model_dump(mode="json"),
                "calendar_sha256": sample.calendar_sha256,
                "market_timezone": sample.market_timezone,
                "input_fields": [
                    x.model_dump(mode="json") for x in sample.definition.input_fields
                ],
                "lookback_sessions": 2,
                "lag_sessions": 0,
            },
            "task": {
                "name": "maintained-source",
                "description": PROVENANCE,
                "formulation": "Source retention only; never executed here",
                "factor_kind": "time_series",
                "output_unit": "return",
                "interpretation": "unsigned",
            },
            "runtime": runtime.model_dump(mode="json")
            if runtime is not None
            else {
                "dependency_locks": [
                    {"role": role, "sha256": "a" * 64}
                    for role in ("analysis", "candidate", "normalizer", "orchestrator")
                ],
                **{
                    role + "_image": "sha256:" + "b" * 64
                    for role in ("analysis", "candidate", "normalizer", "orchestrator")
                },
            },
            "budget": budget.model_dump()
            if budget is not None
            else BudgetVector(
                calls=8,
                tokens=100000,
                mutation_slots=1,
                evaluator_executions=1,
                wall_milliseconds=120000,
                storage_bytes=16 * 1024**2,
                process_slots=1,
            ).model_dump(),
            "max_iterations": 1,
            "max_output_rows_per_cutoff": 10000,
            "max_output_bytes_per_cutoff": 16777216,
        }
    )


def _scope(fence):
    return {
        key: getattr(fence, key)
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    }


def _publish(factory, fence, settings, kind, content):
    with neutral_transaction(factory, fence, resource_scope="objects") as session:
        held = reserve_budget(
            session,
            fence,
            purpose="object_storage",
            logical_key=uuid4().hex,
            amounts=BudgetVector(storage_bytes=2 * len(content) + 8192),
        )
        publication = reserve_object(session, fence, kind, content, held.id, settings)
        publication_id = publication.id
    object_id = publish_reserved_object(
        factory, fence, publication_id, content, settings
    )
    with factory() as session:
        row = session.get(ResearchObject, object_id)
        assert row is not None and row.sha256 == hashlib.sha256(content).hexdigest()
        session.expunge(row)
        return row


def persist_maintained_source(
    factory,
    *,
    owner,
    dataset,
    settings,
    source_utf8,
    runtime: RuntimePins | None = None,
    budget: BudgetVector | None = None,
    window: DateWindow | None = None,
):
    """owner is UUID; dataset is an existing same-owner DatasetVersion row.

    Returns durable source bindings usable by later tests, not execution grants.
    Default runtime hashes are storage-only placeholders, never runnable pins.
    For composed numeric tests, the test caller must supply exact RuntimePins from
    measured image IDs and dependency inventories. Revalidation is not admission
    authority, and no caller-provided image is launched by this helper.
    Optional exact BudgetVector covers measured composed-test reservation peaks;
    it is revalidated and still subject to FactorResearchConfig resource bounds.
    Requires the real revision018 schema. No fallback DDL, mocks or strategy ID.
    Controller/mutation reservations stay held; source gets conservative full-peak
    settlement. This fixture intentionally does not finalize or claim run success.
    """
    if (
        type(owner) is not UUID
        or dataset.owner_id != owner
        or type(source_utf8) is not bytes
    ):
        raise ValueError("Exact owner/dataset/UTF8 bytes required")
    source_text = source_utf8.decode("utf-8", errors="strict")
    source_hash = source_sha256(source_text)
    raw_reply = json.dumps(
        {"code": source_text}, ensure_ascii=False, separators=(",", ":")
    )
    chunks = tuple(raw_reply[i : i + 32768] for i in range(0, len(raw_reply), 32768))
    if not 1 <= len(chunks) <= 6:
        raise ValueError("Fixture response exceeds six bounded continuation parts")
    config = _config(dataset, runtime=runtime, budget=budget, window=window)
    for directory in ("factors", "research-objects"):
        provision_directory(
            settings.storage_root / directory, durable_parent=settings.storage_root
        )
    run_id, attempt_id, token = uuid4(), uuid4(), uuid4()
    fence = AttemptFence(owner, run_id, attempt_id, 1, token)
    with factory.begin() as session:
        run = ResearchRun(
            id=run_id,
            owner_id=owner,
            idempotency_key=uuid4().hex,
            request_sha256=config.sha256,
            config=config.model_dump(mode="json"),
            purpose="factor_research",
            engine="rdagent",
            dataset_version_id=dataset.id,
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
                lease_expires_at=utcnow() + timedelta(hours=1),
                status="running",
                sandbox_id="not-launched:" + attempt_id.hex,
                sandbox_stopped=False,
            )
        )
        session.flush()
        run.active_attempt_id = attempt_id
        spec = FactorResearchSpec(
            id=uuid4(),
            owner_id=owner,
            run_id=run_id,
            config=config.model_dump(mode="json"),
            config_sha256=config.sha256,
            input_sha256=config.input.sha256,
        )
        session.add(spec)
        session.add(
            ResearchBudgetControl(
                id=uuid4(),
                owner_id=owner,
                run_id=run_id,
                limits=config.budget.model_dump(),
                used=BudgetVector().model_dump(),
                reserved=BudgetVector().model_dump(),
            )
        )
    with neutral_transaction(factory, fence, resource_scope="processes") as session:
        initialize_budget(session, fence, config.budget)
        binding = bind_research_attempt(session, fence)
        binding_id = binding.id
        _, bootstrap = expected_operational_bootstrap(session, fence)
        held = reserve_budget(
            session,
            fence,
            purpose="process_unit",
            logical_key="controlled-controller:" + binding.id.hex,
            amounts=BudgetVector(
                process_slots=1, wall_milliseconds=60000, storage_bytes=1179648
            ),
        )
        controller = ResearchProcessAdmission(
            id=uuid4(),
            **_scope(fence),
            role="rd_orchestrator",
            logical_operation_id=binding.id,
            launch_ordinal=1,
            profile="mineagent.rdagent-orchestrator.v1",
            input_sha256=bootstrap.sha256,
            reservation_id=held.id,
            image_id=config.runtime.orchestrator_image,
            schema_version="mineagent.process-admission.v2",
            ipc_policy="host_ticket",
            ticket_sha256=hashlib.sha256(
                (PROVENANCE + str(binding.id)).encode()
            ).hexdigest(),
        )
        session.add(controller)
        session.flush()
        session.add(
            ResearchProcessState(
                admission_id=controller.id, **_scope(fence), status="admitted"
            )
        )
        launch = bind_controller_launch(session, fence, admission_id=controller.id)
        controller_id, launch_id = controller.id, launch.id
        context_payload = {
            "schema_version": "mineagent.rdagent-context.v1",
            "spec_sha256": config.sha256,
            "input_sha256": config.input.sha256,
            "logical_step": 0,
            "task_index": 0,
        }
        context = ResearchOperationContext(
            id=uuid4(),
            **_scope(fence),
            context=context_payload,
            context_sha256=digest(context_payload),
        )
        session.add(context)
        session.flush()
        mutation = reserve_budget(
            session,
            fence,
            purpose="engine_mutation",
            logical_key="maintained-source-step:0",
            amounts=BudgetVector(mutation_slots=1),
        )
        start = begin_generation(
            session,
            fence,
            logical_step=0,
            context_id=context.id,
            mutation_reservation_id=mutation.id,
        )
        call = open_upstream_call(
            session,
            fence,
            start_id=start.id,
            call_id=uuid4(),
            phase="generation",
            kind="chat",
        )
        call_id = call.id
    parts = []
    for ordinal, chunk in enumerate(chunks):
        request = parse_request(
            PROTOCOL,
            {
                "messages": [
                    {"role": "user", "content": f"{PROVENANCE}; part {ordinal}"}
                ],
                "max_output_tokens": 4096,
                "response_format": {"type": "json_object"},
            },
        )
        request_bytes = canonical_bytes(PROTOCOL, request)
        request_object = _publish(
            factory, fence, settings, "model_request", request_bytes
        )
        with neutral_transaction(factory, fence) as session:
            _, member, transport = admit_atomic_request(
                session,
                fence,
                call_id=call_id,
                idempotency_key=uuid4(),
                relation="initial" if ordinal == 0 else "continuation",
                request_bytes=request_bytes,
                request_object_id=request_object.id,
                settings=settings,
            )
            member_id, transport_id = member.id, transport.id
        with neutral_transaction(factory, fence) as session:
            mark_submitted(session, fence, transport_id)
        response = RDChatResponse(
            content=chunk,
            finish_reason="stop" if ordinal == len(chunks) - 1 else "length",
        )
        response_bytes = canonical_bytes(PROTOCOL, response)
        response_object = _publish(
            factory, fence, settings, "model_response", response_bytes
        )
        with neutral_transaction(factory, fence) as session:
            outcome = settle_transport(
                session,
                fence,
                transport_id,
                outcome="completed",
                usage=BudgetVector(calls=1, tokens=1),
                response=response,
                response_object=ObjectRef(
                    id=response_object.id,
                    kind="model_response",
                    sha256=response_object.sha256,
                ),
            )
            parts.append(
                ModelResponsePart(
                    member_id=member_id,
                    outcome_id=outcome.id,
                    response_object_id=response_object.id,
                    response_sha256=response_object.sha256,
                )
            )
    with neutral_transaction(factory, fence) as session:
        # Controlled historical precursor ONLY: never call authenticated_* with a mock peer.
        session.add(
            FactorUpstreamCallOutcome(
                id=uuid4(),
                **_scope(fence),
                call_id=call_id,
                controller_launch_id=launch_id,
                status="completed",
                member_count=len(parts),
                member_ids=[str(p.member_id) for p in parts],
                response_outcome_ids=[str(p.outcome_id) for p in parts],
            )
        )
    assembly = ResponseAssemblyDocument(
        call_id=call_id,
        orchestrator_admission_id=controller_id,
        parts=tuple(parts),
        source=source_text,
        source_sha256=source_hash,
    )
    obj = _publish(
        factory,
        fence,
        settings,
        "factor_response_assembly",
        json.dumps(
            assembly.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8"),
    )
    with neutral_transaction(
        factory, fence, resource_scope="factor_objects"
    ) as session:
        publication = reserve_source(
            session,
            fence,
            call_id=call_id,
            assembly_object_id=obj.id,
            assembly_sha256=obj.sha256,
            settings=settings,
        )
        publication_id = publication.id
    publish_reserved_source(
        factory,
        fence,
        publication_id=publication_id,
        content=source_utf8,
        settings=settings,
    )
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        source_storage_guard(settings.storage_root),
        neutral_transaction(factory, fence, resource_scope="factor_objects") as session,
    ):
        settle_source_conservatively(
            session, fence, publication_id=publication_id, settings=settings
        )
    from backend.app.rdagent_models import (
        FactorResearchAttemptBinding,
        FactorSourcePublicationOutcome,
    )

    with factory() as session:
        outcome = session.scalar(
            select(FactorSourcePublicationOutcome).where(
                FactorSourcePublicationOutcome.publication_id == publication_id
            )
        )
        source = session.get(FactorGenerationSource, outcome.source_id)
        version = session.get(FactorVersion, source.factor_version_id)
        codeobject = session.get(FactorObject, version.code_object_id)
        spec = session.scalar(
            select(FactorResearchSpec).where(FactorResearchSpec.run_id == run_id)
        )
        binding = session.get(FactorResearchAttemptBinding, binding_id)
        for row in (source, version, codeobject, spec, binding):
            session.expunge(row)
    return PersistedSource(
        fence,
        source,
        version,
        codeobject,
        spec,
        binding,
        publication_id,
        controller_id,
        call_id,
        tuple(p.member_id for p in parts),
        tuple(p.outcome_id for p in parts),
        tuple(p.response_object_id for p in parts),
    )

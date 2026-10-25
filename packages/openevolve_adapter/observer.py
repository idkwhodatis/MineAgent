"""Fail-stop primitives for versioned upstream patches.

This is control-flow containment, not durable history or kernel stop authority.
Host operation receipts and whole-unit stop proof remain mandatory.
"""

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

Phase = Literal[
    "submission",
    "result_application",
    "seed_admission",
    "checkpoint_load",
    "checkpoint_publication",
    "evaluation_timeout",
    "worker_observation",
    "candidate_registration",
]
PHASES = frozenset(
    {
        "submission",
        "result_application",
        "seed_admission",
        "checkpoint_load",
        "checkpoint_publication",
        "evaluation_timeout",
        "worker_observation",
        "candidate_registration",
    }
)


class SearchControlAbort(BaseException):
    """Pickle-safe terminal signal, intentionally outside Exception fallbacks."""

    def __init__(self, phase: Phase):
        if phase not in PHASES:
            raise ValueError("Unknown search control phase")
        self.phase = phase
        super().__init__(phase)


class CandidateSourceRejected(Exception):
    """Closed ordinary candidate rejection, never an authorization failure."""

    def __init__(self, reason):
        if reason not in {"source_profile", "host_rejected"}:
            raise ValueError("Unknown candidate rejection")
        self.reason = reason
        super().__init__(reason)


class CandidateEvaluationRejected(Exception):
    """Host-recorded ineligible objective, never a synthetic population score."""

    def __init__(self, reason="native_objective_ineligible"):
        if reason != "native_objective_ineligible":
            raise ValueError("Unknown evaluation rejection")
        super().__init__(reason)


@dataclass
class EpochControl:
    epoch_id: UUID
    population_state_trusted: bool = True
    processed_frontier: int = -1
    pending_checkpoints: dict = field(default_factory=dict)


_CURRENT: ContextVar[EpochControl | None] = ContextVar(
    "mineagent_search_epoch", default=None
)


@contextmanager
def epoch_control(epoch_id: UUID):
    if type(epoch_id) is not UUID or _CURRENT.get() is not None:
        raise ValueError("Explicit nonnested epoch context required")
    state = EpochControl(epoch_id)
    token = _CURRENT.set(state)
    try:
        yield state
    finally:
        _CURRENT.reset(token)


def abort_control(phase: Phase, error: Exception | None = None):
    # Error payloads may contain model output or credentials: never persist/log
    # them here. A host observer records its own bounded closed failure event.
    if phase not in PHASES:
        raise ValueError("Unknown search control phase")
    state = _CURRENT.get()
    if state is not None:
        state.population_state_trusted = False
    raise SearchControlAbort(phase) from None


def require_trusted_population():
    state = _CURRENT.get()
    if state is None or not state.population_state_trusted:
        raise SearchControlAbort("checkpoint_publication")
    return state.epoch_id


@contextmanager
def control_guard(phase: Phase):
    if phase not in PHASES:
        raise ValueError("Unknown search control phase")
    try:
        yield
    except (CandidateSourceRejected, CandidateEvaluationRejected) as error:
        if phase == "candidate_registration":
            raise
        abort_control(phase, error)
    except Exception as error:  # noqa: BLE001 - every unexpected mutation error is terminal
        abort_control(phase, error)
    except BaseException:
        state = _CURRENT.get()
        if state is not None:
            state.population_state_trusted = False
        raise


_WORKER_STACK: ContextVar[ExitStack | None] = ContextVar(
    "mineagent_search_worker_stack", default=None
)
_PROMPT: ContextVar[dict | None] = ContextVar("mineagent_search_prompt", default=None)

_WORKER: ContextVar[dict | None] = ContextVar("mineagent_search_worker", default=None)
_EVALUATION: ContextVar[dict | None] = ContextVar(
    "mineagent_search_evaluation", default=None
)


def before_submit(iteration, parent_id, inspiration_ids, snapshot):
    from .observer_transport import host_event

    if (
        type(iteration) is not int
        or iteration < 1
        or type(parent_id) is not str
        or type(inspiration_ids) is not list
        or type(snapshot) is not dict
    ):
        abort_control("submission")
    # The host resolves every selected native ID against its exact candidate
    # mapping and validates the entire projected snapshot before issuing a grant.
    return host_event(
        "iteration_intent",
        {
            "iteration": iteration,
            "parent_id": parent_id,
            "inspiration_ids": inspiration_ids,
            "snapshot": snapshot,
        },
    )


@contextmanager
def worker_scope(descriptor):
    from .observer_transport import bootstrap, host_event

    required = {
        "schema_version",
        "admission_id",
        "epoch_id",
        "intent_id",
        "intent_ticket",
        "native_iteration",
        "parent_candidate_id",
        "parent_source_sha256",
        "selection_sha256",
    }
    if (
        type(descriptor) is not dict
        or set(descriptor) != required
        or descriptor["schema_version"] != "mineagent.openevolve-iteration-grant.v1"
    ):
        abort_control("worker_observation")
    initial = bootstrap()
    if (
        descriptor["admission_id"] != initial["admission_id"]
        or descriptor["epoch_id"] != initial["epoch_id"]
        or _WORKER.get() is not None
    ):
        abort_control("worker_observation")
    for key in ("intent_id", "parent_candidate_id"):
        if str(UUID(descriptor[key])) != descriptor[key]:
            abort_control("worker_observation")
    for key in ("intent_ticket", "parent_source_sha256", "selection_sha256"):
        if (
            type(descriptor[key]) is not str
            or len(descriptor[key]) != 64
            or any(c not in "0123456789abcdef" for c in descriptor[key])
        ):
            abort_control("worker_observation")
    if (
        type(descriptor["native_iteration"]) is not int
        or descriptor["native_iteration"] < 1
    ):
        abort_control("worker_observation")
    token = _WORKER.set(descriptor)
    with ExitStack() as stack:
        stack_token = _WORKER_STACK.set(stack)
        try:
            with epoch_control(UUID(descriptor["epoch_id"])):
                host_event("worker_started", {"grant": descriptor})
                yield
        finally:
            _WORKER_STACK.reset(stack_token)
            _WORKER.reset(token)


def worker_outcome(result):
    from .observer_transport import host_event

    descriptor = _WORKER.get()
    if descriptor is None:
        abort_control("worker_observation")
    host_event(
        "worker_outcome",
        {
            "grant": descriptor,
            "iteration": result.iteration,
            "outcome": "worker_error" if result.error is not None else "evaluated",
            "child": result.child_program_dict,
            "parent_id": result.parent_id,
            "target_island": result.target_island,
        },
    )


def proposal_response(response, token_usage):
    from .observer_transport import host_event
    from .runtime_profile import PROFILE

    if response is not None and (
        type(response) is not str
        or len(response.encode()) > PROFILE.maximum_response_bytes
    ):
        abort_control("worker_observation")
    host_event("proposal_response", {"grant": _WORKER.get(), "response": response})


def proposal_source(stage, source):
    import hashlib

    from .observer_transport import host_event
    from .runtime_profile import PROFILE

    if stage not in ("applied", "enforced") or type(source) is not str:
        abort_control("worker_observation")
    raw = source.encode("utf-8")
    within_bound = len(raw) <= PROFILE.maximum_source_bytes
    # A rejected proposal is evidence, not an accepted truncated candidate. The
    # original bounded model response is separately retained by its callback.
    host_event(
        "proposal_source",
        {
            "grant": _WORKER.get(),
            "stage": stage,
            "source": source if within_bound else None,
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "source_bytes": len(raw),
            "evidence": "complete_source" if within_bound else "oversize_source_digest",
        },
    )


@contextmanager
def candidate_scope(program_id, program_code):
    from .observer_transport import host_event
    from .runtime_profile import validate_source_bound

    if (
        type(program_id) is not str
        or str(UUID(program_id)) != program_id
        or type(program_code) is not str
        or _EVALUATION.get() is not None
    ):
        abort_control("candidate_registration")
    try:
        validate_source_bound(program_code.encode())
    except ValueError:
        if _WORKER.get() is None:
            abort_control("seed_admission")
        raise CandidateSourceRejected("source_profile") from None
    ticket = host_event(
        "candidate_intent",
        {
            "grant": _WORKER.get(),
            "upstream_program_id": program_id,
            "source": program_code,
        },
    )
    import hashlib

    from .evaluator_protocol import EvaluationGrant
    from .observer_transport import bootstrap, canonical

    grant = EvaluationGrant.model_validate_json(canonical(ticket))
    initial = bootstrap()
    if (
        str(grant.owner_id) != initial["spec"]["owner_id"]
        or str(grant.run_id) != initial["spec"]["run_id"]
        or str(grant.epoch_id) != initial["epoch_id"]
        or str(grant.upstream_program_id) != program_id
        or grant.source_sha256 != hashlib.sha256(program_code.encode()).hexdigest()
        or grant.phase != ("seed" if _WORKER.get() is None else "mutation")
    ):
        abort_control("candidate_registration")
    token = _EVALUATION.set({"grant": ticket, "result": None})
    try:
        yield
    finally:
        _EVALUATION.reset(token)


def evaluation_ticket():
    ticket = _EVALUATION.get()
    if ticket is None:
        abort_control("candidate_registration")
    return ticket["grant"]


def assert_seed_source(program_code):
    import hashlib

    from .contracts import SearchSpec
    from .observer_transport import bootstrap, canonical, host_event
    from .runtime_profile import validate_source_bound

    initial = bootstrap()
    spec = SearchSpec.model_validate_json(canonical(initial["spec"]))
    validate_source_bound(program_code.encode())
    if (
        hashlib.sha256(program_code.encode()).hexdigest()
        != spec.preparation.prepared.source_sha256
    ):
        abort_control("seed_admission")
    host_event(
        "seed_source", {"source_sha256": spec.preparation.prepared.source_sha256}
    )


@contextmanager
def prompt_scope():
    if _PROMPT.get() is not None or _WORKER.get() is None:
        abort_control("worker_observation")
    token = _PROMPT.set(
        {
            "references": [],
            "current_source_sha256": None,
            "artifact_section_sha256": None,
        }
    )
    try:
        yield
    finally:
        _PROMPT.reset(token)


def prompt_reference(role, program):
    import hashlib

    from .runtime_profile import validate_source_bound

    current = _PROMPT.get()
    if (
        current is None
        or role not in ("previous_summary", "top", "diverse", "inspiration")
        or type(program) is not dict
        or type(program.get("id")) is not str
        or type(program.get("code")) is not str
    ):
        abort_control("worker_observation")
    validate_source_bound(program["code"].encode())
    if len(current["references"]) >= 16:
        abort_control("worker_observation")
    current["references"].append(
        {
            "role": role,
            "upstream_program_id": program["id"],
            "source_sha256": hashlib.sha256(program["code"].encode()).hexdigest(),
            "representation": "summary_without_source_code"
            if role == "previous_summary"
            else "source_code",
        }
    )


def prompt_current_source(source):
    import hashlib

    from .runtime_profile import validate_source_bound

    current = _PROMPT.get()
    if current is None or type(source) is not str:
        abort_control("worker_observation")
    validate_source_bound(source.encode())
    current["current_source_sha256"] = hashlib.sha256(source.encode()).hexdigest()


def prompt_artifacts(rendered_section):
    import hashlib

    current = _PROMPT.get()
    if (
        current is None
        or type(rendered_section) is not str
        or len(rendered_section.encode()) > 32768
    ):
        abort_control("worker_observation")
    current["artifact_section_sha256"] = hashlib.sha256(
        rendered_section.encode()
    ).hexdigest()


def prompt_complete(system, user):
    import hashlib

    from .broker_client import OperationGrant, bind_operation
    from .contracts import SearchOperationContext
    from .observer_transport import bootstrap, canonical, host_event, process_ticket
    from .runtime_profile import PROFILE

    current, descriptor, stack = _PROMPT.get(), _WORKER.get(), _WORKER_STACK.get()
    if (
        current is None
        or descriptor is None
        or stack is None
        or any(
            type(x) is not str or not x or len(x) > PROFILE.maximum_message_characters
            for x in (system, user)
        )
    ):
        abort_control("worker_observation")
    projection = {"system": system, "user": user, **current}
    if len(canonical(projection)) > PROFILE.maximum_request_bytes:
        abort_control("worker_observation")
    rendered_sha = hashlib.sha256(canonical(projection)).hexdigest()
    answer = host_event(
        "rendered_prompt",
        {
            "grant": descriptor,
            "projection": projection,
            "rendered_sha256": rendered_sha,
        },
    )
    if set(answer) != {"context", "context_ticket"}:
        abort_control("worker_observation")
    context = SearchOperationContext.model_validate_json(canonical(answer["context"]))
    spec = bootstrap()["spec"]
    if (
        str(context.owner_id) != spec["owner_id"]
        or str(context.run_id) != spec["run_id"]
        or str(context.epoch_id) != descriptor["epoch_id"]
        or str(context.iteration_intent_id) != descriptor["intent_id"]
        or str(context.parent_candidate_id) != descriptor["parent_candidate_id"]
        or context.parent_source_sha256 != descriptor["parent_source_sha256"]
        or context.selection_snapshot_sha256 != descriptor["selection_sha256"]
        or context.rendered_context_sha256 != rendered_sha
        or context.native_iteration != descriptor["native_iteration"]
        or context.operation_ordinal != 0
    ):
        abort_control("worker_observation")
    request = {
        "kind": "chat",
        "model": PROFILE.model_profile,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_output_tokens": PROFILE.maximum_output_tokens,
        "temperature_millionths": 0,
    }
    request_sha = hashlib.sha256(canonical(request)).hexdigest()
    stack.enter_context(
        bind_operation(
            OperationGrant(
                UUID(descriptor["admission_id"]),
                context,
                request_sha,
                process_ticket(),
                answer["context_ticket"],
            )
        )
    )


def _state_digest(native_state):
    import hashlib

    from .observer_transport import canonical

    if type(native_state) is not dict or len(canonical(native_state)) > 786432:
        raise ValueError("Native state exceeds fixed observer projection bound")
    return hashlib.sha256(canonical(native_state)).hexdigest()


def _checkpoint_path(path):
    from pathlib import Path

    if type(path) is not str:
        raise ValueError("Checkpoint path must be a fixed-root capability")
    parsed = Path(path)
    if (
        parsed.parent != Path("/work/checkpoints")
        or str(UUID(parsed.name)) != parsed.name
        or str(parsed) != path
        or any(p.is_symlink() for p in (parsed, *parsed.parents))
    ):
        raise ValueError("Invalid checkpoint destination")
    return parsed


def terminal_applied(iteration, outcome, native_state):
    """Advance the local frontier only after exact authoritative host ACK."""
    from .observer_transport import host_event

    with control_guard("result_application"):
        require_trusted_population()
        state = _CURRENT.get()
        if (
            type(iteration) is not int
            or iteration != state.processed_frontier + 1
            or outcome
            not in ({"seed"} if iteration == 0 else {"accepted", "rejected", "error"})
        ):
            raise ValueError("Noncontiguous native application")
        state_sha = _state_digest(native_state)
        answer = host_event(
            "terminal_applied",
            {
                "iteration": iteration,
                "outcome": outcome,
                "native_state": native_state,
                "state_sha256": state_sha,
            },
        )
        if (
            answer != {"frontier": iteration, "state_sha256": state_sha}
            or type(answer["frontier"]) is not int
        ):
            raise ValueError("Terminal application acknowledgement differs")
        state.processed_frontier = iteration


def before_checkpoint(frontier, native_state):
    from .observer_transport import host_event

    with control_guard("checkpoint_publication"):
        require_trusted_population()
        state = _CURRENT.get()
        if (
            type(frontier) is not int
            or frontier < 0
            or frontier != state.processed_frontier
        ):
            raise ValueError("Checkpoint is not the acknowledged frontier")
        state_sha = _state_digest(native_state)
        answer = host_event(
            "checkpoint_prepare",
            {
                "frontier": frontier,
                "state_sha256": state_sha,
            },
        )
        if (
            set(answer) != {"token", "path"}
            or str(UUID(answer["token"])) != answer["token"]
        ):
            raise ValueError("Invalid checkpoint capability")
        destination = _checkpoint_path(answer["path"])
        if answer["token"] in state.pending_checkpoints:
            raise ValueError("Duplicate checkpoint capability")
        # No arbitrary directories, overwrite or retry of an uncertain write.
        destination.mkdir(mode=0o700)
        state.pending_checkpoints[answer["token"]] = (
            frontier,
            state_sha,
            answer["path"],
        )
        return answer


def after_checkpoint(token, frontier, native_state):
    from .observer_transport import host_event

    with control_guard("checkpoint_publication"):
        require_trusted_population()
        state = _CURRENT.get()
        expected = state.pending_checkpoints.get(token)
        state_sha = _state_digest(native_state)
        if (
            expected is None
            or expected[:2] != (frontier, state_sha)
            or frontier != state.processed_frontier
        ):
            raise ValueError("Checkpoint state drifted during serialization")
        # Host separately verifies closed file inventory, writer stability and
        # current fence. This acknowledgement is NOT whole-unit stop evidence.
        answer = host_event(
            "checkpoint_publish",
            {
                "token": token,
                "frontier": frontier,
                "state_sha256": state_sha,
                "path": expected[2],
            },
        )
        if (
            answer
            != {"published": True, "frontier": frontier, "state_sha256": state_sha}
            or answer["published"] is not True
            or type(answer["frontier"]) is not int
        ):
            raise ValueError("Checkpoint publication acknowledgement differs")
        del state.pending_checkpoints[token]


def before_checkpoint_load(path):
    from .observer_transport import host_event

    with control_guard("checkpoint_load"):
        require_trusted_population()
        state = _CURRENT.get()
        if state.processed_frontier != -1 or state.pending_checkpoints:
            raise ValueError("Restore requires pristine epoch state")
        source = _checkpoint_path(path)
        if not source.is_dir():
            raise ValueError("Missing checkpoint cannot fall back to a new seed")
        answer = host_event("checkpoint_load_prepare", {"path": path})
        if (
            set(answer) != {"token", "path", "frontier", "state_sha256"}
            or str(UUID(answer["token"])) != answer["token"]
            or answer["path"] != path
            or type(answer["frontier"]) is not int
            or answer["frontier"] < 0
            or type(answer["state_sha256"]) is not str
            or len(answer["state_sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in answer["state_sha256"])
        ):
            raise ValueError("Invalid checkpoint load capability")
        state.pending_checkpoints[answer["token"]] = dict(answer)
        return answer


def after_checkpoint_load(descriptor, native_state):
    from .observer_transport import host_event

    with control_guard("checkpoint_load"):
        require_trusted_population()
        state = _CURRENT.get()
        if (
            type(descriptor) is not dict
            or state.pending_checkpoints.get(descriptor.get("token")) != descriptor
        ):
            raise ValueError("Unknown checkpoint load capability")
        state_sha = _state_digest(native_state)
        if state_sha != descriptor["state_sha256"] or state.processed_frontier != -1:
            raise ValueError("Native load repaired or changed persisted state")
        answer = host_event(
            "checkpoint_load_complete", {**descriptor, "state_sha256": state_sha}
        )
        if (
            answer != {"loaded": True, "frontier": descriptor["frontier"]}
            or answer["loaded"] is not True
            or type(answer["frontier"]) is not int
        ):
            raise ValueError("Checkpoint restore acknowledgement differs")
        state.processed_frontier = descriptor["frontier"]
        del state.pending_checkpoints[descriptor["token"]]


def record_evaluation_result(response):
    from .evaluator_protocol import EvaluationResponse

    current = _EVALUATION.get()
    if (
        current is None
        or current["result"] is not None
        or type(response) is not EvaluationResponse
    ):
        abort_control("candidate_registration")
    # Context-copy thread sees this same private per-evaluation record; no
    # ContextVar write propagation from an executor thread is assumed.
    current["result"] = response


def evaluation_complete(metrics):
    from .observer_transport import host_event

    current = _EVALUATION.get()
    if current is None or current["result"] is None:
        abort_control("candidate_registration")
    response = current["result"]
    expected = {"combined_score": response.combined_score} if response.eligible else {}
    if (
        type(metrics) is not dict
        or metrics != expected
        or (
            response.eligible
            and type(metrics.get("combined_score")) not in (int, float)
        )
    ):
        abort_control("candidate_registration")
    answer = host_event(
        "evaluation_complete",
        {
            "grant": current["grant"],
            "objective_receipt_id": str(response.objective_receipt_id),
            "eligible": response.eligible,
            "metrics": metrics,
        },
    )
    if (
        answer
        != {
            "acknowledged": True,
            "objective_receipt_id": str(response.objective_receipt_id),
        }
        or answer["acknowledged"] is not True
    ):
        abort_control("candidate_registration")
    if not response.eligible:
        raise CandidateEvaluationRejected("native_objective_ineligible")
    return metrics

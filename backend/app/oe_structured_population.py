"""Actual027 sampling population versus retained completed engine outcomes.

No caller score, native artifact prose, or decoded candidate is execution
permission. Only the current authenticated sampling cursor may use this reader.
"""

import hashlib
from uuid import UUID

from sqlalchemy import select

from backend.app.oe_structured_engine_results import read_population_engine_outcome
from backend.app.oe_structured_engine_services import SCOPE, fail
from backend.app.oe_structured_models import OEStructuredCandidate
from backend.app.oe_structured_operational_models import (
    OEStructuredControlState,
    OEStructuredIterationIntent,
)
from backend.app.research_lifecycle import require_neutral_transaction
from packages.factors.contracts import canonical_bytes
from packages.openevolve_structured.checkpoint import (
    _json,
    _normalize,
    canonical,
    state_sha256,
)
from packages.openevolve_structured.objective import StructuredMeasurement


def measurement_feedback(measurement):
    """Exact native artifact text; data formatting is not a disclosure grant."""
    if type(measurement) is not StructuredMeasurement:
        fail("oe_structured_measurement_type")
    raw = canonical_bytes(measurement)
    if StructuredMeasurement.model_validate_json(raw) != measurement:
        fail("oe_structured_measurement_drift")
    return raw.decode("ascii")


def _scope(row, fence, epoch_id):
    if (
        row is None
        or row.epoch_id != epoch_id
        or any(getattr(row, key) != getattr(fence, key) for key in SCOPE)
    ):
        fail("oe_structured_population_scope")
    return row


def _state(row, domain):
    raw = row.state_canonical.encode("utf-8")
    if not 0 < len(raw) <= 786432:
        fail("oe_structured_population_size")
    value = _json(raw)
    if type(value) is not dict or set(value) != {"metadata", "programs"}:
        fail("oe_structured_population_shape")
    state = _normalize(value["metadata"], value["programs"], domain)
    if canonical(state) != raw or state_sha256(state) != row.state_sha256:
        fail("oe_structured_population_canonical")
    if state["metadata"]["last_iteration"] != row.frontier:
        fail("oe_structured_population_frontier")
    return state


def _chain(rows, fence, intent, domain):
    if not rows or len(rows) > 5:
        fail("oe_structured_population_chain")
    states = []
    previous = None
    for sequence, row in enumerate(rows, 1):
        _scope(row, fence, intent.epoch_id)
        phase = (
            "seed"
            if sequence == 1
            else ("sampling" if sequence % 2 == 0 else "applied")
        )
        if (
            row.sequence != sequence
            or row.phase != phase
            or row.frontier != (sequence - 1) // 2
            or row.previous_id != (None if previous is None else previous.id)
        ):
            fail("oe_structured_population_chain")
        state = _state(row, domain)
        if phase == "sampling" and (
            state["programs"] != states[-1]["programs"]
            or state["metadata"]["last_iteration"]
            != states[-1]["metadata"]["last_iteration"]
        ):
            fail("oe_structured_population_sampling_changed_programs")
        states.append(state)
        previous = row
    if (
        rows[-1].id != intent.sampling_state_id
        or rows[-1].phase != "sampling"
        or rows[-1].sequence != 2 * intent.ordinal
        or rows[-1].frontier != intent.ordinal - 1
    ):
        fail("oe_structured_population_stale_worker")
    return states[-2], states[-1]


def _selection(intent, before, parent):
    raw = intent.selection_canonical.encode("ascii")
    if not 0 < len(raw) <= 65536:
        fail("oe_structured_selection_size")
    value = _json(raw)
    if (
        type(value) is not dict
        or set(value)
        != {
            "schema_version",
            "native_iteration",
            "parent_id",
            "inspiration_ids",
            "snapshot",
        }
        or value["schema_version"] != "mineagent.oe-structured-selection.v1"
        or type(value["native_iteration"]) is not int
        or value["native_iteration"] != intent.ordinal
        or value["parent_id"] != str(parent.upstream_program_id)
        or canonical_bytes(value) != raw
        or hashlib.sha256(raw).hexdigest() != intent.selection_sha256
    ):
        fail("oe_structured_selection_envelope")
    inspirations = value["inspiration_ids"]
    if (
        type(inspirations) is not list
        or len(inspirations) > 3
        or any(
            type(pid) is not str
            or str(UUID(pid)) != pid
            or pid not in before["programs"]
            for pid in inspirations
        )
    ):
        fail("oe_structured_selection_inspiration")
    artifacts = {
        pid: _json(program["artifacts_json"])
        for pid, program in before["programs"].items()
        if program["artifacts_json"] and _json(program["artifacts_json"])
    }
    snapshot = value["snapshot"]
    if (
        type(snapshot) is not dict
        or set(snapshot)
        != {
            "programs",
            "islands",
            "current_island",
            "feature_dimensions",
            "artifacts",
            "sampling_island",
        }
        or type(snapshot["current_island"]) is not int
        or snapshot["current_island"] != 0
        or type(snapshot["sampling_island"]) is not int
        or snapshot["sampling_island"] != 0
        or snapshot["feature_dimensions"] != ["complexity", "diversity"]
        or canonical(snapshot["programs"]) != canonical(before["programs"])
        or canonical(snapshot["artifacts"]) != canonical(artifacts)
        or type(snapshot["islands"]) is not list
        or len(snapshot["islands"]) != 1
        or type(snapshot["islands"][0]) is not list
        or sorted(snapshot["islands"][0]) != before["metadata"]["islands"][0]
        or str(parent.upstream_program_id) not in before["programs"]
    ):
        fail("oe_structured_selection_snapshot")


def require_selection(session, fence, intent, config, settings, *, peer):
    """Fresh bounded actual control chain and every prompt-visible result member."""
    from backend.app.oe_structured_controller_context import require_controller_context

    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    actual = session.get(OEStructuredIterationIntent, intent.id)
    if actual is None or any(
        getattr(actual, key) != getattr(intent, key)
        for key in (
            *SCOPE,
            "epoch_id",
            "sampling_state_id",
            "ordinal",
            "parent_candidate_id",
            "selection_canonical",
            "selection_sha256",
        )
    ):
        fail("oe_structured_selection_intent")
    context = require_controller_context(
        session, fence, intent.epoch_id, settings, peer=peer, intent_id=intent.id
    )
    if context.config != config:
        fail("oe_structured_selection_config")
    rows = list(
        session.scalars(
            select(OEStructuredControlState)
            .filter_by(epoch_id=intent.epoch_id)
            .order_by(OEStructuredControlState.sequence)
            .limit(6)
        )
    )
    before, _ = _chain(rows, fence, intent, config.search.domain)
    candidates = list(
        session.scalars(
            select(OEStructuredCandidate).filter_by(epoch_id=intent.epoch_id).limit(4)
        )
    )
    if len(candidates) > 3:
        fail("oe_structured_population_count")
    by_native = {}
    by_id = {}
    for row in candidates:
        _scope(row, fence, intent.epoch_id)
        key = str(row.upstream_program_id)
        if key in by_native or row.id in by_id:
            fail("oe_structured_population_duplicate")
        by_native[key], by_id[row.id] = row, row
    parent = by_id.get(intent.parent_candidate_id)
    if parent is None:
        fail("oe_structured_selection_parent")
    _selection(intent, before, parent)
    verified_programs = {}
    for pid, program in before["programs"].items():
        candidate = by_native.get(pid)
        if candidate is None or not 0 <= candidate.ordinal < intent.ordinal:
            fail("oe_structured_population_member")
        parent_row = by_id.get(candidate.parent_candidate_id)
        if (
            program["code"] != candidate.candidate_canonical
            or hashlib.sha256(program["code"].encode()).hexdigest()
            != candidate.candidate_sha256
            or program["iteration_found"] != candidate.ordinal
            or program["parent_id"]
            != (None if parent_row is None else str(parent_row.upstream_program_id))
            or (candidate.ordinal > 0 and parent_row is None)
        ):
            fail("oe_structured_population_program")
        verified = read_population_engine_outcome(
            session,
            fence,
            candidate.id,
            settings,
            peer=peer,
            current_intent_id=intent.id,
        )
        measurement = verified.measurement
        expected_metrics = (
            {"combined_score": measurement.combined_score}
            if measurement.eligible
            else {}
        )
        if (
            verified.candidate_id != candidate.id
            or measurement.structured_strategy_version_id
            != candidate.program_version_id
            or canonical(program["metrics"]) != canonical(expected_metrics)
            or program["artifacts_json"] is None
            or _json(program["artifacts_json"])
            != {"structured_feedback": measurement_feedback(measurement)}
        ):
            fail("oe_structured_population_accounting")
        verified_programs[pid] = (
            candidate,
            expected_metrics,
            measurement_feedback(measurement),
        )
    # This bounded initial profile has at most three programs and capacity eight.
    # Historical control cuts cannot smuggle scores/artifacts later overwritten
    # before the current cursor. Each distinct member was physically reread above.
    for row in rows:
        state = _state(row, config.search.domain)
        for pid, program in state["programs"].items():
            if pid not in verified_programs:
                fail("oe_structured_population_lost_member")
            candidate, metrics, feedback = verified_programs[pid]
            if (
                candidate.ordinal > row.frontier
                or program["code"] != candidate.candidate_canonical
                or canonical(program["metrics"]) != canonical(metrics)
                or program["artifacts_json"] is None
                or _json(program["artifacts_json"]) != {"structured_feedback": feedback}
            ):
                fail("oe_structured_population_historical_accounting")
    # No late cursor/selection replacement may authorize an old socket worker.
    latest = session.scalar(
        select(OEStructuredControlState)
        .filter_by(epoch_id=intent.epoch_id)
        .order_by(OEStructuredControlState.sequence.desc())
        .limit(1)
    )
    if latest is None or latest.id != intent.sampling_state_id:
        fail("oe_structured_population_stale_worker")
    refreshed = require_controller_context(
        session, fence, intent.epoch_id, settings, peer=peer, intent_id=intent.id
    )
    if refreshed.config != config:
        fail("oe_structured_selection_config")

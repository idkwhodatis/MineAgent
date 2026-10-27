"""Initial-epoch event/worker closure; never resume or stopped-unit authority."""


def validate_worker_closure(epoch, rows, pending_intent_ids, open_assignment_ids):
    def indexed(name):
        values = rows[name]
        result = {value.id: value for value in values}
        if len(result) != len(values):
            raise ValueError("Duplicate durable closure row")
        return result

    controls = indexed("control_transition_ids")
    intents = indexed("intent_ids")
    events = indexed("controller_event_receipt_ids")
    tickets = indexed("worker_ticket_ids")
    expected = indexed("worker_expected_identity_ids")
    assignments = indexed("worker_assignment_ids")
    closures = indexed("worker_assignment_closure_ids")
    outcomes = indexed("worker_outcome_ids")
    if len(events) != len(controls) or {x.control_id for x in events.values()} != set(
        controls
    ):
        raise ValueError("Every control cursor requires exact controller event receipt")
    for event in events.values():
        control = controls[event.control_id]
        if (
            event.admission_id != epoch.admission_id
            or event.control_sequence != control.sequence
            or event.control_frontier != control.frontier
            or event.control_state_sha256 != control.state_sha256
            or event.command_sha256 != control.request_sha256
            or event.intent_id != control.sampling_intent_id
            or event.terminal_id != control.terminal_id
        ):
            raise ValueError("Controller event/control identity differs")
        if control.kind == "sampling":
            if (
                event.event_kind != "iteration_intent"
                or event.intent_id not in intents
                or event.native_iteration != intents[event.intent_id].native_iteration
            ):
                raise ValueError("Sampling event identity differs")
        elif (
            event.event_kind != "terminal_applied"
            or event.native_iteration != control.frontier
        ):
            raise ValueError("Terminal event identity differs")
    by_intent = {x.intent_id: x for x in tickets.values()}
    expected_by_intent = {x.intent_id: x for x in expected.values()}
    if (
        len(by_intent) != len(tickets)
        or set(by_intent) != set(intents)
        or len(expected_by_intent) != len(expected)
        or set(expected_by_intent) != set(intents)
    ):
        raise ValueError("Charged intent lacks exact ticket and acknowledged placement")
    for ticket in tickets.values():
        event = events.get(ticket.controller_receipt_id)
        if (
            ticket.admission_id != epoch.admission_id
            or event is None
            or event.event_kind != "iteration_intent"
            or event.intent_id != ticket.intent_id
        ):
            raise ValueError("Worker ticket sampling provenance differs")
    for identity in expected.values():
        if (
            identity.admission_id != epoch.admission_id
            or identity.ticket_id != by_intent[identity.intent_id].id
        ):
            raise ValueError("Expected worker ticket/unit differs")
    assigned = {x.intent_id: x for x in assignments.values()}
    if len(assigned) != len(assignments) or not set(assigned) <= set(intents):
        raise ValueError("Worker assignment intent differs")
    for assignment in assignments.values():
        identity = expected_by_intent[assignment.intent_id]
        if (
            assignment.admission_id != epoch.admission_id
            or assignment.ticket_id != identity.ticket_id
            or assignment.expected_identity_id != identity.id
            or any(
                getattr(assignment, k) != getattr(identity, k)
                for k in ("host_pid", "start_ticks", "pid_namespace", "cgroup")
            )
        ):
            raise ValueError("Assigned worker differs from expected kernel identity")
    closed = {x.assignment_id: x for x in closures.values()}
    outcome_by_intent = {x.intent_id: x for x in outcomes.values()}
    if (
        len(closed) != len(closures)
        or not set(closed) <= set(assignments)
        or len(outcome_by_intent) != len(outcomes)
    ):
        raise ValueError("Assignment closure identity differs")
    for closure in closures.values():
        assignment = assignments[closure.assignment_id]
        outcome = outcomes.get(closure.worker_outcome_id)
        if (
            outcome is None
            or closure.intent_id != assignment.intent_id
            or outcome.intent_id != assignment.intent_id
            or closure.native_iteration
            != intents[assignment.intent_id].native_iteration
        ):
            raise ValueError("Assignment closure outcome differs")
    if set(outcome_by_intent) != {assignments[x].intent_id for x in closed}:
        raise ValueError("Worker outcome and assignment closure must be atomic")
    open_ids = set(assignments) - set(closed)
    if (
        set(open_assignment_ids) != open_ids
        or len(open_ids) > 1
        or any(assignments[x].intent_id not in pending_intent_ids for x in open_ids)
    ):
        raise ValueError("Open assignment exceeds exact pending-tail policy")
    if not (set(intents) - set(assigned)) <= set(pending_intent_ids):
        raise ValueError("Applied intent lacks assigned worker")
    ordered = sorted(assigned, key=lambda identity: intents[identity].native_iteration)
    if [intents[x].native_iteration for x in ordered] != list(
        range(1, len(ordered) + 1)
    ):
        raise ValueError("Single-worker assignments skipped a prior charged intent")
    if any(assigned[x].id not in closed for x in ordered[:-1]):
        raise ValueError("Pool worker reused before prior assignment closure")
    return True

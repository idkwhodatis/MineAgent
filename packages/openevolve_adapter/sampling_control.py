"""Phase7 sampling-state acknowledgement; separate from frozen phase6 runtime.

The native sampler still runs exactly once. This callback neither samples nor
rolls back its state; failed/uncertain acknowledgement poisons the epoch before
executor submission. Applied frontier K and control-state cursor are distinct.
"""

from contextvars import ContextVar
from uuid import UUID

from . import observer
from .checkpoint_native import _normalize, state_sha256
from .observer_transport import host_event

_LAST_ACK: ContextVar[tuple | None] = ContextVar("mineagent_sampling_ack", default=None)


def before_submit(
    iteration, parent_id, inspiration_ids, snapshot, before_state, after_state
):
    with observer.control_guard("submission"):
        epoch = observer.require_trusted_population()
        if (
            type(iteration) is not int
            or not 1 <= iteration <= 2
            or type(parent_id) is not str
            or str(UUID(parent_id)) != parent_id
            or type(inspiration_ids) is not list
            or any(type(x) is not str or str(UUID(x)) != x for x in inspiration_ids)
        ):
            raise ValueError("Invalid actual native sampling identities")
        for value in (before_state, after_state):
            if type(value) is not dict or set(value) != {"metadata", "programs"}:
                raise ValueError("Invalid native sampling state")
        before = _normalize(before_state["metadata"], before_state["programs"])
        after = _normalize(after_state["metadata"], after_state["programs"])
        if (
            before["metadata"]["last_iteration"] != after["metadata"]["last_iteration"]
            or parent_id not in after["programs"]
        ):
            raise ValueError("Sampling cannot advance applied frontier")
        previous = _LAST_ACK.get()
        answer = host_event(
            "iteration_intent",
            {
                "iteration": iteration,
                "parent_id": parent_id,
                "inspiration_ids": inspiration_ids,
                "snapshot": snapshot,
                "sampling": {
                    "schema_version": "mineagent.native-sampling-transition.v1",
                    "before_state_sha256": state_sha256(before),
                    "after_state": after,
                },
            },
        )
        if (
            type(answer) is not dict
            or set(answer)
            != {
                "iteration_grant",
                "sampling_receipt_id",
                "control_sequence",
                "state_sha256",
            }
            or str(UUID(answer["sampling_receipt_id"])) != answer["sampling_receipt_id"]
            or type(answer["control_sequence"]) is not int
            or not 2 <= answer["control_sequence"] <= 5
            or answer["state_sha256"] != state_sha256(after)
        ):
            raise ValueError("Sampling acknowledgement differs")
        if (
            previous is not None
            and previous[0] == epoch
            and answer["control_sequence"] <= previous[1]
        ):
            raise ValueError("Sampling acknowledgement cursor did not advance")
        grant = answer["iteration_grant"]
        if (
            type(grant) is not dict
            or grant.get("schema_version") != "mineagent.openevolve-iteration-grant.v1"
            or grant.get("epoch_id") != str(epoch)
            or type(grant.get("native_iteration")) is not int
            or grant.get("native_iteration") != iteration
        ):
            raise ValueError("Sampling acknowledgement intent differs")
        _LAST_ACK.set((epoch, answer["control_sequence"]))
        return grant

"""Configured-only rejection result dispatch; old successful frame stays exact."""

from packages.rdagent_adapter.numeric_failure import decode_failure_frame
from runtimes.rdagent_operational.client import decode_evaluation_frame
from runtimes.rdagent_operational.rpc import RPCError


def decode_provider_evaluation(result, *, source_id, evaluation_ticket):
    if (
        type(result) is dict
        and result.get("schema_version")
        == "mineagent.rdagent-numeric-rejection-frame.v1"
    ):
        try:
            observed = decode_failure_frame(
                result, source_id=source_id, evaluation_id=evaluation_ticket
            )
        except ValueError:
            raise RPCError("numeric_rejection_frame") from None
        # This is the upstream evaluator's genuine execution-failure input. It
        # must not be an empty DataFrame, invented values, a snapshot or IC=0.
        return observed.feedback_text, None
    feedback, rows = decode_evaluation_frame(
        result, source_id=source_id, evaluation_ticket=evaluation_ticket
    )
    import pandas as pd

    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp(row.event_time), row.listing_id) for row in rows],
        names=["datetime", "instrument"],
    )
    return feedback, pd.DataFrame({"factor": [row.value for row in rows]}, index=index)

"""Production DTO-shaped scalar probes, not process or result authority."""

from pathlib import Path

from packages.factors.contracts import digest
from packages.qlib_adapter.objective import DirectObjectiveMeasurement
from tests.qlib.test_analysis_journal import records as analysis_records
from tests.qlib.test_direct_wire import inputs, result
from tests.rdagent.test_rdagent_controller_process import records as controller_records


def predicate_cases():
    launch, identity, child, stop = controller_records(Path("/tmp"))
    analysis, analysis_identity, analysis_stop, extraction = analysis_records()
    data = inputs()
    answer = result(data)
    measured = DirectObjectiveMeasurement(
        declaration_sha256="a" * 64,
        protocol_sha256=data.protocol.sha256,
        result_sha256=digest(answer),
        status="failed",
        reason="threshold_not_satisfied",
        value=answer.ic,
        defined_days=answer.defined_days,
        total_pairs=answer.total_pairs,
        defined_pairs=5,
    )
    cases = []
    for function, values in (
        (
            "mineagent_rd_controller_payload",
            [
                ("launch_intent", launch.intent),
                ("launch_identity", identity),
                ("controller_child", child),
                ("stop", stop),
            ],
        ),
        (
            "mineagent_analysis_payload",
            [
                ("launch_intent", analysis.intent),
                ("launch_identity", analysis_identity),
                ("stop", analysis_stop),
                ("extraction", extraction),
            ],
        ),
        (
            "mineagent_direct_contract",
            [
                ("protocol", data.protocol),
                ("labels", data.protocol.label_manifest),
                ("result", answer),
                ("measurement", measured),
            ],
        ),
    ):
        for kind, model in values:
            value = model.model_dump(mode="json")
            cases.append((function, kind, value, True))
            cases.append((function, kind, {**value, "unreviewed": True}, False))
            missing = value.copy()
            del missing["schema_version"]
            cases.append((function, kind, missing, False))
    for key in (
        "confirmed_absent",
        "kernel_identity_absent",
        "child_kernel_identity_absent",
    ):
        for invalid in (False, 1, "true"):
            cases.append(
                (
                    "mineagent_rd_controller_payload",
                    "stop",
                    {**stop.model_dump(mode="json"), key: invalid},
                    False,
                )
            )
    for key in ("confirmed_absent", "kernel_identity_absent", "deadline_bound_met"):
        for invalid in (False, 1, "true"):
            cases.append(
                (
                    "mineagent_analysis_payload",
                    "stop",
                    {**analysis_stop.model_dump(mode="json"), key: invalid},
                    key == "deadline_bound_met" and invalid is False,
                )
            )
    for key in ("producer_quiesced", "protected_frame_complete"):
        for invalid in (False, 1, "true"):
            cases.append(
                (
                    "mineagent_analysis_payload",
                    "extraction",
                    {**extraction.model_dump(mode="json"), key: invalid},
                    False,
                )
            )
    for invalid in (True, "0.5", 1.01, -1.01):
        cases.append(
            (
                "mineagent_direct_contract",
                "result",
                {**answer.model_dump(mode="json"), "ic": invalid},
                False,
            )
        )
        cases.append(
            (
                "mineagent_direct_contract",
                "measurement",
                {**measured.model_dump(mode="json"), "value": invalid},
                False,
            )
        )
    return cases

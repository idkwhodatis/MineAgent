"""Re-read protected native bytes and exact persisted stop/extraction receipts.

These are native strategy-shared-interpreter observations, never independent
financial truth. Missing/invalid output cannot be converted into a successful
bundle supplied by a caller or into a fabricated engine timeout diagnosis.
"""

import base64
import json

from backend.app.openevolve_native_process import persisted, resolve_native_input
from packages.openevolve_adapter.observer_transport import canonical, unique
from packages.tradegrad_adapter.independent_results import (
    RESULT_PREFIX_V3,
    validate_envelope,
)
from runtimes.openevolve_evaluator.output_reader import (
    read_native_journal,
    read_stopped_native_bytes,
)


def decode_native_files(files):
    if set(files) != {"metrics.json", "stdout.bin", "stderr.bin"}:
        raise ValueError("Exact native exported files required")
    records = [
        line[len(RESULT_PREFIX_V3) :]
        for line in files["stdout.bin"].decode("utf-8").splitlines()
        if line.startswith(RESULT_PREFIX_V3)
    ]
    if len(records) != 1:
        raise ValueError("Exactly one native observation envelope required")
    envelope = validate_envelope(base64.b64decode(records[0], validate=True))
    metrics = json.loads(
        files["metrics.json"],
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(
            ValueError("Nonfinite native metrics")
        ),
    )
    if canonical(metrics) != canonical(envelope["metrics"]):
        raise ValueError("Native metrics file differs from exported envelope")
    return {
        "schema_version": "mineagent.native-observation-object.v1",
        "bundle": envelope["bundle"],
    }


def read_native_evidence(session, fence, admission_id, settings):
    intent, state, receipts = persisted(session, fence, admission_id)
    evaluation, _manifest, _runtime = resolve_native_input(
        session, fence, intent, settings
    )
    if (
        state.status != "stopped"
        or "stop" not in receipts
        or "launch_identity" not in receipts
    ):
        raise ValueError("Native evidence requires exact persisted positive stop")
    launch, identity, stop, _extraction = read_native_journal(
        settings.storage_root, intent.admission
    )
    if (
        launch.intent != intent
        or receipts["stop"].payload != stop.model_dump(mode="json")
        or receipts["launch_identity"].payload != identity.model_dump(mode="json")
    ):
        raise ValueError("Native physical journal differs from retained receipts")
    if "extraction" not in receipts:
        return receipts["stop"].id, {
            "schema_version": "mineagent.native-failure-object.v1",
            "evaluation_id": str(evaluation.id),
            "failure_code": "invalid_result",
        }
    physical = read_stopped_native_bytes(settings.storage_root, intent.admission)
    if (
        physical.stop != stop
        or physical.identity != identity
        or receipts["extraction"].payload != physical.extraction.model_dump(mode="json")
    ):
        raise ValueError(
            "Native extraction receipt differs from actual retained export"
        )
    # Only parsing already-verified opaque bytes may produce invalid_result.
    # Filesystem/journal/fence failures above remain uncertainty and propagate.
    try:
        observed = decode_native_files(physical.files)
    except ValueError:
        observed = {
            "schema_version": "mineagent.native-failure-object.v1",
            "evaluation_id": str(evaluation.id),
            "failure_code": "invalid_result",
        }
    return receipts["stop"].id, observed

"""Terminal stop predicates consume exact typed persisted receipt chains."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_rdagent_controller_process import records

from backend.app.rdagent_controller_stop import controller_stop_committed
from packages.research_runtime.process_contracts import digest
from packages.research_runtime.process_persistence_codec import encode_row


def fixture(tmp_path):
    launch, identity, child, stop = records(tmp_path)
    row = SimpleNamespace(**encode_row(launch.intent.admission))
    scope = {
        k: getattr(row, k) for k in ("owner_id", "run_id", "attempt_id", "generation")
    }
    state = SimpleNamespace(status="stopped", **scope)
    rows = [
        SimpleNamespace(
            kind=kind,
            payload=value.model_dump(mode="json"),
            payload_sha256=digest(value.model_dump(mode="json")),
            **scope,
        )
        for kind, value in (
            ("launch_intent", launch.intent),
            ("launch_identity", identity),
            ("controller_child", child),
            ("stop", stop),
        )
    ]
    session = SimpleNamespace(
        get=lambda *a: state, scalars=lambda *a: SimpleNamespace(all=lambda: rows)
    )
    return row, state, rows, session


def test_exact_stop(tmp_path):
    row, _, _, session = fixture(tmp_path)
    assert controller_stop_committed(session, row)


@pytest.mark.parametrize(
    "drift", ["state", "scope", "hash", "duplicate", "missing", "identity", "child"]
)
def test_drift_never_proves_stop(tmp_path, drift):
    row, state, rows, session = fixture(tmp_path)
    if drift == "state":
        state.status = "running"
    elif drift == "scope":
        rows[-1].owner_id = uuid4()
    elif drift == "hash":
        rows[-1].payload_sha256 = "0" * 64
    elif drift == "duplicate":
        rows.append(rows[-1])
    elif drift == "missing":
        rows.pop()
    else:
        rows[-1].payload[
            "identity_sha256" if drift == "identity" else "child_sha256"
        ] = "0" * 64
        rows[-1].payload_sha256 = digest(rows[-1].payload)
    assert not controller_stop_committed(session, row)

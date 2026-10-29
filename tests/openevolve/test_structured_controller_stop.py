"""Exact typed persisted stop/completion chain, not kernel/PG observation."""

import json
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_controller_stop as module
from packages.research_runtime.process_contracts import digest
from packages.research_runtime.process_persistence_codec import encode_row
from runtimes.openevolve_structured.trusted_report import report_bytes
from runtimes.openevolve_structured_host.completion_journal import observed_completion
from tests.structured_controller_runtime.fixtures import records


def case(root, count=4):
    launch, identity, child, stop = records(root)
    admission = launch.intent.admission
    if count == 3:
        child = None
        stop = stop.model_copy(
            update={"child_sha256": None, "child_kernel_identity_absent": None}
        )
    docs = {"launch_intent": launch.intent, "launch_identity": identity, "stop": stop}
    if child:
        docs["controller_child"] = child
    if count == 5:
        docs["controller_completion"] = observed_completion(
            launch,
            identity,
            child,
            stop,
            reports=[
                json.loads(report_bytes("started", 2, 1001)),
                json.loads(report_bytes("quiescent", 2, 1001, 0)),
            ],
            reports_eof=True,
            daemon_exit_code=124,
            daemon_oom_killed=False,
            deadline_ns=100_000_000_000,
            kernel_absent_ns=100_100_000_000,
        )
    scope = {
        k: getattr(admission, k)
        for k in ("owner_id", "run_id", "attempt_id", "generation")
    }
    rows = [
        NS(
            **scope,
            kind=kind,
            payload=value.model_dump(mode="json"),
            payload_sha256=digest(value.model_dump(mode="json")),
        )
        for kind, value in docs.items()
    ]
    state = NS(**scope, status="stopped")
    session = NS(get=lambda *a: state, scalars=lambda q: NS(all=lambda: rows))
    return NS(row=NS(**encode_row(admission)), rows=rows, state=state, session=session)


@pytest.mark.parametrize("count", [3, 4, 5])
def test_containment_does_not_require_or_fabricate_completion(tmp_path, count):
    c = case(tmp_path, count)
    assert module.controller_stop_committed(c.session, c.row)
    assert len(c.rows) == count


@pytest.mark.parametrize(
    "change",
    ["scope", "hash", "missing", "duplicate", "state", "profile", "completion"],
)
def test_wrong_chain_not_containment(tmp_path, change):
    c = case(tmp_path, 5)
    if change == "scope":
        c.rows[0].owner_id = uuid4()
    if change == "hash":
        c.rows[0].payload_sha256 = "0" * 64
    if change == "missing":
        c.rows.pop(0)
    if change == "duplicate":
        c.rows.append(c.rows[0])
    if change == "state":
        c.state.status = "running"
    if change == "profile":
        c.row.profile = "mineagent.openevolve-orchestrator.v1"
    if change == "completion":
        row = next(r for r in c.rows if r.kind == "controller_completion")
        row.payload["reports_eof"] = False
        row.payload_sha256 = digest(row.payload)
    assert not module.controller_stop_committed(c.session, c.row)


def test_generic_unit_summary_dispatches_only_exact_new_profile(tmp_path, monkeypatch):
    from backend.app import research_process_services as shared

    c = case(tmp_path)
    original = c.session.scalars

    def rows(query):
        model = query.column_descriptions[0]["entity"]
        return (
            NS(all=lambda: [c.row])
            if model.__name__ == "ResearchProcessAdmission"
            else original(query)
        )

    c.session.scalars = rows
    result = shared.reconcile_attempt_units(
        c.session, c.row.run_id, c.row.attempt_id, c.row.generation
    )
    assert result.confirmed

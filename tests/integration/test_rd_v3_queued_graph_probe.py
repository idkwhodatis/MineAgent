"""Actual API-admitted first workflow child, metadata only; no native execution."""

import json
import time
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from backend.app import rd_v3_invariant_probe_sql as probe
from tests.integration.test_row_validation_invariants import (
    call_outcome,
    catalog,
    execute_sql,
    queued_provider_request,
)


def test_current_head_workflow_queued_graph_equivalence_and_commit_cost(
    api,
    migrated_engine,
    settings,
    make_account,
    monkeypatch,
    capsys,
):
    from rd_configured_dataset_fixture import persist_configured_dataset
    from rd_submission_fixture import login

    from workers.research.workflow_tick import drive_one

    owner = make_account("owner")
    factory = sessionmaker(migrated_engine, expire_on_commit=False)
    dataset, _, policy = persist_configured_dataset(factory, owner.user.id, settings)
    settings.rdagent_research_enabled = True
    settings.factor_library_enabled = True
    request = queued_provider_request(
        dataset, policy, owner.user.id, settings, monkeypatch
    )
    budget = request.budget.model_dump(mode="json")
    raw = {
        "schema_version": "mineagent.workflow-public-request.v1",
        "research": request.model_dump(mode="json"),
        "mode": "continuous",
        "maximum_iterations": 2,
        "budget_ceiling": {
            k: v * 2 + (65536 if k == "storage_bytes" else 0) for k, v in budget.items()
        },
        "refs": [],
        "external_definition": None,
        "allow_workflow_development_feedback_release": False,
    }
    headers = login(api, owner)
    response = api.post(
        "/api/v1/research-workflows/preflight", headers=headers, json=raw
    )
    assert response.status_code == 200, response.text
    response = api.post(
        "/api/v1/research-workflows",
        headers=headers,
        json={
            "schema_version": "mineagent.workflow-public-submit.v1",
            "idempotency_key": "rd-v3-queued-proof",
            "request": raw,
            "preflight_sha256": response.json()["preflight_sha256"],
        },
    )
    assert response.status_code == 201, response.text
    wid = UUID(response.json()["workflow_id"])
    outcome = drive_one(factory, settings, owner_id=owner.user.id, workflow_id=wid)
    assert outcome.state == "child_queued"
    with migrated_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT run_id,id FROM factor_workflow_submissions WHERE workflow_id=:w"
            ),
            {"w": wid},
        ).one()
        rid, sid = row
        assert c.execute(
            text(
                "SELECT status,config->>'schema_version',config->'workflow_parent' FROM research_runs WHERE id=:r"
            ),
            {"r": rid},
        ).one() == ("queued", "mineagent.factor-research.v3", None)
        original_catalog = catalog(c)
        c.rollback()
        cases = [
            (probe.CHANGED[0], str(rid)),
            (probe.CHANGED[1], str(rid)),
            (probe.CHANGED[2], str(sid)),
        ]
        expected = {case: call_outcome(c, *case) for case in cases}
        assert set(expected.values()) == {("ok", None)}
        c.rollback()
        samples = {}
        for phase in ("baseline", "candidate", "restored"):
            if phase != "baseline":
                with c.begin():
                    execute_sql(
                        c,
                        probe.render_candidate_sql(
                            "upgrade" if phase == "candidate" else "downgrade"
                        ),
                    )
            assert {case: call_outcome(c, *case) for case in cases} == expected
            c.rollback()
            elapsed, totals = [], []
            for _ in range(5):
                total_started = time.perf_counter_ns()
                with c.begin():
                    c.execute(
                        text(
                            "UPDATE research_runs SET updated_at=updated_at WHERE id=:r"
                        ),
                        {"r": rid},
                    )
                    started = time.perf_counter_ns()
                elapsed.append((time.perf_counter_ns() - started) / 1_000_000)
                totals.append((time.perf_counter_ns() - total_started) / 1_000_000)
            samples[phase] = {"commit_ms": elapsed, "transaction_ms": totals}
            assert (
                c.execute(
                    text("SELECT status FROM research_runs WHERE id=:r"), {"r": rid}
                ).scalar_one()
                == "queued"
            )
            assert (
                c.exec_driver_sql(
                    "SELECT count(*) FROM research_model_operations"
                ).scalar_one()
                == 0
            )
            assert (
                c.exec_driver_sql(
                    "SELECT count(*) FROM research_process_admissions"
                ).scalar_one()
                == 0
            )
            c.rollback()
        assert catalog(c) == original_catalog
        c.rollback()
    print(
        "RD_V3_QUEUED_METADATA_ONLY:"
        + json.dumps(
            {"samples": samples, "native_runtime": False, "seeded": False},
            sort_keys=True,
        )
    )

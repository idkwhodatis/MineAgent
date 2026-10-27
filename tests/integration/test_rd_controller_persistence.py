"""Real PostgreSQL negatives using actual018 retained source, not controller proof.

The old admitted/unexecuted fixture cannot become an authenticated020 child by
inserting a receipt or observation. Hosted tests must run; local collection is
not successful SQL execution or an operational controller happy path.
"""

import json
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_rd_source_persistence import persist, source_setup  # noqa: F401

pytestmark = pytest.mark.postgres


def test_old_precursor_cannot_receive_protected_child(source_setup):  # noqa: F811
    factory, *_ = source_setup
    saved = persist(source_setup)
    from rd_receipt_predicate_fixture import predicate_cases

    with factory() as session:
        for function, kind, payload, expected in predicate_cases():
            if function != "mineagent_rd_controller_payload":
                continue
            assert (
                session.scalar(
                    text(
                        "SELECT mineagent_rd_controller_payload(:kind,CAST(:payload AS jsonb))"
                    ),
                    {"kind": kind, "payload": json.dumps(payload)},
                )
                is expected
            )
    with (
        pytest.raises(
            DBAPIError,
            match="Child receipt belongs only|RD child cannot extend historical process grammar",
        ) as rejected,
        factory.begin() as session,
    ):
        session.execute(
            text("""INSERT INTO research_process_receipts
        (id,owner_id,run_id,attempt_id,generation,admission_id,kind,payload,payload_sha256,created_at)
        VALUES (:id,:owner,:run,:attempt,:generation,:admission,'controller_child','{}'::jsonb,:sha,now())"""),
            {
                "id": uuid4(),
                "owner": saved.fence.owner_id,
                "run": saved.fence.run_id,
                "attempt": saved.fence.attempt_id,
                "generation": saved.fence.generation,
                "admission": saved.controller_id,
                "sha": "0" * 64,
            },
        )
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    # Either exact historical-child guard can reject first; unrelated DB errors
    # must not satisfy this negative, and the transaction must retain no child.
    assert rejected.value.orig.sqlstate == "P0001"
    with factory() as session:
        assert session.scalar(
            text("""SELECT count(*) FROM research_process_receipts
            WHERE admission_id=:admission AND kind='controller_child'"""),
            {"admission": saved.controller_id},
        ) == 0


def test_unexecuted_precursor_cannot_record_adoption(source_setup):  # noqa: F811
    factory, *_ = source_setup
    saved = persist(source_setup)
    decision = {
        "schema_version": "mineagent.rdagent-adoption-observation.v1",
        "logical_step": 0,
        "evaluated_source_id": str(saved.source.id),
        "previous_adopted_source_id": None,
        "adopted_source_id": str(saved.source.id),
        "continuation_source_id": str(saved.source.id),
        "adopted_new": True,
    }
    from packages.factors.contracts import digest

    with (
        pytest.raises(DBAPIError, match="Current protected controller required"),
        factory.begin() as session,
    ):
        launch = session.execute(
            text("SELECT id FROM factor_controller_launches WHERE admission_id=:id"),
            {"id": saved.controller_id},
        ).scalar_one()
        session.execute(
            text("""INSERT INTO factor_generation_observations
        (id,owner_id,run_id,attempt_id,generation,start_id,controller_launch_id,logical_step,decision,decision_sha256,qualification,created_at)
        VALUES (:id,:owner,:run,:attempt,:generation,:start,:launch,0,CAST(:decision AS jsonb),:sha,'authenticated_observation_not_execution_verification',now())"""),
            {
                "id": uuid4(),
                "owner": saved.fence.owner_id,
                "run": saved.fence.run_id,
                "attempt": saved.fence.attempt_id,
                "generation": saved.fence.generation,
                "start": saved.source.start_id,
                "launch": launch,
                "decision": json.dumps(decision),
                "sha": digest(decision),
            },
        )
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

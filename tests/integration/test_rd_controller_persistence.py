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


def test_old_precursor_cannot_receive_protected_child(source_setup, tmp_path):  # noqa: F811
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
    # A well-formed forged child must reach the old-profile guard, rather than
    # failing an unrelated empty-payload/hash check. This is not physical proof:
    # the retained precursor has no actual protected child or leader identity.
    import hashlib

    from backend.app.research_process_models import ResearchProcessAdmission
    from packages.research_runtime.process_contracts import digest
    from packages.research_runtime.process_persistence_codec import (
        ROW_FIELDS,
        commitment_bytes,
    )
    from runtimes.rdagent_operational.process_journal import ControllerChild
    from tests.rdagent.test_rdagent_controller_process import records

    child_fields = records(tmp_path)[2].model_dump(mode="json")
    with factory() as session:
        admission = session.get(ResearchProcessAdmission, saved.controller_id)
        assert admission.profile == "mineagent.rdagent-orchestrator.v1"
        row = {name: getattr(admission, name) for name in ROW_FIELDS}
        child_fields["admission_sha256"] = hashlib.sha256(
            commitment_bytes(row)
        ).hexdigest()
    child = ControllerChild.model_validate_json(json.dumps(child_fields))
    payload = child.model_dump(mode="json")
    payload_sha256 = digest(payload)
    with factory() as session:
        assert (
            session.scalar(
                text(
                    "SELECT mineagent_rd_controller_payload('controller_child',CAST(:payload AS jsonb))"
                ),
                {"payload": json.dumps(payload)},
            )
            is True
        )
        assert (
            session.scalar(
                text(
                    "SELECT encode(sha256(convert_to(mineagent_rd_canonical_json(CAST(:payload AS jsonb),true),'UTF8')),'hex')"
                ),
                {"payload": json.dumps(payload)},
            )
            == payload_sha256
        )
    with (
        pytest.raises(
            DBAPIError,
            match="Child receipt requires exact protected controller",
        ) as rejected,
        factory.begin() as session,
    ):
        session.execute(
            text("""INSERT INTO research_process_receipts
        (id,owner_id,run_id,attempt_id,generation,admission_id,kind,payload,payload_sha256,created_at)
        VALUES (:id,:owner,:run,:attempt,:generation,:admission,'controller_child',CAST(:payload AS jsonb),:sha,now())"""),
            {
                "id": uuid4(),
                "owner": saved.fence.owner_id,
                "run": saved.fence.run_id,
                "attempt": saved.fence.attempt_id,
                "generation": saved.fence.generation,
                "admission": saved.controller_id,
                "payload": json.dumps(payload),
                "sha": payload_sha256,
            },
        )
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    # Installed024 extends this BEFORE trigger for OE and changes its exact error.
    # Unrelated errors cannot satisfy this negative; no child may be retained.
    assert rejected.value.orig.sqlstate == "P0001"
    with factory() as session:
        assert (
            session.scalar(
                text("""SELECT count(*) FROM research_process_receipts
            WHERE admission_id=:admission AND kind='controller_child'"""),
                {"admission": saved.controller_id},
            )
            == 0
        )


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

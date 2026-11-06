"""Actual recovery/receipt codecs with session doubles; not PostgreSQL proof."""

from types import SimpleNamespace as NS
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from backend.app.core.db import REQUIRED_SCHEMA_REVISION
from backend.app.models import ResearchRun
from backend.app.oe_structured_models import OEStructuredSubmission
from backend.app.services import DomainError
from tests.openevolve.test_structured_persisted_identity import graph
from workers.research.dispatcher import recover_stalled_jobs


def delivery():
    base, run, spec, _, _, rows = graph()
    run.idempotency_key = "recovery-key"
    run.status, run.active_attempt_id = "queued", None
    receipt = OEStructuredSubmission(
        id=spec.submission_id,
        owner_id=run.owner_id,
        run_id=run.id,
        idempotency_key=run.idempotency_key,
        request_canonical=spec.request_canonical,
        request_sha256=spec.request_sha256,
        preflight_sha256=spec.preflight_sha256,
        config_sha256=spec.config_sha256,
        origin="authenticated_user_submission",
    )
    rows[(OEStructuredSubmission, receipt.id)] = receipt
    rows[(ResearchRun, run.id)] = run
    session = MagicMock()
    session.get.side_effect = base.get
    session.scalars.side_effect = lambda query: (
        NS(all=lambda: [REQUIRED_SCHEMA_REVISION])
        if str(query) == "SELECT version_num FROM alembic_version"
        else NS(one_or_none=lambda: run)
    )
    jobs = [{"id": 42, "args": {"run_id": str(run.id), "generation": 1}}]
    session.execute.return_value.mappings.return_value.all.return_value = jobs
    return NS(
        session=session, run=run, receipt=receipt, spec=spec, rows=rows, jobs=jobs
    )


@pytest.mark.parametrize(
    "status", ["queued", "running", "cancel_requested", "failed", "succeeded"]
)
def test_redelivery_checks_saved_receipt_without_restarting_business_attempt(status):
    value = delivery()
    value.run.status = status
    value.run.active_attempt_id = uuid4() if status != "queued" else None
    before = vars(value.run).copy()
    assert recover_stalled_jobs(value.session, oe=True) == 1
    assert value.session.execute.call_args.args[1] == {"job_id": 42}
    assert "procrastinate_retry_job_v2" in str(value.session.execute.call_args.args[0])
    assert vars(value.run) == before
    value.session.add.assert_not_called()
    value.session.commit.assert_not_called()
    value.session.flush.assert_not_called()


@pytest.mark.parametrize(
    "fault",
    [
        "missing_receipt",
        "owner",
        "run",
        "key",
        "origin",
        "request_bytes",
        "request_hash",
        "preflight_hash",
        "config_hash",
        "run_config",
        "wrong_engine",
        "generation",
        "outbox_generation",
        "malformed_args",
        "missing_outbox",
    ],
)
def test_corrupt_oe_receipts_and_cross_family_jobs_never_retry(fault):
    value = delivery()
    if fault == "missing_receipt":
        value.rows.pop((OEStructuredSubmission, value.receipt.id))
    elif fault in {"owner", "run"}:
        setattr(value.receipt, fault + "_id", uuid4())
    elif fault == "key":
        value.receipt.idempotency_key = "other-key"
    elif fault == "origin":
        value.receipt.origin = "workflow_stage"
    elif fault == "request_bytes":
        value.receipt.request_canonical += " "
    elif fault.endswith("_hash"):
        setattr(value.receipt, fault.replace("_hash", "_sha256"), "0" * 64)
    elif fault == "run_config":
        value.run.config["schema_version"] = "mineagent.openevolve-source.v1"
    elif fault == "wrong_engine":
        value.run.purpose, value.run.engine = "factor_research", "rdagent"
    elif fault == "generation":
        value.run.generation = 2
    elif fault == "outbox_generation":
        value.jobs[0]["args"]["generation"] = 2
    elif fault == "malformed_args":
        value.jobs[0]["args"]["generation"] = True
    else:
        value.session.scalars.side_effect = lambda query: (
            NS(all=lambda: [REQUIRED_SCHEMA_REVISION])
            if str(query) == "SELECT version_num FROM alembic_version"
            else NS(one_or_none=lambda: None)
        )
    with pytest.raises(DomainError):
        recover_stalled_jobs(value.session, oe=True)
    assert value.session.execute.call_count == 1


def test_invalid_second_job_does_not_retry_valid_first_job():
    value = delivery()
    value.jobs.append({"id": 43, "args": {"run_id": "bad", "generation": 1}})
    with pytest.raises(DomainError):
        recover_stalled_jobs(value.session, oe=True)
    assert value.session.execute.call_count == 1

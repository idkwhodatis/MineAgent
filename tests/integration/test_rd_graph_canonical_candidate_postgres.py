"""Exact installed032g SQL A/B; verify bytes and a full existing populated graph."""

import json
import os

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_backup_restore import drill_databases as drill_databases  # noqa: PLC0414
from test_rdagent_workflow_user_flow import (
    test_normal_workflow_children_consume_frozen_inputs_and_real_numeric_results as _unchanged_workflow,
)

from tests.sql_probes import rd_graph_canonical_candidate as probe

pytestmark = pytest.mark.postgres
_LIMIT = 1048576


def _outcome(connection, sql, params):
    try:
        with connection.begin_nested():
            return ("ok", connection.execute(text(sql), params).scalar_one())
    except DBAPIError as error:
        return ("error", error.orig.sqlstate, error.orig.diag.message_primary)


def _temp_scope(connection):
    schema = probe.checked_schema(connection)
    # Initialize only a per-connection temp namespace. Outer caller rolls back
    # all helper DDL; no installed graph or encoder is modified.
    connection.exec_driver_sql(
        "CREATE TEMP TABLE rd_canonical_probe_scope (unused boolean) ON COMMIT DROP"
    )
    return schema


def test_installed032g_request_encoding_bytes_and_errors(migrated_engine):
    values = [
        None,
        False,
        0,
        -(10**80),
        {},
        [],
        "plain printable ASCII",
        'quotes " and backslashes \\ / literal \\u007f',
        "".join(chr(i) for i in range(1, 32)),
        "DEL\x7f",
        "é中e\u0301",
        "😀\U0010ffff",
        "\u0085\u2028\u2029",
        {"role": "user", "content": "Normal RD request (ASCII)"},
        {"x\x7f": [{"é": "😀"}, "\r\n\t\b\f"]},
        {"\x7f": "", "\n": ["\\u007f", "/"]},
        "x" * (_LIMIT - 2),
    ]
    corpus = [
        (
            json.dumps(value, ensure_ascii=True, separators=(",", ":")),
            (
                "ok",
                [
                    json.dumps(
                        value,
                        ensure_ascii=ascii_only,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    for ascii_only in (False, True)
                ],
            ),
        )
        for value in values
    ]
    corpus.append((None, ("ok", [None, None])))  # distinct from JSON null
    corpus.extend(
        [
            (
                "1.5",
                ("error", "22023", "RD canonical metadata requires integer tokens"),
            ),
            (
                '{"float":1.0}',
                ("error", "22023", "RD canonical metadata requires integer tokens"),
            ),
            (
                "[" * 25 + "null" + "]" * 25,
                ("error", "22023", "RD canonical metadata depth exceeded"),
            ),
            (
                json.dumps("x" * (_LIMIT - 1)),
                ("error", "22023", "RD canonical metadata byte bound exceeded"),
            ),
            # UTF8 form fits; only true ASCII encoding crosses the bound.
            (
                json.dumps("😀" * (_LIMIT // 12 + 1)),
                ("error", "22023", "RD canonical metadata byte bound exceeded"),
            ),
            ('"\\u0000"', ("error", "22P05", "unsupported Unicode escape sequence")),
            ("{", ("error", "22P02", "invalid input syntax for type json")),
        ]
    )
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            schema = _temp_scope(connection)
            for candidate in (False, True):
                connection.exec_driver_sql(
                    probe.temp_bytes_source(schema, candidate=candidate)
                )
            for index, (value, expected) in enumerate(corpus):
                # No values or error diagnostics in failure output: report a case ordinal.
                old, new = (
                    _outcome(
                        connection,
                        f"SELECT pg_temp.{name}(CAST(:v AS jsonb))",
                        {"v": value},
                    )
                    for name in ("rd_bytes_original", "rd_bytes_reuse")
                )
                if old != new or old != expected:
                    pytest.fail(
                        f"Request encoding bytes or SQL outcome diverged at case {index}",
                        pytrace=False,
                    )
        finally:
            transaction.rollback()


def _persisted_default_child(session, storage):
    from backend.app.execution_snapshot_models import FactorExecutionEvaluationOutcome
    from backend.app.models import ExecutionAttempt, ResearchRun
    from backend.app.rdagent_boundary_models import FactorGenerationBoundary
    from backend.app.rdagent_models import FactorGenerationOutcome
    from backend.app.rdagent_workflow_models import FactorWorkflowSubmission
    from backend.app.research_broker_models import (
        ResearchBudgetReservation,
        ResearchBudgetSettlement,
        ResearchModelOperation,
        ResearchModelOutcome,
        ResearchModelTransport,
    )
    from backend.app.research_object_models import (
        ResearchObject,
        ResearchObjectOutcome,
        ResearchObjectPublication,
    )
    from backend.app.research_object_services import _read_exact
    from packages.rdagent_adapter.execution_outcomes import parse_document

    submitted = session.scalars(select(FactorWorkflowSubmission)).one()
    run = session.get(ResearchRun, submitted.run_id)
    assert run is not None and (run.owner_id, run.purpose, run.engine, run.status) == (
        submitted.owner_id,
        "factor_research",
        "rdagent",
        "succeeded",
    )
    assert run.config["schema_version"] == "mineagent.factor-research.v3"
    attempt = session.scalars(select(ExecutionAttempt).filter_by(run_id=run.id)).one()
    assert (
        attempt.status == "succeeded"
        and attempt.sandbox_stopped
        and attempt.finished_at
    )
    assert attempt.generation == 1
    scope = {
        "owner_id": run.owner_id,
        "run_id": run.id,
        "attempt_id": attempt.id,
        "generation": attempt.generation,
    }

    def one(model, **keys):
        return session.scalars(select(model).filter_by(**scope, **keys)).one()

    accepted = one(FactorGenerationOutcome)
    boundary = one(FactorGenerationBoundary, start_id=accepted.start_id)
    measured = one(
        FactorExecutionEvaluationOutcome, generation_start_id=accepted.start_id
    )
    assert (accepted.logical_step, accepted.outcome, accepted.error_code) == (
        0,
        "accepted",
        None,
    )
    assert accepted.adopted_source_id == accepted.evaluated_source_id
    assert (boundary.logical_step, boundary.boundary, boundary.next_logical_step) == (
        0,
        "returned",
        None,
    )
    assert (measured.status, measured.boundary_id, measured.feedback_object_id) == (
        "valid",
        boundary.id,
        accepted.feedback_object_id,
    )

    def published(kind, identity, key):
        publication = one(ResearchObjectPublication, object_kind=kind)
        outcome = session.get(ResearchObjectOutcome, publication.id)
        obj = session.get(ResearchObject, identity)
        assert outcome is not None and obj is not None
        assert (
            outcome.owner_id,
            outcome.object_id,
            outcome.object_kind,
            outcome.sha256,
        ) == (run.owner_id, obj.id, kind, obj.sha256)
        assert (
            obj.owner_id,
            obj.object_kind,
            obj.sha256,
            obj.size_bytes,
            obj.storage_key,
        ) == (
            run.owner_id,
            kind,
            publication.sha256,
            publication.size_bytes,
            publication.storage_key,
        )
        held = one(ResearchBudgetReservation, id=publication.budget_reservation_id)
        settled = one(ResearchBudgetSettlement, reservation_id=held.id)
        assert (held.purpose, held.logical_key, held.state) == (
            "object_storage",
            f"{key}:{accepted.start_id}",
            "settled",
        )
        assert (
            settled.outcome == "conservative_unknown" and settled.charge == held.amounts
        )
        assert (
            held.amounts["storage_bytes"]
            == publication.peak_bytes
            == 2 * obj.size_bytes + 8192
        )
        raw = _read_exact(storage, obj.storage_key, obj.sha256, obj.size_bytes)
        doc = parse_document(kind, raw)
        assert {key: getattr(doc, key) for key in scope} == scope
        assert (
            doc.start_id,
            doc.snapshot_id,
            doc.evaluation_id,
            doc.source_id,
            doc.factor_version_id,
        ) == (
            accepted.start_id,
            measured.snapshot_id,
            measured.evaluation_id,
            accepted.evaluated_source_id,
            accepted.factor_version_id,
        )
        return obj, doc

    proposal_object, _ = published(
        "factor_generation_proposal",
        accepted.proposal_object_id,
        "rd-execution-proposal",
    )
    adoption_object, adoption = published(
        "factor_adoption_receipt", accepted.adoption_object_id, "rd-execution-adoption"
    )
    assert (accepted.adoption_object_kind, accepted.adoption_sha256) == (
        adoption_object.object_kind,
        adoption_object.sha256,
    )
    assert (
        adoption.observation_id,
        adoption.boundary_id,
        adoption.controller_launch_id,
        adoption.proposal_object_id,
        adoption.proposal_sha256,
        adoption.feedback_object_id,
        adoption.feedback_sha256,
        adoption.adopted_source_id,
    ) == (
        boundary.observation_id,
        boundary.id,
        boundary.controller_launch_id,
        proposal_object.id,
        proposal_object.sha256,
        measured.feedback_object_id,
        measured.feedback_sha256,
        accepted.adopted_source_id,
    )

    operations = session.scalars(
        select(ResearchModelOperation).filter_by(**scope).limit(33)
    ).all()
    assert 4 < len(operations) <= 32
    assert {op.protocol for op in operations} == {
        "mineagent.rdagent-provider-chat.v1",
        "mineagent.rdagent-provider-embedding.v1",
    }
    for op in operations:
        transport = one(ResearchModelTransport, operation_id=op.id)
        outcome = one(ResearchModelOutcome, transport_id=transport.id)
        assert (op.state, transport.state, outcome.outcome) == (
            "completed",
            "completed",
            "completed",
        )
    return run.id


def test_default_off_populated_rd_proposal_adoption_graph_matches_candidate(
    drill_databases, tmp_path, monkeypatch
):
    if os.environ.get("MINEAGENT_RD_GRAPH_ASCII_PROBE_REQUIRED") != "1":
        pytest.fail(
            "Explicit isolated populated provider-graph A/B required", pytrace=False
        )

    import test_backup_restore as legacy

    # Keep every original default-off assertion including browser/cold restore.
    # This standalone diagnostic is not the opt-in two-child or RD→OE gate.
    _unchanged_workflow(
        drill_databases, tmp_path, monkeypatch, allow_feedback_release=False
    )

    engine = legacy._engine(drill_databases.source)
    try:
        with engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            transaction = connection.begin()
            try:
                schema = _temp_scope(connection)
                assert schema == "public"
                connection.exec_driver_sql(probe.temp_graph_source(schema))
                with Session(bind=connection) as session:
                    rid = _persisted_default_child(
                        session, tmp_path / "workflow-provider-user-flow"
                    )
                old = _outcome(
                    connection,
                    "SELECT public.mineagent_rd_provider_graph(:rid)",
                    {"rid": rid},
                )
                new = _outcome(
                    connection,
                    "SELECT pg_temp.rd_graph_ascii_reuse(:rid)",
                    {"rid": rid},
                )
                if old != new or old[0] != "ok":
                    pytest.fail(
                        "Populated installed provider graph SQL outcome diverged",
                        pytrace=False,
                    )
            finally:
                transaction.rollback()
    finally:
        engine.dispose()

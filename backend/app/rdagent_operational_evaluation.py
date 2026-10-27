"""Actual maintained single-cutoff process→native→normalizer→snapshot callback.

No synthetic rows are returned. Every repeated value read resolves retained stopped
outputs and primitive reports through the021 reader. A partial/uncertain first
execution is not relaunched by an RPC retry or converted into successful evidence.
"""

import json
from contextlib import contextmanager

from sqlalchemy import select

from backend.app.core.maintenance import shared_lifecycle
from backend.app.execution_snapshot_models import (
    ExecutionSnapshotOutcome,
    ExecutionSnapshotPublication,
)
from backend.app.execution_snapshot_services import (
    publish_execution_snapshot,
    read_execution_snapshot,
)
from backend.app.factor_models import FactorVersion
from backend.app.factor_native_io import native_storage_guard
from backend.app.rdagent_controller_services import require_rd_controller
from backend.app.rdagent_models import (
    FactorCutoffExecution,
    FactorEvaluationStart,
    FactorGenerationSource,
)
from backend.app.rdagent_services import _scope, _scope_row
from backend.app.research_lifecycle import neutral_transaction
from packages.factors.contracts import SnapshotInput, digest
from packages.rdagent_adapter.fixture_objective import measured_fixture_objective
from runtimes.rdagent_operational.rpc import RPCError


@contextmanager
def snapshot_transaction(factory, fence, settings):
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root),
        neutral_transaction(factory, fence, resource_scope="factor_numeric") as session,
    ):
        yield session


def read_evaluation_snapshot(session, fence, peer, evaluation_id, settings):
    require_rd_controller(session, fence, peer)
    evaluation = _scope_row(session, FactorEvaluationStart, evaluation_id, fence)
    publication = session.scalar(
        select(ExecutionSnapshotPublication).filter_by(
            **_scope(fence), evaluation_id=evaluation.id
        )
    )
    if publication is None:
        raise RPCError("snapshot_not_published")
    snapshot = read_execution_snapshot(session, fence, publication.id, settings)
    if (
        snapshot.evaluation_id,
        snapshot.source_id,
        snapshot.generation_start_id,
        snapshot.factor_version_id,
        snapshot.input_sha256,
    ) != (
        evaluation.id,
        evaluation.evaluated_source_id,
        evaluation.start_id,
        evaluation.factor_version_id,
        evaluation.input_sha256,
    ):
        raise RPCError("snapshot_evaluation_binding")
    inp = SnapshotInput.model_validate_json(json.dumps(evaluation.snapshot_input))
    return snapshot, inp, measured_fixture_objective(inp, snapshot.rows)


def measured_start_snapshot(factory, fence, peer, start_id, settings):
    with snapshot_transaction(factory, fence, settings) as session:
        require_rd_controller(session, fence, peer)
        evaluation = session.scalar(
            select(FactorEvaluationStart).filter_by(**_scope(fence), start_id=start_id)
        )
        if evaluation is None:
            raise RPCError("evaluation_not_started")
        snapshot, _, objective = read_evaluation_snapshot(
            session, fence, peer, evaluation.id, settings
        )
        return snapshot, objective


def _authorize_launch(factory, fence, peer):
    # Revalidate current authenticated controller before each new independent unit;
    # their own durable issuers still enforce full fences, budgets and deadlines.
    with neutral_transaction(factory, fence, resource_scope="processes") as session:
        require_rd_controller(session, fence, peer)


def _read_frame(session, fence, peer, evaluation_id, settings):
    snapshot, _, objective = read_evaluation_snapshot(
        session, fence, peer, evaluation_id, settings
    )
    rows = [row.model_dump(mode="json") for row in snapshot.rows]
    return {
        "schema_version": "mineagent.rdagent-evaluation-frame.v1",
        "source_id": str(snapshot.source_id),
        "evaluation_id": str(snapshot.evaluation_id),
        "snapshot_id": str(snapshot.snapshot_id),
        "factor_version_id": str(snapshot.factor_version_id),
        "rows": rows,
        "rows_sha256": digest(rows),
        "feedback": f"Actual complete declared-window execution snapshot {snapshot.snapshot_id}; {len(rows)} retained rows; maintained momentum objective passed={objective}. Input provided is not proven computation exposure.",
    }


def execute_or_reuse_snapshot(
    factory, fence, peer, *, source_id, evaluation_id, settings
):
    with snapshot_transaction(factory, fence, settings) as session:
        require_rd_controller(session, fence, peer)
        evaluation = _scope_row(session, FactorEvaluationStart, evaluation_id, fence)
        source = _scope_row(session, FactorGenerationSource, source_id, fence)
        if (
            evaluation.evaluated_source_id,
            evaluation.factor_version_id,
            evaluation.start_id,
        ) != (
            source.id,
            source.factor_version_id,
            source.start_id,
        ) or evaluation.expected_cutoffs != 1:
            raise RPCError("complete_single_cutoff_required")
        existing = session.scalar(
            select(ExecutionSnapshotPublication).filter_by(
                **_scope(fence), evaluation_id=evaluation_id
            )
        )
        pending_publication = (
            existing is not None
            and session.get(ExecutionSnapshotOutcome, existing.id) is None
        )
        if existing is not None and not pending_publication:
            # Reuse means a fresh physical read under this same locked scope,
            # never a cached DTO or peer capability. No publication work remains.
            return _read_frame(session, fence, peer, evaluation_id, settings)
        if existing is None:
            if (
                session.scalar(
                    select(FactorCutoffExecution).filter_by(
                        **_scope(fence), evaluation_id=evaluation_id
                    )
                )
                is not None
            ):
                raise RPCError("partial_execution_requires_reconciliation")
            version = session.scalar(
                select(FactorVersion).filter_by(
                    owner_id=fence.owner_id, id=source.factor_version_id
                )
            )
            if version is None:
                raise RPCError("source_version_missing")
            version_id, code_id = version.id, version.code_object_id
    if existing is None:
        from backend.app.factor_native_services import publish_completed_candidate
        from workers.research.numeric_fixture import (
            execute_fixed_candidate_from_source,
            execute_fixed_normalizer_from_native,
        )

        def authorize_submission(session, current_fence):
            require_rd_controller(session, current_fence, peer)
            return peer

        _authorize_launch(factory, fence, peer)
        candidate = execute_fixed_candidate_from_source(
            factory,
            fence,
            settings,
            source_id=source_id,
            factor_version_id=version_id,
            code_object_id=code_id,
            authorize_submission=authorize_submission,
        )
        _authorize_launch(factory, fence, peer)
        native = publish_completed_candidate(factory, fence, candidate, settings)
        _authorize_launch(factory, fence, peer)
        execute_fixed_normalizer_from_native(
            factory,
            fence,
            settings,
            publication_id=native,
            primitive_report=True,
            authorize_submission=authorize_submission,
        )
        _authorize_launch(factory, fence, peer)
        publish_execution_snapshot(factory, fence, evaluation_id, settings)
    elif pending_publication:
        # Resume only immutable publication from already-stopped retained units;
        # never recreate candidate or normalizer after a partial execution.
        _authorize_launch(factory, fence, peer)
        publish_execution_snapshot(factory, fence, evaluation_id, settings)
    with snapshot_transaction(factory, fence, settings) as session:
        return _read_frame(session, fence, peer, evaluation_id, settings)

"""Real retained files/locks, synthetic DB records; not PG/Docker acceptance."""

import shutil

import pytest

from backend.app.factor_native_io import NativeStorageError, native_storage_guard
from backend.app.rdagent_numeric_failure_history import read_rejection_evidence
from tests.numeric_rejection_runtime.test_backup import fixture


def prepared(root):
    root.mkdir(exist_ok=True)
    (root / "factors").mkdir()
    return fixture(root)


def test_actual_relocated_journal_keeps_old_execution_identity(tmp_path):
    original, moved = tmp_path / "original", tmp_path / "moved"
    row, indexes, _ = prepared(original)
    shutil.copytree(original, moved)
    with native_storage_guard(moved):
        launch, _, stop, rejection = read_rejection_evidence(moved, row, indexes)
    assert launch.root_inode == original.stat().st_ino
    assert launch.root_inode != moved.stat().st_ino
    assert stop.confirmed_absent and rejection.outcome == "child_nonzero"


def test_no_caller_bypass_of_real_storage_guard(tmp_path):
    row, indexes, _ = prepared(tmp_path)
    with pytest.raises(NativeStorageError):
        read_rejection_evidence(tmp_path, row, indexes)


@pytest.mark.parametrize(
    "mutation",
    [
        "orphan",
        "temp",
        "symlink",
        "hardlink",
        "directory",
        "missing",
        "refund",
        "running",
    ],
)
def test_actual_uncertain_or_forged_history_stays_unavailable(tmp_path, mutation):
    row, indexes, path = prepared(tmp_path)
    if mutation == "orphan":
        (path / "output" / "result.h5").write_bytes(b"not accepted")
    elif mutation == "temp":
        (path / "control" / ".stop.tmp").write_bytes(b"uncertain")
    elif mutation == "symlink":
        (path / "output" / "foreign").symlink_to(path / "control" / "stop.json")
    elif mutation == "hardlink":
        (path / "output" / "foreign").hardlink_to(path / "control" / "stop.json")
    elif mutation == "directory":
        (path / "control" / "nested").mkdir()
    elif mutation == "missing":
        (path / "control" / "stop.json").unlink()
    elif mutation == "refund":
        indexes["research_budget_settlements"]["settlement"]["charge"][
            "storage_bytes"
        ] -= 1
    else:
        indexes["research_process_states"][row["id"]]["status"] = "running"
    with native_storage_guard(tmp_path), pytest.raises((ValueError, OSError)):
        read_rejection_evidence(tmp_path, row, indexes)
    if mutation in {"orphan", "temp"}:
        assert any(p.name in {"result.h5", ".stop.tmp"} for p in path.rglob("*"))


@pytest.mark.parametrize("mode", ["valid", "foreign", "overflow", "override"])
def test_scoped_sql_is_bounded_and_closes_cursor(mode):
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    from backend.app.rdagent_models import FactorEvaluationStart
    from backend.app.rdagent_numeric_failure_history import _ScopedRows

    scope = {
        "owner_id": uuid4(),
        "run_id": uuid4(),
        "attempt_id": uuid4(),
        "generation": 1,
    }

    class Result:
        closed = False

        def mappings(self):
            row = dict(scope, id=uuid4())
            if mode == "foreign":
                row["owner_id"] = uuid4()
            for _ in range(257 if mode == "overflow" else 1):
                yield row

        def close(self):
            self.closed = True

    result = Result()

    class Session:
        def execute(self, statement):
            compiled = statement.compile(dialect=postgresql.dialect())
            assert all(value in compiled.params.values() for value in scope.values())
            assert "LIMIT" in str(compiled)
            assert statement.get_execution_options()["stream_results"] is True
            return result

    loader = _ScopedRows(Session(), **scope)
    if mode == "override":
        with pytest.raises(ValueError, match="overridden"):
            loader.read(FactorEvaluationStart, owner_id=uuid4())
        assert not result.closed  # No SQL was run.
    elif mode != "valid":
        with pytest.raises(ValueError):
            loader.read(FactorEvaluationStart)
        assert result.closed
    else:
        assert len(loader.read(FactorEvaluationStart)) == 1
        assert result.closed


@pytest.mark.parametrize(
    "mutation", [None, "request", "response", "scope", "refund", "usage", "orphan"]
)
def test_actual_provider_envelopes_not_just_stopped_descriptor(tmp_path, mutation):
    import hashlib
    from uuid import uuid4

    from backend.app.rdagent_numeric_failure_history import _provider_model_bytes
    from packages.rdagent_provider.accounting import request_reservation
    from packages.rdagent_provider.codec import canonical_bytes
    from packages.rdagent_provider.contracts import (
        RDProviderChatResponse,
        RDProviderUsage,
    )
    from tests.rdagent_provider.test_accounting_receipts import pair

    binding, request = pair()
    response = RDProviderChatResponse(
        content="actual critique",
        finish_reason="stop",
        usage=RDProviderUsage(
            input_tokens=7,
            output_tokens=4,
            total_tokens=11,
            usage_source="synthetic_provider",
        ),
    )
    scope = {
        "owner_id": str(uuid4()),
        "run_id": str(uuid4()),
        "attempt_id": str(uuid4()),
        "generation": 1,
    }
    objects = {}

    def obj(kind, body):
        raw = canonical_bytes(request.schema_version, body)
        sha, identity = hashlib.sha256(raw).hexdigest(), str(uuid4())
        key = f"research-objects/{scope['owner_id']}/by-kind/{kind}/{sha}.json"
        path = tmp_path / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        objects[identity] = {
            "id": identity,
            "owner_id": scope["owner_id"],
            "object_kind": kind,
            "sha256": sha,
            "storage_key": key,
            "size_bytes": len(raw),
        }
        return identity, sha

    req, reqsha = obj("model_request", request)
    res, ressha = obj("model_response", response)
    operations = {
        "op": dict(
            scope,
            id="op",
            protocol=request.schema_version,
            state="completed",
            request=request.model_dump(mode="json"),
            request_object_id=req,
            request_object_kind="model_request",
            request_sha256=reqsha,
        )
    }
    transports = {
        "transport": dict(
            scope,
            id="transport",
            operation_id="op",
            reservation_id="held",
            state="completed",
        )
    }
    amounts = request_reservation(request, binding).model_dump()
    reservations = {
        "held": dict(
            scope,
            id="held",
            state="settled",
            purpose="model_transport",
            amounts=amounts,
        )
    }
    outcomes = {
        "outcome": dict(
            scope,
            id="outcome",
            transport_id="transport",
            outcome="completed",
            usage=amounts.copy(),
            response_object_id=res,
            response_object_kind="model_response",
            response_sha256=ressha,
        )
    }
    settlements = {
        "charge": dict(
            scope,
            id="charge",
            reservation_id="held",
            outcome="conservative_unknown",
            charge=amounts.copy(),
        )
    }
    if mutation in {"request", "response"}:
        (
            tmp_path / objects[req if mutation == "request" else res]["storage_key"]
        ).write_bytes(b"replaced")
    elif mutation == "scope":
        transports["transport"]["owner_id"] = str(uuid4())
    elif mutation == "refund":
        settlements["charge"]["charge"]["tokens"] -= 1
    elif mutation == "usage":
        outcomes["outcome"]["usage"]["tokens"] -= 1
    elif mutation == "orphan":
        outcomes["other"] = dict(
            outcomes["outcome"], id="other", transport_id="foreign"
        )
    args = (
        operations,
        transports,
        outcomes,
        reservations,
        settlements,
        objects,
        binding,
        lambda key, sha, cap: (tmp_path / key).read_bytes(),
    )
    if mutation:
        with pytest.raises(ValueError):
            _provider_model_bytes(*args)
    else:
        _provider_model_bytes(*args)


def test_same_size_file_replacement_after_validation_is_not_hidden(
    tmp_path, monkeypatch
):
    from backend.app import rdagent_numeric_failure_history as history

    row, indexes, path = prepared(tmp_path)
    original = history.validate_rejection_closure_unit

    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        target = path / "control" / "rejection.json"
        raw = target.read_bytes()
        target.unlink()
        target.write_bytes(raw)
        return result

    monkeypatch.setattr(history, "validate_rejection_closure_unit", mutate)
    with (
        native_storage_guard(tmp_path),
        pytest.raises(ValueError, match="changed after"),
    ):
        read_rejection_evidence(tmp_path, row, indexes)

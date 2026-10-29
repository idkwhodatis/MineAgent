"""Real retained-file cold component tests; synthetic records, not Docker/PG."""

import hashlib
import json
from uuid import UUID, uuid4

import pytest

from backend.app.numeric_rejection_backup import validate_rejection_closure_unit
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_persistence_codec import encode_row
from tests.research_runtime.test_numeric_rejection import retained


def fixture(root):
    admission, launch, identity, stop, rejection, path = retained(root)
    row = {
        k: str(v) if isinstance(v, UUID) else v
        for k, v in encode_row(admission).items()
    }
    scope = {k: row[k] for k in ("owner_id", "run_id", "attempt_id", "generation")}
    amounts = BudgetVector(
        process_slots=1, wall_milliseconds=60000, storage_bytes=67174400
    ).model_dump()
    reservation = dict(
        scope,
        id=row["reservation_id"],
        purpose="process_unit",
        state="settled",
        amounts=amounts,
    )
    receipts = {}
    for kind, obj in {
        "launch_intent": launch.intent,
        "launch_identity": identity,
        "stop": stop,
        "rejection": rejection,
    }.items():
        value = obj.model_dump(mode="json")
        rid = str(uuid4())
        receipts[rid] = dict(
            scope,
            id=rid,
            admission_id=row["id"],
            kind=kind,
            payload=value,
            payload_sha256=digest(value),
        )
    indexes = {
        "research_process_receipts": receipts,
        "research_process_states": {row["id"]: dict(scope, status="stopped")},
        "research_budget_reservations": {reservation["id"]: reservation},
        "research_budget_settlements": {
            "settlement": dict(
                scope,
                reservation_id=reservation["id"],
                charge=amounts.copy(),
                outcome="conservative_unknown",
            )
        },
    }
    return row, indexes, path


def validate(root, row, indexes):
    files = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}

    def check(key, sha, size):
        raw = (root / key).read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha or len(raw) != size:
            raise ValueError("physical hash/size differs")

    return validate_rejection_closure_unit(
        row,
        indexes,
        files,
        check,
        lambda key: json.loads((root / key).read_bytes()),
        set(),
        set(),
    )


def test_exact_rejection_retains_chain_without_live_root_comparison(tmp_path):
    row, indexes, _ = fixture(tmp_path)
    records = validate(tmp_path, row, indexes)
    assert records[-1].outcome == "child_nonzero"
    assert records[0].intent.admission.id == UUID(row["id"])
    assert len(indexes["research_process_receipts"]) == 4


@pytest.mark.parametrize(
    "mutation",
    [
        "refund",
        "unsettled",
        "missing",
        "scope",
        "physical",
        "extra",
        "duplicate",
        "success",
        "normalizer",
    ],
)
def test_partial_forged_or_success_mixed_graph_rejected(tmp_path, mutation):
    row, indexes, path = fixture(tmp_path)
    if mutation == "refund":
        indexes["research_budget_settlements"]["settlement"]["charge"][
            "storage_bytes"
        ] -= 1
    elif mutation == "unsettled":
        indexes["research_budget_reservations"][row["reservation_id"]]["state"] = (
            "uncertain"
        )
    elif mutation == "missing":
        (path / "control" / "rejection.json").unlink()
    elif mutation == "scope":
        next(
            r
            for r in indexes["research_process_receipts"].values()
            if r["kind"] == "rejection"
        )["owner_id"] = str(uuid4())
    elif mutation == "physical":
        with (path / "control" / "rejection.json").open("ab") as f:
            f.write(b" ")
    elif mutation == "extra":
        (path / "output" / "result.h5").write_bytes(b"not a failure output")
    elif mutation == "duplicate":
        receipt = next(
            r
            for r in indexes["research_process_receipts"].values()
            if r["kind"] == "rejection"
        )
        indexes["research_process_receipts"]["extra"] = {**receipt, "id": str(uuid4())}
    elif mutation == "success":
        receipt = next(
            r
            for r in indexes["research_process_receipts"].values()
            if r["kind"] == "rejection"
        )
        receipt["kind"] = "extraction"
    else:
        row.update(role="factor_normalizer", profile="mineagent.factor-numeric-hdf.v1")
    with pytest.raises((ValueError, ProcessError)):
        validate(tmp_path, row, indexes)

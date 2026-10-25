"""Pure closure verification is not a substitute for the real restore drill."""

import copy
import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.research_backup import (
    FIELDS,
    SCHEMA,
    canonical,
    manifest_keys,
    validate_absence,
    validate_closure,
)
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.object_identity import (
    object_identity,
    publication_journal,
)


def empty_closure():
    return {
        "schema_version": SCHEMA,
        "tables": {name: [] for name in FIELDS},
        "external_bindings": {"runs": [], "attempts": []},
    }


def object_closure(*, uncertain=False):
    closure = empty_closure()
    owner, run, attempt, reservation, publication, obj, budget = (
        str(uuid4()) for _ in range(7)
    )
    stamp = "2026-01-01T00:00:00+00:00"
    scope = {"owner_id": owner, "run_id": run, "attempt_id": attempt, "generation": 1}
    content = canonical({"fixture": "bounded Unicode 数据"})
    digest = hashlib.sha256(content).hexdigest()
    from uuid import UUID

    key = object_identity(UUID(owner), "model_request", digest).storage_key
    peak = len(content) * 2 + 8192
    vector = BudgetVector(storage_bytes=peak).model_dump()
    closure["external_bindings"]["runs"] = [
        {
            "id": run,
            "owner_id": owner,
            "generation": 1,
            "purpose": "legacy_strategy",
            "engine": "legacy",
            "status": "succeeded",
        }
    ]
    closure["external_bindings"]["attempts"] = [
        {
            "id": attempt,
            "owner_id": owner,
            "run_id": run,
            "generation": 1,
            "status": "succeeded",
            "sandbox_stopped": True,
            "finished_at": stamp,
        }
    ]
    tables = closure["tables"]
    tables["research_budget_controls"] = [
        {
            "id": budget,
            "owner_id": owner,
            "run_id": run,
            "created_at": stamp,
            "limits": vector,
            "used": BudgetVector().model_dump(),
            "reserved": vector,
        }
    ]
    tables["research_budget_reservations"] = [
        dict(
            id=reservation,
            **scope,
            created_at=stamp,
            purpose="object_storage",
            logical_key="fixture",
            amounts=vector,
            state="uncertain" if uncertain else "reserved",
        )
    ]
    publication_row = dict(
        id=publication,
        **scope,
        budget_reservation_id=reservation,
        object_kind="model_request",
        sha256=digest,
        size_bytes=len(content),
        storage_key=key,
        peak_bytes=peak,
        created_at=stamp,
    )
    tables["research_object_publications"] = [publication_row]
    journal = publication_journal(SimpleNamespace(**publication_row))
    journal_key = f"research-objects/{owner}/journals/{publication}.json"
    files = {key: content, journal_key: journal}
    if not uncertain:
        tables["research_budget_reservations"][0]["state"] = "settled"
        tables["research_budget_controls"][0]["reserved"] = BudgetVector().model_dump()
        tables["research_budget_controls"][0]["used"] = vector
        tables["research_budget_settlements"] = [
            {
                "id": str(uuid4()),
                **scope,
                "created_at": stamp,
                "reservation_id": reservation,
                "charge": vector,
                "outcome": "conservative_unknown",
            }
        ]
        tables["research_objects"] = [
            {
                "id": obj,
                "owner_id": owner,
                "object_kind": "model_request",
                "sha256": digest,
                "size_bytes": len(content),
                "storage_key": key,
                "media_type": "application/json",
                "created_at": stamp,
            }
        ]
        tables["research_object_outcomes"] = [
            {
                "publication_id": publication,
                "owner_id": owner,
                "object_id": obj,
                "object_kind": "model_request",
                "sha256": digest,
                "journal_sha256": hashlib.sha256(journal).hexdigest(),
                "created_at": stamp,
            }
        ]
    return closure, files


def verify(closure, files):
    import json

    inventory = [
        {"path": key, "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        for key, raw in files.items()
    ]

    def check(key, sha, size=None):
        assert key in files
        assert hashlib.sha256(files[key]).hexdigest() == sha
        assert size is None or len(files[key]) == size

    validate_closure(
        closure, check, lambda key: json.loads(files[key]), 1000, inventory=inventory
    )


def test_empty_neutral_closure_is_complete_not_missing():
    verify(empty_closure(), {})
    assert manifest_keys(empty_closure()) == set()


@pytest.mark.parametrize("uncertain", [False, True])
def test_object_and_uncertain_publication_keep_exact_kind_bytes_and_charge(uncertain):
    closure, files = object_closure(uncertain=uncertain)
    verify(closure, files)
    assert set(files) <= manifest_keys(closure)


@pytest.mark.parametrize(
    "mutation",
    [
        "future",
        "missing",
        "extra",
        "extra_column",
        "wrong_owner",
        "wrong_run",
        "released",
        "undercharged",
        "missing_journal",
        "substituted_journal",
        "unknown_file",
    ],
)
def test_neutral_closure_rejects_incomplete_or_substituted_evidence(mutation):
    closure, files = object_closure()
    tables = closure["tables"]
    if mutation == "future":
        closure["schema_version"] = "mineagent.research-restore-closure.v2"
    elif mutation == "missing":
        del tables["research_process_receipts"]
    elif mutation == "extra":
        tables["unknown_future_table"] = []
    elif mutation == "extra_column":
        tables["research_objects"][0]["unknown"] = True
    elif mutation in {"wrong_owner", "wrong_run"}:
        tables["research_object_publications"][0][
            "owner_id" if mutation == "wrong_owner" else "run_id"
        ] = str(uuid4())
    elif mutation == "released":
        tables["research_budget_settlements"][0]["charge"] = BudgetVector().model_dump()
    elif mutation == "undercharged":
        tables["research_budget_controls"][0]["used"] = BudgetVector().model_dump()
    elif mutation == "missing_journal":
        del files[next(k for k in files if "/journals/" in k)]
    elif mutation == "substituted_journal":
        key = next(k for k in files if "/journals/" in k)
        import json

        value = json.loads(files[key])
        value["run_id"] = str(uuid4())
        files[key] = canonical(value)
    else:
        files["research-objects/unknown.json"] = b"{}"
    with pytest.raises((ValueError, AssertionError, KeyError)):
        verify(closure, files)


def test_legacy_revision_cannot_smuggle_neutral_files():
    validate_absence([{"path": "runs/example"}])
    with pytest.raises(ValueError):
        validate_absence([{"path": "research-objects/example"}])


def test_unknown_atomic_temporary_is_not_garbage_collected():
    closure, files = object_closure(uncertain=True)
    object_key = next(k for k in files if "/by-kind/" in k)
    parent, name = object_key.rsplit("/", 1)
    files[f"{parent}/.{name}.{uuid4().hex}.tmp"] = b"partial"
    verify(closure, files)
    forged = copy.deepcopy(files)
    forged[f"{parent}/.{'f' * 64}.json.{uuid4().hex}.tmp"] = b"partial"
    with pytest.raises(ValueError):
        verify(closure, forged)


def test_literal_closure_inventory_matches_exact_current_tables():
    from backend.app.research_broker_models import BROKER_TABLES
    from backend.app.research_object_models import (
        ResearchObject,
        ResearchObjectOutcome,
        ResearchObjectPublication,
    )
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
        ResearchProcessReceipt,
        ResearchProcessState,
    )

    models = (
        *BROKER_TABLES,
        ResearchObject,
        ResearchObjectPublication,
        ResearchObjectOutcome,
        ResearchProcessAdmission,
        ResearchProcessReceipt,
        ResearchProcessState,
    )
    assert {model.__tablename__ for model in models} == set(FIELDS)
    for model in models:
        assert set(model.__table__.columns.keys()) == set(
            FIELDS[model.__tablename__].split()
        )


def process_closure():
    """Invented pure-validator inputs, explicitly not real containment evidence."""
    from packages.research_runtime.process_contracts import digest
    from runtimes.research_supervisor.supervisor import IDENTITY_SCHEMA, STOP_SCHEMA

    closure, files = object_closure()
    tables = closure["tables"]
    publication = tables["research_object_publications"][0]
    scope = {
        k: publication[k] for k in ("owner_id", "run_id", "attempt_id", "generation")
    }
    admission_id, reservation_id = str(uuid4()), str(uuid4())
    stamp = publication["created_at"]
    amount = BudgetVector(
        storage_bytes=1179648, process_slots=1, wall_milliseconds=60000
    )
    control = tables["research_budget_controls"][0]
    control["limits"] = (
        BudgetVector.model_validate(control["limits"]).add(amount).model_dump()
    )
    control["reserved"] = (
        BudgetVector.model_validate(control["reserved"]).add(amount).model_dump()
    )
    tables["research_budget_reservations"].append(
        {
            "id": reservation_id,
            **scope,
            "created_at": stamp,
            "purpose": "process_unit",
            "logical_key": "pure_process",
            "amounts": amount.model_dump(),
            "state": "reserved",
        }
    )
    admission = {
        "id": admission_id,
        **scope,
        "role": "neutral_fixture",
        "logical_operation_id": str(uuid4()),
        "launch_ordinal": 1,
        "profile": "neutral_fixture.v1",
        "input_sha256": "a" * 64,
        "reservation_id": reservation_id,
        "image_id": "sha256:" + "b" * 64,
        "ticket_sha256": "c" * 64,
    }
    tables["research_process_admissions"] = [{**admission, "created_at": stamp}]
    tables["research_process_states"] = [
        {
            "admission_id": admission_id,
            **scope,
            "status": "stopped",
            "updated_at": stamp,
        }
    ]
    launch = {
        "schema_version": "mineagent.research-process-launch.v1",
        "admission": admission,
        "intent": {
            "schema_version": "mineagent.research-process-intent.v1",
            "intent_id": str(uuid4()),
            "admission_sha256": digest(admission),
            "issued_at": "2026-01-01T00:00:00+00:00",
            "not_after": "2026-01-01T00:01:00+00:00",
        },
        "runtime_name": "mineagent-research-" + admission_id,
        "root_device": 0,
        "root_inode": 1,
    }
    identity = {
        "schema_version": IDENTITY_SCHEMA,
        "launch_sha256": digest(launch),
        "admission_sha256": digest(admission),
        "container_id": "d" * 64,
        "host_pid": 1,
        "start_ticks": 1,
        "pid_namespace": "pid:[1]",
        "cgroup": "/pure-validator-fixture",
        "observed_state": "running",
    }
    stop = {
        "schema_version": STOP_SCHEMA,
        "launch_sha256": digest(launch),
        "container_id": identity["container_id"],
        "confirmed_absent": True,
    }
    tables["research_process_receipts"] = [
        {
            "id": str(uuid4()),
            **scope,
            "admission_id": admission_id,
            "kind": kind,
            "payload": payload,
            "payload_sha256": digest(payload),
            "created_at": stamp,
        }
        for kind, payload in (
            ("launch_intent", launch["intent"]),
            ("launch_identity", identity),
            ("stop", stop),
        )
    ]
    for name, payload in (("launch", launch), ("identity", identity), ("stop", stop)):
        files[
            f"research-processes/{scope['owner_id']}/{admission_id}/control/{name}.json"
        ] = canonical(payload)
    return closure, files


def test_pure_process_closure_binds_all_three_journals_to_immutable_receipts():
    closure, files = process_closure()
    verify(closure, files)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_stop",
        "missing_state",
        "running",
        "different_container",
        "different_admission",
        "missing_receipt",
        "unexpected_socket",
    ],
)
def test_pure_process_closure_rejects_unknown_or_substituted_stop(mutation):
    import json

    closure, files = process_closure()
    tables = closure["tables"]
    stop_key = next(key for key in files if key.endswith("/stop.json"))
    if mutation == "missing_stop":
        del files[stop_key]
    elif mutation == "missing_state":
        tables["research_process_states"] = []
    elif mutation == "running":
        tables["research_process_states"][0]["status"] = "running"
    elif mutation in {"different_container", "different_admission"}:
        key = (
            stop_key
            if mutation == "different_container"
            else stop_key.replace("/stop.json", "/launch.json")
        )
        value = json.loads(files[key])
        if mutation == "different_container":
            value["container_id"] = "f" * 64
        else:
            value["admission"]["logical_operation_id"] = str(uuid4())
        files[key] = canonical(value)
    elif mutation == "missing_receipt":
        tables["research_process_receipts"].pop()
    else:
        files[stop_key.replace("/control/stop.json", "/ipc/peer.sock")] = (
            b"not even a real socket"
        )
    with pytest.raises((ValueError, AssertionError)):
        verify(closure, files)


def test_process_intent_deadline_is_bounded_even_when_all_hashes_are_recomputed():
    import json

    from packages.research_runtime.process_contracts import digest

    closure, files = process_closure()
    prefix = next(k.rsplit("/", 1)[0] for k in files if k.endswith("/launch.json"))
    launch = json.loads(files[prefix + "/launch.json"])
    launch["intent"]["not_after"] = "2026-01-01T00:01:01+00:00"
    identity = json.loads(files[prefix + "/identity.json"])
    identity["launch_sha256"] = digest(launch)
    stop = json.loads(files[prefix + "/stop.json"])
    stop["launch_sha256"] = digest(launch)
    for name, payload in (("launch", launch), ("identity", identity), ("stop", stop)):
        files[prefix + "/" + name + ".json"] = canonical(payload)
    replacement = {
        "launch_intent": launch["intent"],
        "launch_identity": identity,
        "stop": stop,
    }
    for row in closure["tables"]["research_process_receipts"]:
        row["payload"] = replacement[row["kind"]]
        row["payload_sha256"] = digest(row["payload"])
    with pytest.raises(ValueError):
        verify(closure, files)


@pytest.mark.parametrize(
    "key",
    [
        "research-objects/not-a-uuid",
        f"research-objects/{uuid4()}/future",
        f"research-objects/{uuid4()}/by-kind/future-kind",
        f"research-processes/{uuid4()}/{uuid4()}/ipc",
    ],
)
def test_unknown_even_empty_neutral_directories_are_rejected(key):
    from backend.app.operations_backup import BackupError, _key

    with pytest.raises(BackupError):
        _key(key, storage=True)


@pytest.mark.parametrize(
    "mutation", [None, "wrong_context", "wrong_owner", "future_reason"]
)
def test_abort_context_evidence_preserves_ledger_and_rejects_substitution(mutation):
    from packages.research_runtime.broker_contracts import FixtureContext, digest

    closure, files = object_closure(uncertain=True)
    publication = closure["tables"]["research_object_publications"][0]
    scope = {
        key: publication[key]
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    }
    context_id = str(uuid4())
    context = FixtureContext(scenario="chat", ordinal=0)
    closure["tables"]["research_operation_contexts"] = [
        {
            "id": context_id,
            **scope,
            "created_at": publication["created_at"],
            "context": context.model_dump(mode="json"),
            "context_sha256": digest(context),
        }
    ]
    abort = {
        "id": str(uuid4()),
        **scope,
        "created_at": publication["created_at"],
        "context_id": context_id,
        "reason": "uncertain_transport",
    }
    closure["tables"]["research_context_aborts"] = [abort]
    if mutation is None:
        verify(closure, files)
        return
    if mutation == "wrong_context":
        abort["context_id"] = str(uuid4())
    elif mutation == "wrong_owner":
        abort["owner_id"] = str(uuid4())
    else:
        abort["reason"] = "future_reason"
    with pytest.raises(ValueError):
        verify(closure, files)

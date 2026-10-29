# ruff: noqa: F811
"""Actual retained control bytes; kernel/daemon observations remain test doubles."""

import hashlib
import json
from uuid import uuid4

import pytest

from backend.app import oe_structured_backup as service
from packages.factors.contracts import digest
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_persistence_codec import encode_row
from runtimes.openevolve_structured.profile import MAX_WALL_MS, STORAGE_FLOOR
from tests.structured_controller_runtime.test_completion import running


def test_literal_inventory_matches_all_twenty_owned_tables():
    from backend.app import (  # noqa: F401
        oe_structured_checkpoint_models,
        oe_structured_operational_models,
        oe_structured_sampling_models,
        oe_structured_terminal_models,
    )
    from backend.app.models import Base

    actual = {
        name: table
        for name, table in Base.metadata.tables.items()
        if name.startswith("oe_structured_")
    }
    assert len(actual) == len(service.FIELDS) == 20
    assert set(actual) == set(service.FIELDS)
    for name, table in actual.items():
        assert set(service.FIELDS[name].split()) == set(table.columns.keys())
    from sqlalchemy import UniqueConstraint

    assert set(service.UNIQUE_KEYS) == {
        (name, tuple(column.name for column in constraint.columns))
        for name, table in actual.items()
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def empty():
    return {
        "schema_version": service.SCHEMA,
        "tables": {name: [] for name in service.FIELDS},
        "external_bindings": {"runs": []},
    }


@pytest.mark.parametrize(
    "change", [None, "version", "missing_table", "extra_table", "bad_rows"]
)
def test_exact_separate_closure_envelope(change):
    value = empty()
    if change == "version":
        value["schema_version"] = "mineagent.openevolve-restore-closure.v1"
    elif change == "missing_table":
        value["tables"].pop("oe_structured_specs")
    elif change == "extra_table":
        value["tables"]["search_specs"] = []
    elif change == "bad_rows":
        value["tables"]["oe_structured_specs"] = {}
    if change:
        with pytest.raises(ValueError):
            service.indexes(value)
    else:
        assert len(service.indexes(value)) == 21


@pytest.fixture
def physical_graph(monkeypatch, tmp_path):
    value, operation = running(monkeypatch, tmp_path)
    stop = value.finish()
    admission = value.launch.intent.admission
    row = json.loads(json.dumps(encode_row(admission), default=str))
    row["created_at"] = "2026-10-26T00:00:00Z"
    scope = {k: row[k] for k in service.SCOPE}
    amounts = BudgetVector(
        process_slots=1, wall_milliseconds=MAX_WALL_MS, storage_bytes=STORAGE_FLOOR
    ).model_dump()
    reservation = {
        **scope,
        "id": row["reservation_id"],
        "purpose": "process_unit",
        "logical_key": "oe-structured-controller:" + row["logical_operation_id"],
        "amounts": amounts,
        "state": "settled",
    }
    physical = {}
    for path in (operation / "control").iterdir():
        raw = path.read_bytes()
        physical[str(path.relative_to(tmp_path))] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }
    completion = json.loads((operation / "control/completion.json").read_bytes())
    payloads = {
        "launch_intent": value.launch.intent.model_dump(mode="json"),
        "launch_identity": value.identity.model_dump(mode="json"),
        "controller_child": value.child.model_dump(mode="json"),
        "stop": stop.model_dump(mode="json"),
        "controller_completion": completion,
    }
    receipts = {}
    for kind, payload in payloads.items():
        identity = str(uuid4())
        receipts[identity] = {
            **scope,
            "id": identity,
            "admission_id": row["id"],
            "kind": kind,
            "payload": payload,
            "payload_sha256": digest(payload),
        }
    indexes = {
        "research_budget_reservations": {reservation["id"]: reservation},
        "research_budget_settlements": {
            "settlement": {
                **scope,
                "reservation_id": reservation["id"],
                "charge": amounts,
                "outcome": "conservative_unknown",
            }
        },
        "research_process_states": {row["id"]: {**scope, "status": "stopped"}},
        "research_process_receipts": receipts,
    }
    return tmp_path, row, indexes, physical


@pytest.mark.parametrize(
    "change",
    [
        None,
        "missing_completion",
        "missing_bytes",
        "changed_bytes",
        "wrong_scope",
        "unsettled",
        "partial_charge",
        "reused",
    ],
)
def test_actual_controller_five_receipt_chain(physical_graph, change):
    root, row, indexes, physical = physical_graph
    if change == "missing_completion":
        indexes["research_process_receipts"] = {
            k: v
            for k, v in indexes["research_process_receipts"].items()
            if v["kind"] != "controller_completion"
        }
    elif change == "missing_bytes":
        physical.pop(next(k for k in physical if k.endswith("completion.json")))
    elif change == "changed_bytes":
        path = next(root / k for k in physical if k.endswith("completion.json"))
        path.write_bytes(
            path.read_bytes().replace(b'"reports_eof":true', b'"reports_eof":false')
        )
    elif change == "wrong_scope":
        next(iter(indexes["research_process_receipts"].values()))["owner_id"] = str(
            uuid4()
        )
    elif change == "unsettled":
        indexes["research_budget_reservations"][row["reservation_id"]]["state"] = (
            "uncertain"
        )
    elif change == "partial_charge":
        indexes["research_budget_settlements"]["settlement"]["charge"] = (
            BudgetVector().model_dump()
        )
    allowed, used = set(), set()
    if change == "reused":
        used.add(row["reservation_id"])

    def read(key, sha, size):
        return (root / key).read_bytes()

    def check(key, sha, size):
        raw = read(key, sha, size)
        assert len(raw) == size and hashlib.sha256(raw).hexdigest() == sha

    if change:
        with pytest.raises(ValueError):
            service.validate_controller_closure_unit(
                row, indexes, physical, check, read, allowed, used
            )
    else:
        result = service.validate_controller_closure_unit(
            row, indexes, physical, check, read, allowed, used
        )
        assert set(result) == {
            "intent",
            "launch",
            "identity",
            "child",
            "stop",
            "completion",
        }
        assert used == {row["reservation_id"]} and allowed == set(physical)


@pytest.mark.parametrize("prefix", ["launch", "identity", "child", "stop"])
def test_incomplete_controller_retains_unknown_hold_without_success(
    physical_graph, prefix
):
    root, row, indexes, physical = physical_graph
    names = ("launch", "identity", "child", "stop", "completion")
    retained = set(names[: names.index(prefix) + 1])
    kinds = dict(
        zip(
            names,
            (
                "launch_intent",
                "launch_identity",
                "controller_child",
                "stop",
                "controller_completion",
            ),
            strict=True,
        )
    )
    indexes["research_process_receipts"] = {
        k: v
        for k, v in indexes["research_process_receipts"].items()
        if v["kind"] in {kinds[n] for n in retained}
    }
    physical = {
        k: v for k, v in physical.items() if k.rsplit("/", 1)[1][:-5] in retained
    }
    indexes["research_budget_reservations"][row["reservation_id"]]["state"] = (
        "uncertain"
    )
    indexes["research_budget_settlements"].clear()
    indexes["research_process_states"][row["id"]]["status"] = (
        "stopped" if prefix == "stop" else "uncertain"
    )
    result = service.validate_controller_closure_unit(
        row,
        indexes,
        physical,
        lambda *a: None,
        lambda key, sha, size: (root / key).read_bytes(),
        set(),
        set(),
    )
    assert result["completion"] is None
    assert (result["stop"] is not None) == (prefix == "stop")
    assert (
        indexes["research_budget_reservations"][row["reservation_id"]]["state"]
        == "uncertain"
    )


@pytest.mark.parametrize(
    "change",
    [
        None,
        "request_bytes",
        "receipt_bytes",
        "usage",
        "refund",
        "orphan_retry",
        "wrong_scope",
        "missing_stop",
    ],
)
def test_original_provider_bytes_and_exact_full_charge(monkeypatch, tmp_path, change):
    from backend.app import oe_structured_provider_evidence as live
    from tests.openevolve.test_structured_provider_evidence import fixture

    case = fixture(monkeypatch)

    def row(value):
        return json.loads(json.dumps(vars(value), default=str))

    shared = {}
    for table, values in {
        "research_model_operations": [case.operation],
        "research_model_transports": [case.transport],
        "research_model_outcomes": [case.outcome],
        "research_budget_reservations": [case.reservation],
        "research_budget_settlements": [case.settlement],
        "research_operation_contexts": [case.ctx],
        "research_context_aborts": [],
    }.items():
        shared[table] = {str(v.id): row(v) for v in values}
    owned = {
        "oe_structured_provider_transports": {
            str(case.membership.id): row(case.membership)
        },
        "oe_structured_provider_receipts": {str(v.id): row(v) for v in case.receipts},
    }
    shared["research_objects"] = {}
    specs = [
        (
            case.exchange.request_object_id,
            case.exchange.request_kind,
            case.exchange.request_sha256,
        ),
        (
            case.exchange.response_object_id,
            case.exchange.response_kind,
            case.exchange.response_sha256,
        ),
    ]
    specs += [
        (v.receipt_object_id, v.receipt_object_kind, v.receipt_sha256)
        for v in case.receipts
    ]
    for identity, kind, sha in specs:
        raw = live.read_research_object(None, None, identity, kind, sha, None)
        key = str(identity)
        (tmp_path / key).write_bytes(raw)
        shared["research_objects"][key] = {
            "id": key,
            "owner_id": str(case.fence.owner_id),
            "object_kind": kind,
            "sha256": sha,
            "size_bytes": len(raw),
            "storage_key": key,
        }
    if change in {"request_bytes", "receipt_bytes"}:
        identity = specs[0 if change == "request_bytes" else 2][0]
        (tmp_path / str(identity)).write_bytes(b"{}")
    elif change == "usage":
        shared["research_model_outcomes"][str(case.outcome.id)]["usage"] = (
            BudgetVector().model_dump()
        )
    elif change == "refund":
        shared["research_budget_settlements"][str(case.settlement.id)]["charge"] = (
            BudgetVector().model_dump()
        )
    elif change == "orphan_retry":
        duplicate = row(case.transport)
        duplicate["id"] = str(uuid4())
        shared["research_model_transports"][duplicate["id"]] = duplicate
    elif change == "wrong_scope":
        owned["oe_structured_provider_transports"][str(case.membership.id)][
            "owner_id"
        ] = str(uuid4())
    elif change == "missing_stop":
        owned["oe_structured_provider_receipts"] = {
            k: v
            for k, v in owned["oe_structured_provider_receipts"].items()
            if v["kind"] != "stopped"
        }
    read = lambda key, sha, size: (tmp_path / key).read_bytes()
    if change:
        with pytest.raises(ValueError):
            service.validate_provider_exchange(
                row(case.exchange), owned, shared, case.config.model_binding, read
            )
    else:
        assert service.validate_provider_exchange(
            row(case.exchange), owned, shared, case.config.model_binding, read
        ) == (case.request, case.response)


from tests.openevolve.test_structured_checkpoint import domain, native  # noqa: F401
from tests.openevolve.test_structured_checkpoint_publication import files  # noqa: F401
from tests.openevolve.test_structured_checkpoint_upload import transfer  # noqa: F401


@pytest.mark.parametrize(
    "change",
    [
        None,
        "missing_chunk",
        "changed_file",
        "different_state",
        "wrong_scope",
        "refunded_hold",
        "prefix",
        "completion_only",
    ],
)
def test_retained_checkpoint_reconstructs_original_chunks_and_native_state(
    transfer,
    tmp_path,
    change,
):
    from backend.app import oe_structured_checkpoint_services as live
    from packages.openevolve_structured.checkpoint import canonical as native_bytes
    from packages.openevolve_structured.upload_contracts import Begin
    from runtimes.openevolve_structured.checkpoint_transfer import transfer_checkpoint

    c = transfer
    transfer_checkpoint(
        c.send,
        token=c.token,
        files=tuple(sorted(c.files.items())),
        deadline=__import__("time").monotonic() + 10,
    )
    publication = live.publish_checkpoint(
        c.session,
        c.fence,
        c.epoch,
        native_bytes(
            {
                "token": str(c.token),
                "frontier": 0,
                "state_sha256": c.control.state_sha256,
                "path": f"/work/checkpoints/{c.token}",
            }
        ),
        None,
        peer=None,
    )

    def row(value):
        return json.loads(
            json.dumps(
                {k: v for k, v in vars(value).items() if not k.startswith("_")},
                default=str,
            )
        )

    owned = {name: {} for name in service.FIELDS}
    for value in c.rows:
        owned[value.__tablename__][str(value.id)] = row(value)
    owned["oe_structured_control_states"][str(c.control.id)] = row(c.control)
    upload = next(iter(owned["oe_structured_checkpoint_uploads"].values()))
    begin = Begin.model_validate_json(upload["inventory_canonical"])
    scope = {k: str(v) if k != "generation" else v for k, v in vars(c.fence).items()}
    shared = {
        "research_objects": {},
        "research_budget_settlements": {},
        "research_budget_reservations": {
            upload["reservation_id"]: {
                **scope,
                "id": upload["reservation_id"],
                "purpose": "object_storage",
                "logical_key": "oe-structured-upload:" + str(c.token),
                "state": "reserved",
                "amounts": BudgetVector(
                    storage_bytes=begin.transfer_peak_bytes
                ).model_dump(),
            }
        },
    }
    for identity, (kind, raw) in c.contents.items():
        key = str(identity)
        (tmp_path / key).write_bytes(raw)
        shared["research_objects"][key] = {
            "id": key,
            "owner_id": scope["owner_id"],
            "object_kind": kind,
            "storage_key": key,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }
    if change == "missing_chunk":
        owned["oe_structured_checkpoint_chunks"].pop(
            next(iter(owned["oe_structured_checkpoint_chunks"]))
        )
    elif change == "changed_file":
        obj = next(
            v
            for v in shared["research_objects"].values()
            if v["object_kind"] == "oe_structured_checkpoint_file"
        )
        (tmp_path / obj["storage_key"]).write_bytes(b"{}")
    elif change == "different_state":
        owned["oe_structured_control_states"][str(c.control.id)]["state_sha256"] = (
            "0" * 64
        )
    elif change == "wrong_scope":
        upload["owner_id"] = str(uuid4())
    elif change == "refunded_hold":
        shared["research_budget_reservations"][upload["reservation_id"]]["state"] = (
            "settled"
        )
    read = lambda key, sha, size: (tmp_path / key).read_bytes()
    if change == "prefix":
        owned["oe_structured_checkpoint_publications"].clear()
        owned["oe_structured_checkpoint_completions"].clear()
        owned["oe_structured_checkpoint_chunks"] = {
            k: v
            for k, v in owned["oe_structured_checkpoint_chunks"].items()
            if v["sequence"] < 2
        }
        assert service.validate_checkpoint_prefix(upload, owned, shared, read) == begin
        return
    if change == "completion_only":
        owned["oe_structured_checkpoint_publications"].clear()
        completion = next(iter(owned["oe_structured_checkpoint_completions"].values()))
        assert (
            service.validate_checkpoint_completion(
                completion, owned, shared, c.domain, read
            )
            == c.state
        )
        return
    if change:
        with pytest.raises(ValueError):
            service.validate_checkpoint_publication(
                row(publication), owned, shared, c.domain, read
            )
    else:
        assert (
            service.validate_checkpoint_publication(
                row(publication), owned, shared, c.domain, read
            )
            == c.state
        )


from tests.structured.test_reconstruction import retained_declaration  # noqa: F401


def execution_material(retained_declaration):
    from types import SimpleNamespace as NS

    from packages.factors.contracts import canonical_bytes
    from packages.openevolve_structured.candidate import (
        CandidateDocument,
        TypedFrozenCandidateDomain,
    )
    from packages.openevolve_structured.provider_run import FixedExecutionInputs
    from packages.structured_factor_strategy.contracts import StructuredProgram
    from packages.structured_factor_strategy.run import StructuredRunConfig

    args, retained_files, expected = retained_declaration
    old = StructuredRunConfig.model_validate_json(args["spec"]["config_canonical"])
    program = StructuredProgram.model_validate_json(
        args["version"]["program_canonical"]
    )
    manifest = expected.snapshot
    fixed = FixedExecutionInputs(
        snapshot_id=manifest.snapshot_id,
        snapshot_manifest_sha256=digest(manifest),
        snapshot_input_sha256=manifest.input_sha256,
        snapshot_output_sha256=manifest.output_rows_sha256,
        factor_version_id=manifest.input.version.factor_version_id,
        dataset_version_id=old.dataset_version_id,
        dataset_sha256=old.dataset_sha256,
        source_policy=old.source_policy,
        market_sha256=expected.market_sha256,
        market_size_bytes=args["execution"]["market_size_bytes"],
        release_receipt_ids=(uuid4(),),
    )
    # Data-only assembly seam; full run authority is tested by declarations graph.
    config = NS(
        fixed_inputs=fixed,
        search=NS(
            domain=TypedFrozenCandidateDomain(seed_program=program),
            objective=NS(window=old.execution_window, initial_cash=old.initial_cash),
        ),
        runtime=NS(
            engine_image_id=old.engine_image_id,
            engine_installed_parent_image_id=old.engine_installed_parent_image_id,
            engine_dependency_lock_sha256=old.engine_dependency_lock_sha256,
        ),
    )
    execution = dict(
        args["execution"],
        candidate_id=str(uuid4()),
        program_version_id=args["version"]["id"],
    )
    candidate = {k: execution[k] for k in service.SCOPE}
    candidate.update(
        id=execution["candidate_id"],
        program_version_id=execution["program_version_id"],
        program_sha256=digest(program),
        candidate_canonical=canonical_bytes(
            CandidateDocument(program=program)
        ).decode(),
    )
    return args, retained_files, expected, config, execution, candidate


@pytest.mark.parametrize(
    "change", [None, "preview", "rows", "snapshot", "owner", "market"]
)
def test_normal_engine_input_rebuilds_actual_source_without_old_run_identity(
    retained_declaration,
    change,
):
    args, retained_files, expected, config, execution, candidate = execution_material(
        retained_declaration
    )
    manifest = expected.snapshot
    if change == "preview":
        retained_files[args["dataset"]["storage_key"] + "/preview.json"] += b" "
    elif change == "rows":
        retained_files[manifest.assembled_rows.storage_key] += b" "
    elif change == "snapshot":
        execution["snapshot_id"] = str(uuid4())
    elif change == "owner":
        candidate["owner_id"] = str(uuid4())
    elif change == "market":
        execution["market_sha256"] = "0" * 64
    if change:
        with pytest.raises(ValueError):
            service.reconstruct_execution(
                execution,
                candidate,
                config,
                args["publication"],
                args["dataset"],
                args["read_bytes"],
            )
    else:
        value, market = service.reconstruct_execution(
            execution,
            candidate,
            config,
            args["publication"],
            args["dataset"],
            args["read_bytes"],
        )
        assert (
            value == expected
            and hashlib.sha256(market).hexdigest() == expected.market_sha256
        )


@pytest.mark.parametrize(
    "change",
    [None, "omitted_run", "wrong_origin", "request", "config", "active", "seed_owner"],
)
def test_exact_normal_user_declaration_and_shared_schema_partition(change):
    from tests.openevolve.test_structured_persisted_identity import graph

    _, run, spec, version, _, _ = graph()

    def row(value):
        return json.loads(
            json.dumps(
                {k: v for k, v in vars(value).items() if not k.startswith("_")},
                default=str,
            )
        )

    run = row(run)
    run["status"] = "cancelled"
    spec, version = row(spec), row(version)
    submission = {
        "id": spec["submission_id"],
        "owner_id": run["owner_id"],
        "run_id": run["id"],
        "origin": "authenticated_user_submission",
        **{
            k: spec[k]
            for k in (
                "request_canonical",
                "request_sha256",
                "preflight_sha256",
                "config_sha256",
            )
        },
    }
    owned = {
        "research_runs": {run["id"]: run},
        "oe_structured_specs": {run["id"]: spec},
        "oe_structured_submissions": {submission["id"]: submission},
    }
    shared = {
        run["id"]: {
            k: run[k]
            for k in ("id", "owner_id", "generation", "purpose", "engine", "status")
        }
    }
    shared[run["id"]]["config_schema_version"] = run["config"]["schema_version"]
    if change == "omitted_run":
        owned["research_runs"].clear()
    elif change == "wrong_origin":
        submission["origin"] = "workflow_stage"
    elif change == "request":
        submission["request_sha256"] = "0" * 64
    elif change == "config":
        spec["config_sha256"] = "0" * 64
    elif change == "active":
        run["status"] = shared[run["id"]]["status"] = "running"
    elif change == "seed_owner":
        version["owner_id"] = str(uuid4())
    if change:
        with pytest.raises(ValueError):
            service.validate_declarations(owned, shared, {version["id"]: version})
    else:
        assert set(
            service.validate_declarations(owned, shared, {version["id"]: version})
        ) == {run["id"]}


def _predecessors():
    from backend.app.research_backup import FIELDS

    shared = {
        "schema_version": service.RESEARCH_SCHEMA,
        "tables": {name: [] for name in FIELDS},
        "external_bindings": {"runs": [], "attempts": []},
    }
    execution = {
        "tables": {
            "factor_execution_snapshot_publications": [],
            "factor_execution_snapshot_outcomes": [],
        }
    }
    analysis = {"tables": {"dataset_versions": [], "data_usage_exposures": []}}
    structured = {"tables": {"structured_strategy_versions": []}}
    return {
        "research_closure": shared,
        "execution_closure": execution,
        "direct_analysis_closure": analysis,
        "structured_closure": structured,
        "inventory": [],
        "read_bytes": lambda *a: pytest.fail("No files in empty closure"),
    }


def test_top_level_empty_explicit_successor_is_not_old_protocol():
    args = _predecessors()
    assert service.validate_closure(empty(), **args) == {
        "configs": {},
        "measurements": {},
        "epochs": {},
    }
    args["research_closure"]["schema_version"] = (
        "mineagent.research-restore-closure.v10"
    )
    with pytest.raises(ValueError, match="research-v11"):
        service.validate_closure(empty(), **args)


def test_top_level_cancelled_declaration_still_requires_original_release():
    from tests.openevolve.test_structured_persisted_identity import graph

    _, run, spec, version, _, _ = graph()
    args = _predecessors()
    closure = empty()

    def row(value, fields):
        return json.loads(
            json.dumps(
                {k: getattr(value, k, None) for k in fields.split()}, default=str
            )
        )

    run = row(run, service.RUN_FIELDS)
    run["status"] = "cancelled"
    spec = row(spec, service.FIELDS["oe_structured_specs"])
    version = row(
        version,
        "id owner_id parent_version_id program program_canonical program_sha256",
    )
    submission = {k: None for k in service.FIELDS["oe_structured_submissions"].split()}
    submission.update(
        id=spec["submission_id"],
        owner_id=run["owner_id"],
        run_id=run["id"],
        idempotency_key="cancelled-before-claim",
        origin="authenticated_user_submission",
        **{
            k: spec[k]
            for k in (
                "request_canonical",
                "request_sha256",
                "preflight_sha256",
                "config_sha256",
            )
        },
    )
    closure["tables"]["oe_structured_specs"] = [spec]
    closure["tables"]["oe_structured_submissions"] = [submission]
    closure["external_bindings"]["runs"] = [run]
    bound_run = {
        k: run[k]
        for k in ("id", "owner_id", "generation", "purpose", "engine", "status")
    }
    bound_run["config_schema_version"] = run["config"]["schema_version"]
    args["research_closure"]["external_bindings"]["runs"] = [bound_run]
    args["structured_closure"]["tables"]["structured_strategy_versions"] = [version]
    args["direct_analysis_closure"]["tables"]["dataset_versions"] = [
        {"owner_id": run["owner_id"], "id": spec["dataset_id"]}
    ]
    args["execution_closure"]["tables"]["factor_execution_snapshot_outcomes"] = [
        {"owner_id": run["owner_id"], "publication_id": spec["snapshot_id"]}
    ]
    with pytest.raises(ValueError, match="source release bindings missing"):
        service.validate_closure(closure, **args)


@pytest.mark.parametrize(
    "change",
    [
        None,
        "score",
        "artifact",
        "missing_terminal",
        "gap",
        "missing_result",
        "no_final_saves",
    ],
)
def test_historical_population_requires_exact_measurement_and_terminal_rows(
    native, domain, change
):
    from types import SimpleNamespace as NS

    from backend.app.oe_structured_population import measurement_feedback
    from packages.openevolve_structured.checkpoint import canonical as native_bytes
    from packages.openevolve_structured.checkpoint import (
        state_sha256,
        validate_directory,
    )
    from packages.openevolve_structured.objective import StructuredMeasurement

    # Explicit measurement double tests graph equality only; real engine byte
    # reconstruction is a separate mandatory top-level dependency.
    state = validate_directory(native, domain)
    pid = next(iter(state["programs"]))
    scope = {
        "owner_id": str(uuid4()),
        "run_id": str(uuid4()),
        "attempt_id": str(uuid4()),
        "generation": 1,
    }
    epoch = {**scope, "id": str(uuid4())}
    candidate = {
        **scope,
        "id": str(uuid4()),
        "epoch_id": epoch["id"],
        "upstream_program_id": pid,
        "ordinal": 0,
        "parent_candidate_id": None,
        "program_version_id": str(uuid4()),
        "candidate_canonical": state["programs"][pid]["code"],
    }
    measurement = StructuredMeasurement(
        execution_id=uuid4(),
        structured_strategy_version_id=__import__("uuid").UUID(
            candidate["program_version_id"]
        ),
        snapshot_id=uuid4(),
        strategy_input_sha256="a" * 64,
        market_sha256="b" * 64,
        result_sha256="c" * 64,
        objective_sha256="d" * 64,
        total_return_pct=-4.0,
        max_drawdown_pct=1.0,
        closed_trade_count=0,
        eligible=True,
        combined_score=-5.0,
        reason="eligible",
    )
    state["programs"][pid]["metrics"] = {"combined_score": measurement.combined_score}
    state["programs"][pid]["artifacts_json"] = native_bytes(
        {"structured_feedback": measurement_feedback(measurement)}
    ).decode()
    if change == "score":
        state["programs"][pid]["metrics"]["combined_score"] = 99.0
    elif change == "artifact":
        state["programs"][pid]["artifacts_json"] = (
            '{"structured_feedback":"caller prose"}'
        )
    cursor = {
        **scope,
        "id": str(uuid4()),
        "epoch_id": epoch["id"],
        "sequence": 1,
        "frontier": 0,
        "phase": "seed",
        "previous_id": None,
        "state_canonical": native_bytes(state).decode(),
        "state_sha256": state_sha256(state),
    }
    event = {
        "iteration": 0,
        "outcome": "seed",
        "native_state": state,
        "state_sha256": cursor["state_sha256"],
    }
    terminal = {
        **scope,
        "id": str(uuid4()),
        "epoch_id": epoch["id"],
        "ordinal": 0,
        "control_state_id": cursor["id"],
        "worker_outcome_id": None,
        "outcome": "seed",
        "event_canonical": native_bytes(event).decode(),
        "event_sha256": hashlib.sha256(native_bytes(event)).hexdigest(),
    }
    own = {name: {} for name in service.FIELDS}
    own["research_runs"] = {scope["run_id"]: {"status": "failed"}}
    for table, value in (
        ("oe_structured_candidates", candidate),
        ("oe_structured_control_states", cursor),
        ("oe_structured_terminal_applications", terminal),
    ):
        own[table][value["id"]] = value
    measurements = {candidate["id"]: measurement}
    if change == "missing_result":
        measurements.clear()
    elif change == "gap":
        cursor["sequence"] = 3
    elif change == "missing_terminal":
        own["oe_structured_terminal_applications"].clear()
    elif change == "no_final_saves":
        own["research_runs"][scope["run_id"]]["status"] = "succeeded"
    config = NS(search=NS(domain=domain, maximum_mutation_slots=1))
    from packages.research_runtime.process_contracts import ProcessError

    if change:
        with pytest.raises((ValueError, ProcessError)):
            service.validate_epoch_graph(
                epoch,
                config,
                own,
                {},
                measurements,
                lambda *a: pytest.fail("No checkpoint publication"),
            )
    else:
        assert service.validate_epoch_graph(
            epoch,
            config,
            own,
            {},
            measurements,
            lambda *a: pytest.fail("No checkpoint publication"),
        ) == {cursor["id"]: state}


@pytest.mark.parametrize(
    "change",
    [
        None,
        "extra_transport",
        "foreign_transport",
        "missing_transport",
        "extra_operation",
    ],
)
def test_provider_reverse_inventory_rejects_unregistered_retry(change):
    shared = {
        "research_model_operations": {"op": {"id": "op", "run_id": "run"}},
        "research_model_transports": {
            "transport": {"id": "transport", "run_id": "run", "operation_id": "op"}
        },
    }
    members = {"member": {"operation_id": "op", "transport_id": "transport"}}
    if change in {"extra_transport", "foreign_transport"}:
        shared["research_model_transports"]["orphan"] = {
            "id": "orphan",
            "run_id": "foreign" if change == "foreign_transport" else "run",
            "operation_id": "op",
        }
    elif change == "missing_transport":
        shared["research_model_transports"].clear()
    elif change == "extra_operation":
        shared["research_model_operations"]["orphan"] = {
            "id": "orphan",
            "run_id": "run",
        }
    if change:
        with pytest.raises(ValueError, match="reverse inventory"):
            service.validate_provider_inventory({"run"}, members, shared)
    else:
        service.validate_provider_inventory({"run"}, members, shared)


@pytest.mark.parametrize("change", [None, "duplicate", "nullable"])
def test_literal_unique_keys_cover_unreferenced_terminal_aliases(change):
    own = {name: {} for name in service.FIELDS}
    columns = service.FIELDS["oe_structured_provider_receipts"].split()
    first = {key: "first-" + key for key in columns}
    second = {key: "second-" + key for key in columns}
    if change == "duplicate":
        second["membership_id"] = first["membership_id"]
        second["kind"] = first["kind"]
    elif change == "nullable":
        first["membership_id"] = second["membership_id"] = None
        second["kind"] = first["kind"]
    own["oe_structured_provider_receipts"] = {"first": first, "second": second}
    if change == "duplicate":
        with pytest.raises(ValueError, match="unique key"):
            service.validate_unique_keys(own)
    else:
        service.validate_unique_keys(own)


@pytest.mark.parametrize(
    "change", [None, "id", "source", "evidence", "dataset", "owner"]
)
def test_source_release_requires_original_exact_ledger(retained_declaration, change):
    from types import SimpleNamespace

    from backend.app.experiment_services import dataset_identities

    args, _, expected, config, _, _ = execution_material(retained_declaration)
    publication = dict(args["publication"])
    publication.update(
        id=str(expected.snapshot.snapshot_id),
        run_id=str(expected.snapshot.run_id),
        owner_id=str(expected.owner_id),
    )
    fixed = config.fixed_inputs
    dataset = args["dataset"]
    outcome = {
        "publication_id": publication["id"],
        "owner_id": publication["owner_id"],
        "run_id": publication["run_id"],
        "manifest_sha256": fixed.snapshot_manifest_sha256,
        "input_sha256": fixed.snapshot_input_sha256,
        "output_sha256": fixed.snapshot_output_sha256,
    }
    exposure = {
        "id": str(fixed.release_receipt_ids[0]),
        "owner_id": publication["owner_id"],
        "run_id": publication["run_id"],
        "source_key": f"engine-result:execution_snapshot:{fixed.snapshot_id}:factor:v1",
        "dataset_version_id": dataset["id"],
        "purpose": "engine_execution_snapshot_release",
        "start_date": dataset["start_date"],
        "end_date": dataset["end_date"],
        "identities": dataset_identities(
            SimpleNamespace(manifest=dataset["manifest"], market=dataset["market"])
        ),
        "provenance": {
            "run_id": publication["run_id"],
            "execution_snapshot_id": publication["id"],
            "manifest_sha256": fixed.snapshot_manifest_sha256,
            "input_sha256": fixed.snapshot_input_sha256,
            "output_sha256": fixed.snapshot_output_sha256,
            "extent": "conservative_full_retained_dataset",
            "qualification": "historical_disclosure_not_live_execution_authority",
            "role": "factor",
            "dataset_version_id": dataset["id"],
            "dataset_content_sha256": fixed.dataset_sha256,
        },
    }
    if change == "id":
        exposure["id"] = str(uuid4())
    elif change == "source":
        exposure["source_key"] += "-other"
    elif change == "evidence":
        exposure["provenance"]["output_sha256"] = "0" * 64
    elif change == "dataset":
        exposure["dataset_version_id"] = str(uuid4())
    elif change == "owner":
        exposure["owner_id"] = str(uuid4())
    if change:
        with pytest.raises(ValueError):
            service.validate_source_release(
                str(expected.owner_id),
                config,
                publication,
                outcome,
                dataset,
                [exposure],
            )
    else:
        service.validate_source_release(
            str(expected.owner_id), config, publication, outcome, dataset, [exposure]
        )


@pytest.mark.parametrize("change", [None, "result", "measurement", "stop", "source"])
def test_complete_engine_byte_reader_rebuilds_input_and_result(
    retained_declaration, tmp_path, change
):
    """Real retained codecs/files; broker/accounting and stop observations are doubles."""
    from uuid import UUID

    from packages.factors.contracts import canonical_bytes
    from packages.openevolve_structured.objective import (
        StructuredObjective,
        project_result,
    )
    from packages.structured_factor_strategy.operator import ClosedExchange
    from packages.structured_factor_strategy.wire import (
        StructuredResult,
        accounting_only,
        read_result,
        write_input,
    )
    from runtimes.portable_engine.export import export_result
    from runtimes.portable_engine.runtime import Controller, load_inputs
    from runtimes.structured_factor_engine.journal import StructuredInputBinding
    from tests.portable.test_engine import FakeAQ, FakeBroker, fake_result
    from tests.structured_factor_strategy.test_process_journal import records

    args, retained, value, config, execution, candidate = execution_material(
        retained_declaration
    )
    _, market = service.reconstruct_execution(
        execution,
        candidate,
        config,
        args["publication"],
        args["dataset"],
        args["read_bytes"],
    )
    strategy = write_input(value)
    native = load_inputs(
        canonical_bytes(value.engine), market, calendar_verifier=lambda *_: None
    )  # Explicit calendar double; not native certification.
    exchange = ClosedExchange(value)
    controller = Controller(native, exchange)
    broker = FakeBroker(native.sessions[0])
    for session in native.sessions:
        broker.session = session
        controller.on_session(session, broker, FakeAQ)
    result = StructuredResult(
        execution_id=value.execution_id,
        structured_strategy_version_id=value.structured_strategy_version_id,
        snapshot_id=value.snapshot.snapshot_id,
        strategy_input_sha256=hashlib.sha256(strategy).hexdigest(),
        market_sha256=value.market_sha256,
        decisions=tuple(exchange.records),
        accounting_tables=accounting_only(
            export_result(fake_result(controller, broker), controller)
        ),
    )
    raw = canonical_bytes(result)
    assert read_result(raw, value) == result
    config.search.objective = StructuredObjective(
        window=value.engine.window, initial_cash=value.engine.initial_cash
    )
    measurement = project_result(strategy, raw, config.search.objective)
    launch, identity, stop, extraction = records()
    binding = StructuredInputBinding(
        execution_id=value.execution_id,
        snapshot_id=value.snapshot.snapshot_id,
        structured_strategy_version_id=value.structured_strategy_version_id,
        strategy_input_sha256=hashlib.sha256(strategy).hexdigest(),
        strategy_input_size_bytes=len(strategy),
        market_sha256=value.market_sha256,
        market_size_bytes=len(market),
    )
    admission = launch.intent.admission.model_copy(
        update={
            **{k: UUID(execution[k]) for k in ("owner_id", "run_id", "attempt_id")},
            "logical_operation_id": value.execution_id,
            "image_id": config.runtime.engine_image_id,
            "input_sha256": digest(binding),
        }
    )
    intent = launch.intent.model_copy(
        update={"admission": admission, "inputs": binding}
    )
    launch = launch.model_copy(update={"intent": intent})
    identity = identity.model_copy(
        update={
            "admission_sha256": digest(admission),
            "launch_sha256": digest(launch),
            "image_id": admission.image_id,
        }
    )
    stop = stop.model_copy(
        update={"launch_sha256": digest(launch), "identity_sha256": digest(identity)}
    )
    extraction = extraction.model_copy(
        update={
            "launch_sha256": digest(launch),
            "identity_sha256": digest(identity),
            "strategy_input_sha256": binding.strategy_input_sha256,
            "market_sha256": binding.market_sha256,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }
    )
    a = {
        k: str(v) if hasattr(v, "hex") else v for k, v in encode_row(admission).items()
    }
    scope = {k: a[k] for k in service.SCOPE}
    held = {
        **scope,
        "id": a["reservation_id"],
        "state": "settled",
        "purpose": "process_unit",
        "logical_key": "structured-factor-execution:" + execution["id"],
        "amounts": BudgetVector(
            evaluator_executions=1,
            process_slots=1,
            wall_milliseconds=90000,
            storage_bytes=268500992,
        ).model_dump(),
    }
    shared = {
        "research_process_admissions": {a["id"]: a},
        "research_budget_reservations": {held["id"]: held},
        "research_budget_settlements": {
            "settled": {
                **scope,
                "reservation_id": held["id"],
                "charge": held["amounts"],
                "outcome": "conservative_unknown",
            }
        },
        "research_process_states": {a["id"]: {**scope, "status": "stopped"}},
        "research_process_receipts": {},
    }
    outcome = {
        **scope,
        "execution_id": execution["id"],
        "candidate_id": candidate["id"],
        "admission_id": a["id"],
        "result_canonical": raw.decode(),
        "result_sha256": hashlib.sha256(raw).hexdigest(),
        "result_size_bytes": len(raw),
        "measurement_canonical": canonical_bytes(measurement).decode(),
        "measurement_sha256": digest(measurement),
        "objective_sha256": digest(config.search.objective),
    }
    prefix = f"research-processes/{a['owner_id']}/{a['id']}/"
    retained = dict(retained)
    for kind, name, document, payload in (
        ("launch_intent", "launch", launch, intent),
        ("launch_identity", "identity", identity, identity),
        ("stop", "stop", stop, stop),
        ("extraction", "extraction", extraction, extraction),
    ):
        rid = str(uuid4())
        shared["research_process_receipts"][rid] = {
            **scope,
            "id": rid,
            "admission_id": a["id"],
            "kind": kind,
            "payload": payload.model_dump(mode="json"),
            "payload_sha256": digest(payload),
        }
        retained[prefix + "control/" + name + ".json"] = canonical_bytes(document)
        if kind in {"stop", "extraction"}:
            outcome.update(
                {
                    kind + "_receipt_id": rid,
                    kind + "_receipt_kind": kind,
                    kind + "_receipt_sha256": digest(payload),
                }
            )
    retained[prefix + "output/result.json"] = raw
    physical = {}
    for key, content in retained.items():
        path = tmp_path / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        physical[key] = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }
    if change == "result":
        (tmp_path / (prefix + "output/result.json")).write_bytes(b"{}")
    elif change == "measurement":
        outcome["measurement_sha256"] = "0" * 64
    elif change == "stop":
        outcome["stop_receipt_id"] = str(uuid4())
    elif change == "source":
        (tmp_path / (args["dataset"]["storage_key"] + "/preview.json")).write_bytes(
            b"{}"
        )
    read = lambda key, sha, size: (tmp_path / key).read_bytes()
    if change:
        with pytest.raises(ValueError):
            service.validate_engine_outcome(
                outcome,
                execution,
                candidate,
                config,
                args["publication"],
                args["dataset"],
                shared,
                physical,
                read,
            )
    else:
        assert (
            service.validate_engine_outcome(
                outcome,
                execution,
                candidate,
                config,
                args["publication"],
                args["dataset"],
                shared,
                physical,
                read,
            )
            == measurement
        )


def test_committed_controller_intent_without_physical_launch_stays_held(physical_graph):
    _, row, shared, _ = physical_graph
    shared["research_process_receipts"] = {
        k: v
        for k, v in shared["research_process_receipts"].items()
        if v["kind"] == "launch_intent"
    }
    shared["research_process_states"][row["id"]]["status"] = "launch_intended"
    shared["research_budget_reservations"][row["reservation_id"]]["state"] = "reserved"
    shared["research_budget_settlements"].clear()

    def no_read(*args):
        raise AssertionError("Intent-only graph has no physical bytes")

    used = set()
    actual = service.validate_controller_closure_unit(
        row, shared, {}, no_read, no_read, set(), used
    )
    assert actual["intent"].admission.id.hex == __import__("uuid").UUID(row["id"]).hex
    assert actual["launch"] is actual["completion"] is actual["stop"] is None
    assert used == {row["reservation_id"]}

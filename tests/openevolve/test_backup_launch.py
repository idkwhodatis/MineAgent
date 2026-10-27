"""Typed canonical bootstrap/process projection doubles, not PG/Docker proof."""

import hashlib
from copy import deepcopy
from uuid import UUID, uuid4

import pytest

from backend.app.openevolve_backup_launch import validate_launches
from packages.openevolve_adapter.checkpoint_native import canonical
from packages.research_runtime.broker_contracts import digest
from packages.research_runtime.process_contracts import digest as process_digest
from packages.research_runtime.process_persistence_codec import encode_row
from runtimes.openevolve_operational.bootstrap import input_inventory
from runtimes.openevolve_operational.process_journal import validate_records
from tests.openevolve.operational_fixtures import records
from tests.openevolve.test_operational_native_process import case as native_case


@pytest.fixture
def case(monkeypatch, tmp_path):
    base = native_case.__wrapped__(monkeypatch, tmp_path)
    from backend.app.openevolve_controller_services import typed_bootstrap

    bootstrap, raw = typed_bootstrap(base.launch)
    seed = b"x = 1\n"
    inventory, _ = input_inventory(raw, seed)
    launch, identity, child, stop = records(tmp_path)
    scope = {
        k: getattr(base.fence, k)
        for k in ("owner_id", "run_id", "attempt_id", "generation")
    }
    operation = uuid4()
    admission = launch.intent.admission.model_copy(
        update=dict(
            scope,
            id=bootstrap.admission_id,
            logical_operation_id=operation,
            image_id=bootstrap.spec.runtime_image,
            input_sha256=inventory,
        )
    )
    intent = type(launch.intent).model_validate(
        launch.intent.model_copy(
            update={
                "admission": admission,
                "bootstrap_sha256": hashlib.sha256(raw).hexdigest(),
                "bootstrap_size_bytes": len(raw),
                "seed_sha256": hashlib.sha256(seed).hexdigest(),
                "seed_size_bytes": len(seed),
                "issued_at": bootstrap.issued_at,
                "not_after": bootstrap.not_after,
            }
        ).model_dump()
    )
    launch = type(launch).model_validate(
        launch.model_copy(
            update={
                "intent": intent,
                "runtime_name": "mineagent-oe-controller-" + str(admission.id),
            }
        ).model_dump()
    )
    identity = identity.model_copy(
        update={
            "admission_sha256": process_digest(admission.model_dump(mode="json")),
            "launch_sha256": process_digest(launch.model_dump(mode="json")),
            "image_id": admission.image_id,
        }
    )
    child = child.model_copy(
        update={
            "admission_sha256": identity.admission_sha256,
            "identity_sha256": process_digest(identity.model_dump(mode="json")),
        }
    )
    stop = stop.model_copy(
        update={
            "launch_sha256": identity.launch_sha256,
            "identity_sha256": child.identity_sha256,
            "child_sha256": process_digest(child.model_dump(mode="json")),
        }
    )
    validate_records(launch, identity, child, stop)
    scope = {k: str(v) if isinstance(v, UUID) else v for k, v in scope.items()}
    spec, native = bootstrap.spec, bootstrap.native_inputs
    epoch = dict(
        scope,
        id=str(bootstrap.epoch_id),
        admission_id=str(admission.id),
        ordinal=1,
        reason="initial",
        first_native_iteration=1,
        spec_sha256=spec.sha256,
        runtime_profile_sha256=bootstrap.runtime_profile_sha256,
        bootstrap_sha256=inventory,
    )
    binding = dict(
        scope,
        id=str(operation),
        epoch_id=epoch["id"],
        admission_id=str(admission.id),
        bootstrap=bootstrap.model_dump(mode="json"),
        bootstrap_canonical=raw.decode(),
        bootstrap_sha256=hashlib.sha256(raw).hexdigest(),
        input_inventory_sha256=inventory,
        physical_profile_sha256=bootstrap.physical_profile_sha256,
    )
    profile = {
        "run_id": str(spec.run_id),
        "owner_id": str(spec.owner_id),
        "profile_canonical": canonical(native.model_dump(mode="json")).decode(),
        "profile": native.model_dump(mode="json"),
        "profile_sha256": digest(native),
        "execution_sha256": digest(spec.execution),
    }
    source = {
        "id": str(spec.preparation.prepared.strategy_version_id),
        "owner_id": str(spec.owner_id),
        "code": seed.decode(),
        "content_sha256": hashlib.sha256(seed).hexdigest(),
    }
    closure = {
        "tables": {
            "strategy_search_epochs": [epoch],
            "search_controller_launches": [binding],
            "search_resolved_execution_profiles": [profile],
        },
        "external_bindings": {"strategy_versions": [source]},
    }
    row = {
        k: str(v) if isinstance(v, UUID) else v
        for k, v in encode_row(admission).items()
    }
    research = {"tables": {"research_process_admissions": [row]}}
    return (
        closure,
        research,
        {str(spec.run_id): spec},
        {
            str(admission.id): {
                "launch": launch,
                "identity": identity,
                "child": child,
                "stop": stop,
            }
        },
    )


def test_exact_canonical_epoch_and_stopped_controller(case):
    validate_launches(*case)


@pytest.mark.parametrize(
    "table,key,value",
    [
        ("strategy_search_epochs", "ordinal", 2),
        ("strategy_search_epochs", "reason", "resume"),
        ("strategy_search_epochs", "first_native_iteration", 2),
        ("strategy_search_epochs", "attempt_id", str(uuid4())),
        ("strategy_search_epochs", "bootstrap_sha256", "0" * 64),
        ("search_controller_launches", "physical_profile_sha256", "0" * 64),
        ("search_controller_launches", "bootstrap_canonical", "{}"),
        ("search_controller_launches", "id", str(uuid4())),
        ("search_resolved_execution_profiles", "profile_sha256", "0" * 64),
        ("search_resolved_execution_profiles", "owner_id", str(uuid4())),
    ],
)
def test_exact_binding_rejects_substitution(case, table, key, value):
    case[0]["tables"][table][0][key] = value
    with pytest.raises(ValueError):
        validate_launches(*case)


def test_retained_seed_bytes_not_just_declared_hash(case):
    case[0]["external_bindings"]["strategy_versions"][0]["code"] += "x = 2\n"
    with pytest.raises(ValueError):
        validate_launches(*case)


@pytest.mark.parametrize(
    "table",
    [
        "strategy_search_epochs",
        "search_controller_launches",
        "search_resolved_execution_profiles",
    ],
)
def test_duplicate_or_missing_projection(case, table):
    rows = case[0]["tables"][table]
    rows.append(deepcopy(rows[0]))
    with pytest.raises(ValueError):
        validate_launches(*case)
    rows.clear()
    with pytest.raises(ValueError):
        validate_launches(*case)


def test_no_detached_process_without_positive_closure(case):
    case[3].clear()
    with pytest.raises(ValueError):
        validate_launches(*case)


def test_orphan_controller_rejected(case):
    row = deepcopy(case[1]["tables"]["research_process_admissions"][0])
    row["id"] = str(uuid4())
    case[1]["tables"]["research_process_admissions"].append(row)
    with pytest.raises(ValueError):
        validate_launches(*case)


def test_empty_pre_epoch_inventory_has_no_execution_claim():
    validate_launches(
        {
            "tables": {
                "strategy_search_epochs": [],
                "search_controller_launches": [],
                "search_resolved_execution_profiles": [],
            },
            "external_bindings": {"strategy_versions": []},
        },
        {"tables": {"research_process_admissions": []}},
        {},
        {},
    )


def test_missing_stop_is_never_an_admitted_cold_unit(case):
    next(iter(case[3].values()))["stop"] = None
    with pytest.raises(ValueError):
        validate_launches(*case)


def test_stopped_before_child_is_not_a_success_claim(case):
    record = next(iter(case[3].values()))
    record.pop("child")
    record["stop"] = record["stop"].model_copy(
        update={
            "child_sha256": None,
            "child_kernel_identity_absent": None,
        }
    )
    validate_launches(*case)


def test_raw_profile_canonical_not_reencoded_json(case):
    row = case[0]["tables"]["search_resolved_execution_profiles"][0]
    row["profile_canonical"] += " "
    with pytest.raises(ValueError):
        validate_launches(*case)

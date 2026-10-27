"""024 retained initial-controller binding; never image or launch authority.

Shape/spec and physical process validators are mandatory preceding layers. This
joins their exact immutable records; no current daemon lookup, synthetic fence,
resume permission, or claim that retained image metadata installs a runtime.
"""

import hashlib
from types import SimpleNamespace
from uuid import UUID

from backend.app.openevolve_controller_services import typed_bootstrap
from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.execution_inputs import ResolvedNativeInputs
from packages.openevolve_adapter.operational_profile import require_initial_profile
from packages.openevolve_adapter.runtime_profile import PROFILE as SEARCH_PROFILE
from packages.research_runtime.broker_contracts import digest
from packages.research_runtime.process_persistence_codec import encode_row
from runtimes.openevolve_operational.bootstrap import input_inventory
from runtimes.openevolve_operational.process_journal import (
    PROFILE,
    ControllerIdentity,
    ControllerLaunch,
    ControllerStop,
    validate_records,
)
from runtimes.openevolve_operational.profile import profile_sha256
from runtimes.openevolve_operational.recipe_pins import require_declared_recipe_pins

SCOPE = ("owner_id", "run_id", "attempt_id", "generation")


def _index(rows, key):
    result = {row[key]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError("OE duplicate launch/profile inventory")
    return result


def validate_launches(closure, research, specs, process_records):
    """Bind all epochs and controller records; failed pre-epoch runs may be empty.

    process_records is keyed by canonical admission ID and supplied ONLY by the
    physical process validator (including positive stop and conservative charge).
    A stopped-before-child controller is retained containment, not success.
    """
    tables = closure["tables"]
    epochs = _index(tables["strategy_search_epochs"], "id")
    launches = _index(tables["search_controller_launches"], "epoch_id")
    profiles = _index(tables["search_resolved_execution_profiles"], "run_id")
    sources = _index(closure["external_bindings"]["strategy_versions"], "id")
    admissions = _index(research["tables"]["research_process_admissions"], "id")
    if set(epochs) != set(launches) or len(
        {e["run_id"] for e in epochs.values()}
    ) != len(epochs):
        raise ValueError("OE exact one initial launch per epoch/run required")
    native_profiles = {}
    for run_id, row in profiles.items():
        spec = specs.get(run_id)
        if spec is None or row["owner_id"] != str(spec.owner_id):
            raise ValueError("OE profile owner/spec differs")
        raw = row["profile_canonical"].encode("utf-8")
        if not 0 < len(raw) <= 262144:
            raise ValueError("OE native profile bound")
        native = ResolvedNativeInputs.model_validate_json(raw)
        if (
            canonical(native.model_dump(mode="json")) != raw
            or native.model_dump(mode="json") != row["profile"]
            or row["profile_sha256"] != digest(native)
            or row["execution_sha256"] != digest(spec.execution)
        ):
            raise ValueError("OE canonical native profile differs")
        require_declared_recipe_pins(spec)
        require_initial_profile(spec, native)
        native_profiles[run_id] = native
    used = set()
    for epoch_id, epoch in epochs.items():
        binding = launches[epoch_id]
        spec = specs.get(epoch["run_id"])
        if (
            spec is None
            or epoch["owner_id"] != str(spec.owner_id)
            or type(epoch["ordinal"]) is not int
            or epoch["ordinal"] != 1
            or epoch["reason"] != "initial"
            or type(epoch["first_native_iteration"]) is not int
            or epoch["first_native_iteration"] != 1
            or epoch["spec_sha256"] != spec.sha256
            or epoch["runtime_profile_sha256"] != SEARCH_PROFILE.sha256
            or any(binding[k] != epoch[k] for k in SCOPE)
            or binding["admission_id"] != epoch["admission_id"]
        ):
            raise ValueError("OE initial epoch scope/profile differs")
        bootstrap, raw = typed_bootstrap(SimpleNamespace(**binding))
        if (
            bootstrap.spec != spec
            or bootstrap.native_inputs != native_profiles.get(epoch["run_id"])
            or str(bootstrap.epoch_id) != epoch_id
            or str(bootstrap.admission_id) != epoch["admission_id"]
            or binding["physical_profile_sha256"] != profile_sha256()
        ):
            raise ValueError("OE bootstrap epoch/spec/native identity differs")
        source = sources.get(str(spec.preparation.prepared.strategy_version_id))
        if source is None or source["owner_id"] != epoch["owner_id"]:
            raise ValueError("OE owned seed absent")
        seed = source["code"].encode("utf-8")
        inventory_sha, _ = input_inventory(raw, seed)
        if (
            source["content_sha256"] != hashlib.sha256(seed).hexdigest()
            or inventory_sha != binding["input_inventory_sha256"]
            or inventory_sha != epoch["bootstrap_sha256"]
        ):
            raise ValueError("OE retained seed/input inventory differs")
        admission_id = epoch["admission_id"]
        records = process_records.get(admission_id)
        if (
            records is None
            or type(records.get("launch")) is not ControllerLaunch
            or type(records.get("identity")) is not ControllerIdentity
            or type(records.get("stop")) is not ControllerStop
        ):
            raise ValueError("OE positive controller process closure required")
        launch = records["launch"]
        validate_records(
            launch, records["identity"], records.get("child"), records["stop"]
        )
        intent, admission = launch.intent, launch.intent.admission
        row = admissions.get(admission_id)
        encoded = {
            k: str(v) if isinstance(v, UUID) else v
            for k, v in encode_row(admission).items()
        }
        if (
            row is None
            or any(row[k] != v for k, v in encoded.items())
            or str(admission.id) != admission_id
            or admission_id in used
            or any(str(getattr(admission, k)) != str(epoch[k]) for k in SCOPE)
            or str(admission.logical_operation_id) != binding["id"]
            or admission.launch_ordinal != 1
            or admission.image_id != spec.runtime_image
            or admission.input_sha256 != inventory_sha
            or intent.bootstrap_sha256 != binding["bootstrap_sha256"]
            or intent.bootstrap_size_bytes != len(raw)
            or intent.seed_sha256 != hashlib.sha256(seed).hexdigest()
            or intent.seed_size_bytes != len(seed)
            or (intent.issued_at, intent.not_after)
            != (bootstrap.issued_at, bootstrap.not_after)
        ):
            raise ValueError("OE physical intent/epoch input binding differs")
        used.add(admission_id)
    retained = {key for key, row in admissions.items() if row["profile"] == PROFILE}
    physical = {
        key
        for key, value in process_records.items()
        if type(value.get("launch")) is ControllerLaunch
    }
    if used != retained or used != physical:
        raise ValueError("OE orphan controller process or launch")

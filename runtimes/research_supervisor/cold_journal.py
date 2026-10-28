"""Offline stopped-process inventory, not launch or result authority.

Restoration changes the storage inode. Historical launch/identity hashes must
still agree, but must not be compared to the new root. Database closure and
physical output checks remain independently mandatory in the backup reader.
"""

import importlib
import os

from packages.research_runtime.process_contracts import ProcessError, canonical
from runtimes.research_supervisor.numeric_journal import read_numeric_record
from runtimes.research_supervisor.supervisor import _directory

# Closed dispatch: no module or model name is taken from stored input.
_FORMATS = {
    "mineagent.rd-controller-launch.v1": (
        "runtimes.rdagent_operational.process_journal",
        "Controller",
        "validate_records",
    ),
    "mineagent.oe-controller-launch.v1": (
        "runtimes.openevolve_operational.process_journal",
        "Controller",
        "validate_records",
    ),
    "mineagent.qlib-analysis-process-launch.v1": (
        "packages.research_runtime.analysis_process_journal",
        "Analysis",
        "validate_analysis_journal",
    ),
    "mineagent.openevolve-native-process-launch.v1": (
        "runtimes.openevolve_evaluator.journal",
        "Native",
        "validate_native_journal",
    ),
    "mineagent.structured-factor-engine-process-launch.v1": (
        "runtimes.structured_factor_engine.journal",
        "Structured",
        "validate_structured_journal",
    ),
}


def _names(path):
    fd = _directory(path)
    try:
        return set(os.listdir(fd))
    finally:
        os.close(fd)


def validate_modern_quiescence(operation, schema):
    """Return False only for an unknown schema; malformed known formats fail."""
    if schema not in _FORMATS:
        return False
    module_name, prefix, validator_name = _FORMATS[schema]
    module = importlib.import_module(module_name)
    control = operation / "control"

    def read(name, suffix):
        return getattr(module, prefix + suffix).model_validate_json(
            canonical(read_numeric_record(control / (name + ".json")))
        )

    launch = read("launch", "Launch")
    admission = launch.intent.admission
    if (str(admission.owner_id), str(admission.id)) != (
        operation.parent.name,
        operation.name,
    ):
        raise ProcessError("research_process_journal_identity")
    identity, stop = read("identity", "Identity"), read("stop", "Stop")
    expected = {"launch.json", "identity.json", "stop.json"}
    if prefix == "Controller":
        child = None
        if stop.child_sha256 is not None:
            child = read("child", "Child")
            expected.add("child.json")
        module.validate_records(launch, identity, child, stop)
        if _names(operation) != {"control"}:
            raise ProcessError("research_process_journal_inventory")
    else:
        extraction = None
        if "extraction.json" in _names(control):
            extraction = read("extraction", "Extraction")
            expected.add("extraction.json")
        getattr(module, validator_name)(launch, identity, stop, extraction)
        # Stopped evaluators retain an output directory, including empty output
        # on failure. Inputs must have been removed by positive-stop cleanup.
        if _names(operation) != {"control", "output"}:
            raise ProcessError("research_process_journal_inventory")
        if _names(operation / "output") != (
            set() if extraction is None else {extraction.basename}
        ):
            raise ProcessError("research_process_journal_inventory")
    if _names(control) != expected:
        raise ProcessError("research_process_journal_inventory")
    return True

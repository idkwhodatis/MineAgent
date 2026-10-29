"""Fresh physical rejection read. No side effects, settlement or recovery grant."""

import os

from packages.research_runtime.numeric_rejection import (
    NumericRejection,
    validate_rejection_journal,
)
from packages.research_runtime.process_contracts import ProcessError, canonical
from runtimes.research_supervisor.numeric_journal import (
    read_numeric_journal,
    read_numeric_record,
)
from runtimes.research_supervisor.supervisor import _directory, operation_path


def read_rejection_journal(root, admission):
    launch, identity, stop, _ = read_numeric_journal(root, admission)
    path = operation_path(root, admission)
    control = _directory(path / "control")
    output = None
    try:
        output = _directory(path / "output")
        # A rejection cannot coexist with success or an unqualified retained
        # payload. Do not follow a symlink or silently treat it as absence.
        if set(os.listdir(control)) != {
            "launch.json",
            "identity.json",
            "stop.json",
            "rejection.json",
        } or os.listdir(output):
            raise ProcessError("numeric_rejection_inventory")
        rejection = NumericRejection.model_validate_json(
            canonical(read_numeric_record(path / "control" / "rejection.json"))
        )
    finally:
        if output is not None:
            os.close(output)
        os.close(control)
    return validate_rejection_journal(launch, identity, stop, rejection)

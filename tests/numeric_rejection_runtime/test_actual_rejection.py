"""Two genuine isolated candidate failures; component gate, not DB acceptance.

Required mode has no skips and does not substitute transport/kernel doubles.
The parent composed026 gate must additionally exercise persisted feedback.
"""

import hashlib
import os
import re
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from packages.research_runtime.numeric_process_journal import (
    NumericInput,
    NumericInputFile,
    NumericIntent,
)
from packages.research_runtime.process_contracts import digest
from packages.research_runtime.process_declarations_v2 import (
    NoIPC,
    ProcessDeclarationV2,
)
from runtimes.factor_execution.acceptance import frozen_inputs
from runtimes.research_supervisor.numeric_rejection import read_rejection_journal
from runtimes.research_supervisor.numeric_supervisor import (
    NumericSupervisor,
    _mint_numeric_authority,
)


@pytest.mark.parametrize(
    "source,expected",
    [
        (b"raise RuntimeError('maintained failure')\n", "child_nonzero"),
        (b"pass\n", "output_rejected"),
    ],
)
def test_actual_kind3_requires_observed_identity_eof_and_whole_stop(
    tmp_path, source, expected
):
    if os.environ.get("MINEAGENT_NUMERIC_REJECTION_REQUIRED") != "1":
        pytest.skip("Explicit real numeric rejection gate not requested")
    candidate = os.environ.get("MINEAGENT_NUMERIC_CANDIDATE_IMAGE", "")
    normalizer = os.environ.get("MINEAGENT_NUMERIC_NORMALIZER_IMAGE", "")
    assert all(
        re.fullmatch(r"sha256:[0-9a-f]{64}", image) for image in (candidate, normalizer)
    )
    prefix, inputs, _ = frozen_inputs(candidate, normalizer, source_bytes=source)
    binding = NumericInput(
        schema_version="mineagent.numeric-process-input.v2",
        source_object_id=uuid4(),
        source_sha256=hashlib.sha256(source).hexdigest(),
        prefix_sha256=prefix.sha256,
        cutoff=prefix.host_cutoff,
        files=tuple(
            NumericInputFile(
                destination=name,
                sha256=hashlib.sha256(raw).hexdigest(),
                size_bytes=len(raw),
            )
            for name, raw in sorted(inputs.items())
        ),
    )
    admission = ProcessDeclarationV2(
        schema_version="mineagent.process-admission.v2",
        **{
            key: uuid4()
            for key in (
                "id",
                "owner_id",
                "run_id",
                "attempt_id",
                "logical_operation_id",
                "reservation_id",
            )
        },
        generation=1,
        launch_ordinal=1,
        role="factor_cutoff_candidate",
        profile="mineagent.factor-prefix-candidate.v2",
        image_id=candidate,
        input_sha256=digest(binding.model_dump(mode="json")),
        ipc=NoIPC(ipc_policy="none", ticket_sha256=None),
    )
    supervisor = NumericSupervisor(candidate)
    inventory = supervisor.verified_inventory("candidate")
    issued = datetime.now(UTC)
    intent = NumericIntent(
        schema_version="mineagent.numeric-process-intent.v2",
        id=uuid4(),
        admission=admission,
        inputs=binding,
        issued_at=issued,
        not_after=issued + timedelta(seconds=8),
    )
    authority = _mint_numeric_authority(intent, inputs, inventory)
    observed = []
    result = supervisor.execute_with_rejection(
        tmp_path, authority, on_identity=observed.append
    )
    assert len(observed) == 1 and observed[0].container_id == result.container_id
    assert result.outcome == expected
    assert time.monotonic() <= authority.deadline + 1
    launch, identity, stop, retained = read_rejection_journal(tmp_path, admission)
    assert retained == result and identity == observed[0] and stop.deadline_bound_met
    assert supervisor.client._inspect(identity.container_id) is None
    assert supervisor.client._inspect(launch.runtime_name) is None

"""Installed030 signed rejection predicate matrix, not physical journal evidence."""

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from packages.rdagent_adapter.numeric_profile import CANDIDATE_LIMITS
from packages.research_runtime.numeric_process_journal import (
    NumericIdentity,
    NumericInput,
    NumericInputFile,
    NumericIntent,
)
from packages.research_runtime.numeric_rejection import NumericRejection
from packages.research_runtime.process_contracts import digest
from packages.research_runtime.process_declarations_v2 import (
    NoIPC,
    ProcessDeclarationV2,
)
from runtimes.factor_execution.protected_output import rejection_metadata


def _rejection(code):
    raw = rejection_metadata("result.h5", code)
    return NumericRejection(
        schema_version="mineagent.numeric-process-rejection.v1",
        launch_sha256="a" * 64,
        identity_sha256="b" * 64,
        stop_sha256="c" * 64,
        container_id="d" * 64,
        protocol="MANU2-kind3",
        basename="result.h5",
        payload_utf8=raw.decode(),
        payload_sha256=hashlib.sha256(raw).hexdigest(),
        child_exit_code=code,
        descendants_reaped=True,
        complete_eof=True,
    ).model_dump(mode="json")


def _unsigned_documents():
    now = datetime(2024, 1, 1, tzinfo=UTC)
    inputs = NumericInput(
        schema_version="mineagent.numeric-process-input.v2",
        source_object_id=UUID(int=1),
        source_sha256="a" * 64,
        prefix_sha256="a" * 64,
        cutoff=now,
        files=tuple(
            NumericInputFile(destination=name, sha256="a" * 64, size_bytes=10)
            for name in sorted(CANDIDATE_LIMITS)
        ),
    )
    admission = ProcessDeclarationV2(
        schema_version="mineagent.process-admission.v2",
        **{
            key: UUID(int=index)
            for index, key in enumerate(
                (
                    "id",
                    "owner_id",
                    "run_id",
                    "attempt_id",
                    "logical_operation_id",
                    "reservation_id",
                ),
                10,
            )
        },
        generation=1,
        launch_ordinal=1,
        role="factor_cutoff_candidate",
        profile="mineagent.factor-prefix-candidate.v2",
        input_sha256=digest(inputs.model_dump(mode="json")),
        image_id="sha256:" + "a" * 64,
        ipc=NoIPC(ipc_policy="none", ticket_sha256=None),
    )
    intent = NumericIntent(
        schema_version="mineagent.numeric-process-intent.v2",
        id=UUID(int=20),
        admission=admission,
        inputs=inputs,
        issued_at=now,
        not_after=now + timedelta(seconds=8),
    )
    identity = NumericIdentity(
        schema_version="mineagent.numeric-process-identity.v2",
        admission_sha256=digest(admission.model_dump(mode="json")),
        launch_sha256="a" * 64,
        container_id="b" * 64,
        image_id=admission.image_id,
        root_device=1,
        root_inode=2,
        init_pid=100,
        init_start_ticks=200,
        boot_id=UUID(int=30),
        pid_namespace_inode=300,
    )
    return intent, identity


def _case(label, kind, value, wanted):
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).replace("'", "''")
    expected = "true" if wanted else "false"
    return f"DO $$ BEGIN IF mineagent_numeric_rejection_payload('{kind}','{raw}'::jsonb) IS DISTINCT FROM {expected} THEN RAISE EXCEPTION 'Signed rejection case {label} differs'; END IF; END $$;"


def signed_statements():
    statements = []
    for code in (-64, -9, -1, 0, 1, 255):
        statements.append(_case(f"valid_{code}", "rejection", _rejection(code), True))
    for index, code in enumerate((-65, 256, -9.0, 0.5, "-9", True, None)):
        bad = _rejection(-9)
        bad["child_exit_code"] = code
        statements.append(_case(f"invalid_{index}", "rejection", bad, False))
    for label, change in (
        ("missing", lambda v: v.pop("child_exit_code")),
        ("extra", lambda v: v.update(extra=1)),
        ("tag", lambda v: v.update(schema_version="other")),
        ("proof", lambda v: v.update(descendants_reaped=False)),
    ):
        bad = _rejection(-9)
        change(bad)
        statements.append(_case(label, "rejection", bad, False))
    intent, identity = _unsigned_documents()
    for kind, model, paths in (
        (
            "launch_intent",
            intent,
            (("admission", "generation"), ("admission", "launch_ordinal")),
        ),
        (
            "launch_identity",
            identity,
            (("init_pid",), ("init_start_ticks",), ("root_inode",)),
        ),
    ):
        value = model.model_dump(mode="json")
        statements.append(_case(kind + "_baseline", kind, value, True))
        for path in paths:
            bad = copy.deepcopy(value)
            selected = bad
            for key in path[:-1]:
                selected = selected[key]
            selected[path[-1]] = -1
            statements.append(_case("negative_" + path[-1], kind, bad, False))
    statements.append(
        "DO $$ BEGIN IF mineagent_rd_schema('-9'::jsonb,'{\"type\":\"integer\"}'::jsonb,'{}'::jsonb,0) IS NOT FALSE THEN RAISE EXCEPTION 'Global integer grammar widened'; END IF; END $$;"
    )
    assert len(statements) == 25
    return [*statements, "SELECT 'DECLARED_UPLOAD_SIGNED_MATRIX_COMPLETE';"]

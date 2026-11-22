"""Structured issuer/transport doubles only; no Docker result or admission claim."""

import hashlib
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from packages.research_runtime.process_contracts import ProcessError, digest
from packages.structured_factor_strategy.wire import write_input
from runtimes.research_supervisor.structured_factor_supervisor import (
    StructuredAuthority,
    StructuredSupervisor,
    _mint_structured_authority,
    check_input_bytes,
)
from runtimes.structured_factor_engine.journal import StructuredIntent
from runtimes.structured_factor_engine.protected_output import frame
from runtimes.structured_factor_engine.transport import read_structured_frame
from tests.structured.test_operator import declared_input
from tests.structured_factor_strategy.test_process_journal import records


def bound():
    launch, *_ = records()
    market = b"{}"
    value = declared_input()
    document = value.model_dump(mode="json")
    document["market_sha256"] = hashlib.sha256(market).hexdigest()
    document["engine"]["input_identity"]["market_data_sha256"] = document[
        "market_sha256"
    ]
    value = type(value).model_validate_json(__import__("json").dumps(document))
    raw = write_input(value)
    binding = launch.intent.inputs.model_copy(
        update={
            "execution_id": value.execution_id,
            "structured_strategy_version_id": value.structured_strategy_version_id,
            "snapshot_id": value.snapshot.snapshot_id,
            "strategy_input_sha256": hashlib.sha256(raw).hexdigest(),
            "strategy_input_size_bytes": len(raw),
            "market_sha256": document["market_sha256"],
            "market_size_bytes": len(market),
        }
    )
    admission = launch.intent.admission.model_copy(
        update={
            "owner_id": value.owner_id,
            "run_id": value.run_id,
            "attempt_id": value.attempt_id,
            "generation": value.generation,
            "logical_operation_id": value.execution_id,
            "input_sha256": digest(binding.model_dump(mode="json")),
        }
    )
    now = datetime.now(UTC)
    intent = StructuredIntent(
        id=admission.id,
        admission=admission,
        inputs=binding,
        issued_at=now,
        not_after=now + timedelta(seconds=60),
    )
    return intent, {"/input/strategy.json": raw, "/input/market.json": market}


def test_input_identity_and_no_detached_authority():
    intent, files = bound()
    check_input_bytes(intent, files)
    with pytest.raises(ProcessError):
        StructuredAuthority(object(), intent, files, {})
    with pytest.raises(ProcessError):
        check_input_bytes(
            intent,
            {**files, "/input/strategy.json": files["/input/strategy.json"] + b" "},
        )
    with pytest.raises(ProcessError):
        check_input_bytes(intent, {**files, "/input/source.py": b"evil"})


def test_one_shot_private_authority():
    intent, files = bound()
    authority = _mint_structured_authority(
        intent, files, {"image_id": intent.admission.image_id}
    )
    assert authority.consume()["image_id"] == intent.admission.image_id
    with pytest.raises(ProcessError):
        authority.consume()


def test_closed_create_profile():
    intent, _ = bound()
    launch = records()[0].model_copy(
        update={
            "intent": intent,
            "runtime_name": "mineagent-factor-strategy-" + str(intent.id),
        }
    )
    supervisor = StructuredSupervisor(intent.admission.image_id, "sha256:" + "f" * 64)
    args = supervisor._create_args(
        launch,
        {
            "/input/strategy.json": "/private/input",
            "/input/market.json": "/private/market",
        },
    )
    assert args[args.index("--network") + 1] == "none"
    assert args[args.index("--ipc") + 1] == "none"
    assert args[args.index("--memory") + 1] == "1g"
    assert args[args.index("--pids-limit") + 1] == "64"
    assert "type=bind,src=/private/input,dst=/input/strategy.json,readonly" in args
    assert not any("socket" in item for item in args)


def test_staged_readonly_inputs_pass_precreate_verification_under_private_umask(
    tmp_path,
):
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
from pathlib import Path
from stat import S_IMODE
from sys import argv
from unittest.mock import patch

from tests.structured_factor_strategy.test_supervisor import bound
from runtimes.research_supervisor.structured_factor_supervisor import (
    StructuredSupervisor, _mint_structured_authority, operation_path,
)

class CreateReached(Exception):
    pass

root = Path(argv[1])
intent, files = bound()
supervisor = StructuredSupervisor(intent.admission.image_id, "sha256:" + "f" * 64)
authority = _mint_structured_authority(intent, files, {"image_id": intent.admission.image_id})
operation = operation_path(root, intent.admission)

def command(args):
    assert args[0] == "create"
    assert S_IMODE((operation / "control" / "launch.json").stat().st_mode) == 0o600
    for destination, expected in files.items():
        source = operation / "inputs" / Path(destination).name
        assert S_IMODE(source.stat().st_mode) == 0o444
        assert source.read_bytes() == expected
        assert f"type=bind,src={source},dst={destination},readonly" in args
    raise CreateReached()

# Real staging, durable journal readback and both input verifications; no
# Docker invocation or synthetic containment receipt.
with patch.object(supervisor.client, "_command", command), patch.object(
    supervisor, "stop_and_verify"
) as stop:
    try:
        supervisor.execute(root, authority, on_identity=lambda _: None)
    except CreateReached:
        stop.assert_called_once()
    else:
        raise AssertionError("structured supervisor never reached Docker create")
""",
            str(tmp_path),
        ],
        check=True,
        timeout=15,
        umask=0o077,
    )


class Reader:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def read_frame(self, **_):
        return next(self.chunks, None)


def test_protected_stream_cannot_accept_legacy_or_duplicates():
    data = frame(2, b"opaque")
    assert read_structured_frame(Reader([(1, data[:7]), (1, data[7:])]), 0) == (
        2,
        b"opaque",
    )
    for invalid in (b"MANU2\0" + data[6:], data + data):
        with pytest.raises(ValueError):
            read_structured_frame(Reader([(1, invalid)]), 0)


def test_both_input_hashes_and_scope_are_exact():
    intent, files = bound()
    with pytest.raises(ProcessError):
        check_input_bytes(intent, {**files, "/input/market.json": b"[]"})
    other = bound()[0]
    with pytest.raises(ProcessError):
        check_input_bytes(other, files)

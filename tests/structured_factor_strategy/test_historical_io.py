"""Relocated private files + synthetic records; NOT an actual PG restore claim."""

import shutil

import pytest

from backend.app.factor_native_io import NativeStorageError, native_storage_guard
from backend.app.structured_historical_io import read_historical_structured_bytes
from packages.research_runtime.process_contracts import ProcessError
from runtimes.research_supervisor.structured_factor_journal import (
    read_structured_journal,
)
from runtimes.research_supervisor.structured_factor_supervisor import (
    StructuredSupervisor,
)
from tests.structured_factor_strategy.test_files import fixture


def test_historical_relocation_does_not_change_live_or_launch_authority(tmp_path):
    original = tmp_path / "original"
    original.mkdir(mode=0o700)
    launch, identity, stop, extraction, raw, _ = fixture(original)
    (original / "factors").mkdir(mode=0o700)
    restored = tmp_path / "restored"
    shutil.copytree(original, restored)
    assert original.stat().st_ino != restored.stat().st_ino
    with pytest.raises(ProcessError, match="root_identity"):
        read_structured_journal(restored, launch.intent.admission, require_output=True)
    kwargs = {
        "intent": launch.intent,
        "identity": identity,
        "stop": stop,
        "extraction": extraction,
    }
    with pytest.raises(NativeStorageError):
        read_historical_structured_bytes(restored, **kwargs)
    with native_storage_guard(restored):
        history = read_historical_structured_bytes(restored, **kwargs)
    assert history.result_bytes == raw
    assert history.qualification == "historical_integrity_not_live_authority"
    supervisor = StructuredSupervisor(
        launch.intent.admission.image_id, "sha256:" + "f" * 64
    )
    with pytest.raises(ProcessError, match="authority"):
        supervisor.execute(restored, history, on_identity=lambda _: None)


def test_historical_receipt_substitution_still_rejected(tmp_path):
    launch, identity, stop, extraction, _, _ = fixture(tmp_path)
    (tmp_path / "factors").mkdir(mode=0o700)
    with native_storage_guard(tmp_path), pytest.raises(ProcessError):
        read_historical_structured_bytes(
            tmp_path,
            intent=launch.intent,
            identity=identity,
            stop=stop,
            extraction=extraction.model_copy(update={"sha256": "f" * 64}),
        )


def test_overdue_containment_is_not_historical_result(tmp_path):
    from runtimes.research_supervisor.supervisor import operation_path, write_record

    launch, identity, stop, extraction, _, _ = fixture(tmp_path)
    (tmp_path / "factors").mkdir(mode=0o700)
    stop = stop.model_copy(update={"deadline_bound_met": False})
    path = operation_path(tmp_path, launch.intent.admission) / "control" / "stop.json"
    path.unlink()  # Replace only this synthetic test journal, never production evidence.
    write_record(
        path,
        stop.model_dump(mode="json"),
    )
    with native_storage_guard(tmp_path), pytest.raises(ProcessError):
        read_historical_structured_bytes(
            tmp_path,
            intent=launch.intent,
            identity=identity,
            stop=stop,
            extraction=extraction,
        )

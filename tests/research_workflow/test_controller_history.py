"""Actual retained-file integrity/relocation; kernel identities are test fixtures."""

import shutil
import stat

import pytest

from backend.app.factor_native_io import native_storage_guard
from backend.app.rdagent_controller_history import read_historical_controller_records
from packages.research_runtime.process_contracts import ProcessError
from tests.rdagent.test_rdagent_controller_process import records, write


@pytest.mark.parametrize("change", [None, "missing_stop", "extra", "tamper"])
def test_actual_controller_history_after_relocation(tmp_path, change):
    original = tmp_path / "old"
    original.mkdir()
    (original / "factors").mkdir()
    launch, identity, child, stop = values = records(original)
    write(original, values)
    restored = tmp_path / "restored"
    shutil.copytree(original, restored)
    shutil.rmtree(original)
    control = (
        restored
        / "research-processes"
        / str(launch.intent.admission.owner_id)
        / str(launch.intent.admission.id)
        / "control"
    )
    with native_storage_guard(restored):
        assert (
            read_historical_controller_records(
                restored,
                intent=launch.intent,
                identity=identity,
                child=child,
                stop=stop,
            )
            == values
        )
    if change == "missing_stop":
        (control / "stop.json").unlink()
    elif change == "extra":
        (control / "fake.json").write_text("{}")
    elif change == "tamper":
        (control / "stop.json").write_text("{}")
    # The actual backup/restore copier seals restored files read-only.
    for path in control.iterdir():
        path.chmod(0o400)
    from runtimes.research_supervisor.numeric_journal import read_numeric_record

    with pytest.raises(ProcessError, match="numeric_process_journal"):
        read_numeric_record(control / "launch.json")
    with native_storage_guard(restored):
        if change:
            with pytest.raises((ProcessError, ValueError)):
                read_historical_controller_records(
                    restored,
                    intent=launch.intent,
                    identity=identity,
                    child=child,
                    stop=stop,
                )
        else:
            observed = read_historical_controller_records(
                restored,
                intent=launch.intent,
                identity=identity,
                child=child,
                stop=stop,
            )
            assert observed == values
            assert all(
                stat.S_IMODE(path.stat().st_mode) == 0o400 for path in control.iterdir()
            )
            assert observed[0].root_inode != restored.stat().st_ino

            (control / "launch.json").chmod(0o640)
            with pytest.raises(ProcessError, match="historical_process_file_integrity"):
                read_historical_controller_records(
                    restored,
                    intent=launch.intent,
                    identity=identity,
                    child=child,
                    stop=stop,
                )
            (control / "launch.json").chmod(0o400)

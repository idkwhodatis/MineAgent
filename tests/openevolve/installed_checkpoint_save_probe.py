"""Installed native saver + operational ordinals; synthetic host, no PG authority."""

import hashlib
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from uuid import uuid4

from openevolve.config import DatabaseConfig
from openevolve.controller import OpenEvolve
from openevolve.database import Program, ProgramDatabase

from packages.openevolve_adapter import observer
from packages.openevolve_adapter.checkpoint_native import (
    canonical,
    snapshot,
    validate_directory,
)
from runtimes.openevolve_operational import transport


def main():
    database = ProgramDatabase(
        DatabaseConfig(num_islands=1, population_size=4, archive_size=4)
    )
    program = Program(
        id=str(uuid4()), code="# fixture\n", metrics={"combined_score": -0.25}
    )
    database.add(program)
    database.last_iteration = 2
    controller = object.__new__(OpenEvolve)
    controller.database = database
    controller.file_extension = ".py"
    calls = []
    with tempfile.TemporaryDirectory(prefix="oe024-save-probe-") as root:

        def exchange(kind, payload, **kwargs):
            assert kind == "checkpoint_prepare"
            ordinal = payload["save_ordinal"]
            destination = Path(root) / str(uuid4())
            destination.mkdir()
            calls.append((ordinal, destination, payload))
            return {"token": destination.name, "path": str(destination)}

        def before(frontier, state):
            return transport.host_event(
                "checkpoint_prepare",
                {
                    "frontier": frontier,
                    "state_sha256": hashlib.sha256(canonical(state)).hexdigest(),
                },
            )

        def after(token, frontier, state):
            assert validate_directory(Path(root) / token) == state

        with (
            patch.object(observer, "require_trusted_population"),
            patch.object(observer, "before_checkpoint", before),
            patch.object(observer, "after_checkpoint", after),
            patch.object(transport, "_SAVE_ORDINAL", 2),
            patch.object(transport, "_bootstrap", lambda: NS(remaining=lambda now: 10)),
            patch.object(transport, "_send", exchange),
        ):
            controller._save_checkpoint_impl(2)
            controller._save_checkpoint_impl(2)
        assert [c[0] for c in calls] == [3, 4]
        assert calls[0][1] != calls[1][1]
        assert calls[0][2]["state_sha256"] == calls[1][2]["state_sha256"]
        first = (calls[0][1] / "best_program_info.json").read_bytes()
        final = (calls[1][1] / "best_program_info.json").read_bytes()
        assert first != final
        assert json.loads(first)["saved_at"] != json.loads(final)["saved_at"]
        assert snapshot(database) == validate_directory(calls[-1][1])
        print(
            json.dumps(
                {
                    "installed_native_saves": 2,
                    "ordinals": [3, 4],
                    "same_cursor_distinct_retained_bytes": True,
                    "scope": "installed native saver; synthetic host ACK, no DB/Docker acceptance",
                }
            )
        )


if __name__ == "__main__":
    main()

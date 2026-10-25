"""Probe actual native prompt selection callbacks; no host persistence claim."""

import argparse
import hashlib
import importlib.util
import json
import random
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from unittest.mock import patch

from openevolve.config import PromptConfig
from openevolve.prompt import sampler as native

from packages.openevolve_adapter import observer
from packages.openevolve_adapter.observer import SearchControlAbort

RECORDS = ContextVar("prompt_smoke_records", default=None)


def program(index):
    return {
        "id": f"program-{index}",
        "code": f"source_identity_{index} = {index}",
        "metrics": {"combined_score": float(-index)},
        "metadata": {},
    }


def main(baseline_sampler: Path | None = None):
    completed = []
    selected = []
    real_sample = random.sample

    @contextmanager
    def scope():
        assert RECORDS.get() is None
        token = RECORDS.set({"references": [], "current": None, "artifacts": None})
        try:
            yield
        finally:
            RECORDS.reset(token)

    def reference(role, value):
        RECORDS.get()["references"].append((role, value["id"], value["code"]))
        value["code"] = "observer cannot mutate the native rendering input"

    def current(source):
        RECORDS.get()["current"] = source

    def artifacts(section):
        RECORDS.get()["artifacts"] = section

    def complete(system, user):
        completed.append((RECORDS.get(), system, user))

    def sample(values, count):
        result = real_sample(values, count)
        selected.append([x["id"] for x in result])
        return result

    config = PromptConfig()
    config.num_top_programs = config.num_diverse_programs = 2
    config.use_template_stochasticity = False
    config.programs_as_changes_description = False
    config.include_artifacts = True
    config.max_artifact_bytes = 10
    sampler = native.PromptSampler(config)
    all_programs = [program(index) for index in range(1, 7)]
    inspirations = all_programs + [program(7)]
    state = random.getstate()
    baseline_result = None
    if baseline_sampler is not None:
        manifest = json.loads(
            (Path(__file__).resolve().parents[1] / "upstream-source.json").read_text()
        )
        expected = next(
            item["sha256"]
            for item in manifest["files"]
            if item["path"] == "openevolve/prompt/sampler.py"
        )
        assert hashlib.sha256(baseline_sampler.read_bytes()).hexdigest() == expected
        spec = importlib.util.spec_from_file_location(
            "verified_baseline_sampler", baseline_sampler
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        random.seed(370)
        baseline_result = module.PromptSampler(config).build_prompt(
            current_program="current = 0",
            previous_programs=all_programs[:4],
            top_programs=all_programs,
            inspirations=inspirations,
            program_artifacts={"feedback": "abcdefghijklmnopqrst"},
        )
    random.seed(370)
    try:
        with (
            patch.object(observer, "prompt_scope", scope, create=True),
            patch.object(observer, "prompt_reference", reference, create=True),
            patch.object(observer, "prompt_current_source", current, create=True),
            patch.object(observer, "prompt_artifacts", artifacts, create=True),
            patch.object(observer, "prompt_complete", complete, create=True),
            patch.object(native.random, "sample", sample),
        ):
            result = sampler.build_prompt(
                current_program="current = 0",
                previous_programs=all_programs[:4],
                top_programs=all_programs,
                inspirations=inspirations,
                program_artifacts={"feedback": "abcdefghijklmnopqrst"},
            )
            if baseline_result is not None:
                assert result == baseline_result, (
                    "Observer patch changed native rendered prompt"
                )
            assert len(selected) == 1, "Recording reran stochastic selection"
            records, system, user = completed[0]
            assert result == {"system": system, "user": user}
            expected = [("previous_summary", f"program-{i}") for i in (4, 3, 2)]
            expected += [("top", "program-1"), ("top", "program-2")]
            expected += [("diverse", value) for value in selected[0]]
            shown = {"program-1", "program-2", *selected[0]}
            expected += [
                ("inspiration", p["id"]) for p in inspirations if p["id"] not in shown
            ]
            assert [
                (role, identity) for role, identity, _code in records["references"]
            ] == expected
            for role, _identity, code in records["references"]:
                if role != "previous_summary":
                    assert code in user
            assert "observer cannot mutate" not in user
            assert records["current"] == "current = 0"
            assert records["artifacts"] in user
            assert "abcdefghij\n... (truncated)" in records["artifacts"]
            assert "klmnopqrst" not in records["artifacts"]
            sampler.build_prompt(current_program="second = 0")
            assert completed[1][0]["references"] == []
            assert completed[1][0]["current"] == "second = 0"
            assert RECORDS.get() is None

            def fail_reference(*_args):
                raise RuntimeError("host acknowledgement unavailable")

            with patch.object(observer, "prompt_reference", fail_reference):
                try:
                    sampler.build_prompt(
                        current_program="third = 0", top_programs=all_programs
                    )
                except SearchControlAbort as error:
                    assert error.phase == "worker_observation"
                else:
                    raise AssertionError("Prompt callback failure did not fail-stop")
            assert len(completed) == 2 and RECORDS.get() is None
    finally:
        random.setstate(state)
    print(
        "Installed native prompt order/dedup/truncation/scope probes passed; recorder-only, no durable-host claim."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-sampler", type=Path)
    main(parser.parse_args().baseline_sampler)

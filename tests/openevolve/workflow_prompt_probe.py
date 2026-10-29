"""Offline actual pinned SDK rendering; recording observer is not host authority."""

import hashlib
import importlib.util
import json
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def run(raw):
    import openevolve.prompt.sampler as installed

    from packages.factors.contracts import canonical_bytes
    from packages.openevolve_structured.provider_prompt import prompt_config
    from packages.openevolve_structured.workflow_bootstrap import (
        WorkflowStructuredBootstrap,
    )
    from packages.openevolve_structured.workflow_prompt import (
        validate_workflow_rendered,
        workflow_prompt_scope,
    )
    from runtimes.openevolve_structured.apply import rewrite

    bootstrap = WorkflowStructuredBootstrap.model_validate_json(raw)
    from packages.openevolve_structured.candidate import CandidateDocument

    parent = canonical_bytes(
        CandidateDocument(program=bootstrap.spec.domain.seed_program)
    )
    path = Path(importlib.util.find_spec("openevolve.prompt.sampler").origin)
    source = path.read_bytes()
    entry = json.loads(
        (ROOT / "runtimes/openevolve_structured/hook-manifest.json").read_bytes()
    )["files"]["openevolve/prompt/sampler.py"]
    rewritten = rewrite("openevolve/prompt/sampler.py", source)
    assert hashlib.sha256(source).hexdigest() == entry["before_sha256"]
    assert hashlib.sha256(rewritten).hexdigest() == entry["after_sha256"]
    private = types.ModuleType("openevolve.prompt.sampler")
    private.__file__ = str(path)
    private.__package__ = "openevolve.prompt"
    exec(compile(rewritten, str(path), "exec"), private.__dict__)  # noqa: S102

    class Capture:
        @contextmanager
        def control_guard(self, phase):
            assert phase == "worker_observation"
            yield

        @contextmanager
        def prompt_scope(self):
            yield

        def prompt_reference(self, *args):
            raise AssertionError("No fabricated evaluated references")

        def prompt_current_source(self, source):
            assert source == parent.decode()

        def prompt_artifacts(self, section):
            assert section == ""

        def prompt_complete(self, system, user):
            validate_workflow_rendered(bootstrap.workflow_context, system, user)
            self.seen.append({"system": system, "user": user})

    capture = Capture()
    capture.seen = []
    private.mineagent_observer = capture
    with patch.object(installed, "PromptSampler", private.PromptSampler):
        original = private.PromptSampler._build_prompt_impl
        sampler = private.PromptSampler(prompt_config())
        try:
            with workflow_prompt_scope(bootstrap):
                # Repeated rendering, repair-style extra fields and continuation
                # all traverse the same native build_prompt before observation.
                for kwargs in (
                    {},
                    {"repair_context": "retry"},
                    {"continuation": "continue"},
                ):
                    rendered = sampler.build_prompt(
                        current_program=parent.decode(),
                        language="json",
                        diff_based_evolution=False,
                        **kwargs,
                    )
                    assert rendered == capture.seen[-1]
                    from uuid import uuid4

                    from packages.factors.contracts import digest
                    from packages.openevolve_structured.contracts import (
                        SearchOperationContext,
                    )
                    from packages.openevolve_structured.provider_contracts import (
                        RenderedPrompt,
                    )
                    from packages.openevolve_structured.provider_response import (
                        build_request,
                    )

                    observed = RenderedPrompt(
                        **rendered,
                        references=(),
                        current_candidate_sha256=hashlib.sha256(parent).hexdigest(),
                        artifact_section_sha256=hashlib.sha256(b"").hexdigest(),
                    )
                    operation = SearchOperationContext(
                        owner_id=bootstrap.spec.owner_id,
                        run_id=bootstrap.spec.run_id,
                        epoch_id=bootstrap.epoch_id,
                        iteration_intent_id=uuid4(),
                        native_iteration=1,
                        parent_candidate_id=uuid4(),
                        parent_candidate_sha256=hashlib.sha256(parent).hexdigest(),
                        execution_binding_sha256=bootstrap.spec.execution_binding_sha256,
                        selection_snapshot_sha256="a" * 64,
                        rendered_context_sha256=digest(observed),
                    )
                    request = build_request(
                        binding=bootstrap.model_binding,
                        context=operation,
                        domain=bootstrap.spec.domain,
                        parent_candidate=parent,
                        rendered=observed,
                    )
                    assert tuple(m.content for m in request.messages) == (
                        rendered["system"],
                        rendered["user"],
                    )
                    assert (
                        rendered["user"].count(
                            canonical_bytes(bootstrap.workflow_context).decode()
                        )
                        == 1
                    )
                raise RuntimeError("test finally")
        except RuntimeError as exc:
            assert str(exc) == "test finally"
        assert private.PromptSampler._build_prompt_impl is original
        assert len(capture.seen) == 3
        # All retry/repair-style renders contain the same full selected inputs;
        # no hook invocation or provider call can be introduced by this wrapper.
        with workflow_prompt_scope(bootstrap):
            try:
                with workflow_prompt_scope(bootstrap):
                    raise AssertionError("nested accepted")
            except ValueError:
                pass
        assert private.PromptSampler._build_prompt_impl is original
        with patch.object(private.PromptSampler, "_build_prompt_impl", lambda self: {}):
            try:
                with workflow_prompt_scope(bootstrap):
                    raise AssertionError("unknown ABI accepted")
            except ValueError:
                pass
        from packages.openevolve_structured.workflow_prompt import BEGIN

        for bad_base in ("duplicate" + BEGIN,):
            try:
                with workflow_prompt_scope(bootstrap):
                    sampler.build_prompt(
                        current_program=bad_base,
                        language="json",
                        diff_based_evolution=False,
                    )
                raise AssertionError("duplicate delimiter accepted")
            except ValueError:
                pass
        assert private.PromptSampler._build_prompt_impl is original
        with patch.object(
            sampler.config, "system_message", "unexpected trusted instructions"
        ):
            try:
                with workflow_prompt_scope(bootstrap):
                    sampler.build_prompt(
                        current_program=parent.decode(),
                        language="json",
                        diff_based_evolution=False,
                    )
                raise AssertionError("system drift accepted")
            except ValueError:
                pass
        assert private.PromptSampler._build_prompt_impl is original
    return {
        "renderings": 3,
        "observer_final_bytes": True,
        "restored": True,
        "provider_calls": 0,
    }


if __name__ == "__main__":
    print(json.dumps(run(sys.stdin.buffer.read()), sort_keys=True))

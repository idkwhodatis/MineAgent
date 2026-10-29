"""Offline real spawn + installed sampler; recording callback is not a host grant.

Run with -I and an isolated image COPY-set/installed SDK staging directory.
There are no provider calls, Docker execution, or transport-retry claims here.
"""

import importlib
import importlib.abc
import json
import multiprocessing
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path


def isolate(root):
    root = Path(root).resolve()
    sys.path.insert(0, str(root))
    for name in ("packages", "runtimes"):
        module = importlib.import_module(name)
        module.__path__ = [str(root / name)]

    class Closed(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if any(
                fullname == prefix or fullname.startswith(prefix + ".")
                for prefix in (
                    "backend",
                    "pandas",
                    "packages.marketdata",
                    "packages.rdagent_adapter",
                    "packages.openevolve_structured.workflow_run",
                    "packages.openevolve_structured.workflow_host_binding",
                    "packages.openevolve_structured.provider_run",
                )
            ):
                raise AssertionError("Host module in controller: " + fullname)

    sys.meta_path.insert(0, Closed())
    return root


def render_worker(root, raw, parent_pid):
    root = isolate(root)
    assert os.getpid() != parent_pid
    assert multiprocessing.get_start_method() == "spawn"
    import hashlib
    import time
    from contextlib import ExitStack
    from unittest.mock import patch
    from uuid import uuid4

    from openevolve.prompt.sampler import PromptSampler

    from packages.factors.contracts import canonical_bytes, digest
    from packages.openevolve_structured import observer, observer_transport
    from packages.openevolve_structured.candidate import CandidateDocument
    from packages.openevolve_structured.contracts import SearchOperationContext
    from packages.openevolve_structured.provider_contracts import RenderedPrompt
    from packages.openevolve_structured.provider_prompt import prompt_config
    from packages.openevolve_structured.provider_response import build_request
    from packages.openevolve_structured.provider_runtime_config import (
        build_config,
        config_manifest,
    )
    from packages.openevolve_structured.workflow_bootstrap import (
        WorkflowStructuredBootstrap,
    )
    from packages.openevolve_structured.workflow_prompt import (
        validate_workflow_rendered,
    )
    from runtimes.openevolve_operational.fixed_input import read_fixed
    from runtimes.openevolve_structured.profile import profile_sha256

    initial = WorkflowStructuredBootstrap.model_validate_json(raw)
    seed = canonical_bytes(CandidateDocument(program=initial.spec.domain.seed_program))
    # Fixture mount mapping only. The actual fixed reader/parser/hash/deadline
    # checks run unchanged on these ordinary local files.
    from datetime import UTC, datetime, timedelta

    now = datetime.fromtimestamp(time.time(), UTC)
    data = initial.model_dump(mode="python")
    data.update(
        issued_at=now,
        not_after=now + timedelta(seconds=120),
        physical_profile_sha256=profile_sha256(),
        config_sha256=digest(
            config_manifest(build_config(initial.spec, seed, initial.model_binding))
        ),
    )
    initial = WorkflowStructuredBootstrap.model_validate(data)
    import tempfile

    mount = Path(tempfile.mkdtemp(prefix="probe-input-", dir=root))
    (mount / "bootstrap.json").write_bytes(canonical_bytes(initial))
    (mount / "seed.json").write_bytes(seed)

    def mapped_read(path, limit):
        if str(path) == "/bootstrap/seed.json":
            path = mount / "seed.json"
        return read_fixed(path, limit)

    captured = []

    def completed(system, user):
        if type(initial) is WorkflowStructuredBootstrap:
            validate_workflow_rendered(initial.workflow_context, system, user)
        else:
            from packages.openevolve_structured.provider_prompt import SYSTEM_MESSAGE

            assert system == SYSTEM_MESSAGE
            assert "MINEAGENT_WORKFLOW_EVIDENCE_V1" not in user
        current = observer._PROMPT.get()
        rendered = RenderedPrompt.model_validate_json(
            canonical_bytes({"system": system, "user": user, **current})
        )
        operation = SearchOperationContext(
            owner_id=initial.spec.owner_id,
            run_id=initial.spec.run_id,
            epoch_id=initial.epoch_id,
            iteration_intent_id=uuid4(),
            native_iteration=1,
            parent_candidate_id=uuid4(),
            parent_candidate_sha256=hashlib.sha256(seed).hexdigest(),
            execution_binding_sha256=initial.spec.execution_binding_sha256,
            selection_snapshot_sha256="a" * 64,
            rendered_context_sha256=digest(rendered),
        )
        request = build_request(
            binding=initial.model_binding,
            context=operation,
            domain=initial.spec.domain,
            parent_candidate=seed,
            rendered=rendered,
        )
        assert tuple(message.content for message in request.messages) == (system, user)
        captured.append({"system": system, "user": user})

    original = PromptSampler._build_prompt_impl
    sampler = PromptSampler(prompt_config())
    worker_token = observer._WORKER.set({"fixture_worker": True})
    try:
        with ExitStack() as patches:
            patches.enter_context(
                patch.object(observer_transport, "BOOTSTRAP", mount / "bootstrap.json")
            )
            patches.enter_context(
                patch.object(observer_transport, "read_fixed", mapped_read)
            )
            patches.enter_context(patch.object(observer, "prompt_complete", completed))
            for kwargs in (
                {},
                {"repair_context": "retry"},
                {"continuation": "continue"},
            ):
                result = sampler.build_prompt(
                    current_program=seed.decode(),
                    language="json",
                    diff_based_evolution=False,
                    **kwargs,
                )
                assert result == captured[-1]
                assert PromptSampler._build_prompt_impl is original
                assert observer._PROMPT.get() is None
            with patch.object(
                sampler.config, "system_message", "untrusted replacement"
            ):
                try:
                    sampler.build_prompt(
                        current_program=seed.decode(),
                        language="json",
                        diff_based_evolution=False,
                    )
                except observer.SearchControlAbort:
                    pass
                else:
                    raise AssertionError("System drift accepted")
            assert PromptSampler._build_prompt_impl is original
            assert observer._PROMPT.get() is None
            # The same spawned worker must retain the old v2 prompt bytes.
            from packages.openevolve_structured.provider_bootstrap import (
                ProviderStructuredBootstrap,
                runtime_profile_sha256,
            )

            legacy = initial.model_dump(mode="json")
            legacy.pop("workflow_context")
            legacy.pop("workflow_context_sha256")
            legacy.update(
                schema_version="mineagent.oe-structured-bootstrap.v2",
                runtime_profile_sha256=runtime_profile_sha256(),
            )
            initial = ProviderStructuredBootstrap.model_validate_json(
                canonical_bytes(legacy)
            )
            (mount / "bootstrap.json").write_bytes(canonical_bytes(initial))
            token = observer._PROMPT.set(
                {
                    "references": [],
                    "current_candidate_sha256": None,
                    "artifact_section_sha256": None,
                }
            )
            try:
                baseline = original(
                    sampler,
                    current_program=seed.decode(),
                    language="json",
                    diff_based_evolution=False,
                )
            finally:
                observer._PROMPT.reset(token)
            legacy_result = sampler.build_prompt(
                current_program=seed.decode(),
                language="json",
                diff_based_evolution=False,
            )
            assert legacy_result == baseline == captured[-1]
            assert PromptSampler._build_prompt_impl is original
            assert observer._PROMPT.get() is None
    finally:
        observer._WORKER.reset(worker_token)
    for name, module in tuple(sys.modules.items()):
        if name.startswith(("packages.", "runtimes.", "openevolve.")) and getattr(
            module, "__file__", None
        ):
            assert Path(module.__file__).resolve().is_relative_to(root), name
    return {
        "spawn": True,
        "renderings": len(captured),
        "legacy_v2_bytes_unchanged": True,
        "observer_final_bytes": True,
        "request_messages_equal": True,
        "restored": True,
        "provider_calls": 0,
        "checkout_fallback": False,
    }


if __name__ == "__main__":
    if not sys.flags.isolated or len(sys.argv) != 3:
        raise SystemExit("Use -I with isolated stage and bootstrap JSON")
    root = isolate(sys.argv[1])
    raw = Path(sys.argv[2]).read_bytes()
    with ProcessPoolExecutor(
        max_workers=1, mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        result = pool.submit(render_worker, str(root), raw, os.getpid()).result(
            timeout=30
        )
    print(json.dumps(result, sort_keys=True))

"""Offline installed-sampler -> real SDK probe, deliberately NOT a PG/worker grant.

The only in-memory substitution is a recording observer on a privately compiled,
exact-hash-verified upstream sampler. No installed files/global024 hooks change.
No seed score, completed engine result or persisted authority is fabricated.
"""

import hashlib
import importlib.util
import json
import sys
import time
import types
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

# Test harness only; not the production isolated controller entrypoint.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def run(payload):
    from packages.factors.contracts import digest
    from packages.openevolve_structured.candidate import (
        TypedFrozenCandidateDomain,
        project_candidate,
    )
    from packages.openevolve_structured.contracts import SearchOperationContext
    from packages.openevolve_structured.provider_contracts import (
        OEChatBinding,
        RenderedPrompt,
    )
    from packages.openevolve_structured.provider_prompt import prompt_config
    from packages.openevolve_structured.provider_response import (
        build_request,
        parse_response,
    )
    from packages.openevolve_structured.provider_transport import complete_chat
    from runtimes.openevolve_structured.apply import rewrite

    binding = OEChatBinding.model_validate_json(json.dumps(payload["binding"]))
    domain = TypedFrozenCandidateDomain.model_validate_json(
        json.dumps(payload["domain"])
    )
    parent = payload["parent"].encode()
    project_candidate(parent, domain)
    path = Path(importlib.util.find_spec("openevolve.prompt.sampler").origin)
    original = path.read_bytes()
    manifest = json.loads(
        (ROOT / "runtimes/openevolve_structured/hook-manifest.json").read_bytes()
    )
    entry = manifest["files"]["openevolve/prompt/sampler.py"]
    rewritten = rewrite("openevolve/prompt/sampler.py", original)
    if (
        hashlib.sha256(original).hexdigest() != entry["before_sha256"]
        or hashlib.sha256(rewritten).hexdigest() != entry["after_sha256"]
    ):
        raise ValueError("Installed sampler differs from reviewed hook inventory")
    private = types.ModuleType("mineagent_private_structured_prompt_probe")
    private.__file__ = str(path)
    private.__package__ = "openevolve.prompt"
    exec(compile(rewritten, str(path), "exec"), private.__dict__)  # noqa: S102 - exact reviewed upstream hash, private test module

    class Capture:
        @contextmanager
        def control_guard(self, phase):
            if phase != "worker_observation":
                raise ValueError("Unexpected sampler phase")
            yield

        @contextmanager
        def prompt_scope(self):
            self.references, self.current, self.artifact = [], None, None
            yield

        def prompt_reference(self, *_):
            raise ValueError(
                "Unscored transport probe does not invent evaluated references"
            )

        def prompt_current_source(self, source):
            if source.encode() != parent:
                raise ValueError("Actual upstream parent source changed")
            self.current = hashlib.sha256(parent).hexdigest()

        def prompt_artifacts(self, section):
            if section != "":
                raise ValueError("No fabricated evaluated artifacts in transport probe")
            self.artifact = hashlib.sha256(section.encode()).hexdigest()

        def prompt_complete(self, system, user):
            self.projection = RenderedPrompt(
                system=system,
                user=user,
                references=(),
                current_candidate_sha256=self.current,
                artifact_section_sha256=self.artifact,
            )

    capture = Capture()
    private.mineagent_observer = (
        capture  # Only this private test module, not installed globals.
    )
    sampler = private.PromptSampler(prompt_config())
    rendered = sampler.build_prompt(
        current_program=parent.decode(),
        parent_program="",
        program_metrics={},
        previous_programs=[],
        top_programs=[],
        inspirations=[],
        language="json",
        evolution_round=1,
        diff_based_evolution=False,
        program_artifacts=None,
    )
    if rendered != {
        "system": capture.projection.system,
        "user": capture.projection.user,
    }:
        raise ValueError("Observed prompt differs from actual upstream return")
    context = SearchOperationContext(
        owner_id=UUID(payload["owner_id"]),
        run_id=UUID(payload["run_id"]),
        epoch_id=UUID(payload["epoch_id"]),
        iteration_intent_id=UUID(payload["intent_id"]),
        native_iteration=1,
        parent_candidate_id=UUID(payload["parent_id"]),
        parent_candidate_sha256=hashlib.sha256(parent).hexdigest(),
        execution_binding_sha256="a" * 64,
        selection_snapshot_sha256="b" * 64,
        rendered_context_sha256=digest(capture.projection),
    )
    request = build_request(
        binding=binding,
        context=context,
        domain=domain,
        parent_candidate=parent,
        rendered=capture.projection,
    )
    phases, identities = [], []
    completed = complete_chat(
        request,
        binding=binding,
        base_url=payload["base_url"],
        secret=payload["secret"],
        deadline_monotonic=time.monotonic() + 8,
        boundary=phases.append,
        identity_boundary=lambda phase, identity: identities.append((phase, identity)),
        python_executable=payload["sdk_python"],
    )
    # A real broker must persist completed.response before this parse. This probe
    # records no DB completion and issues no process/engine/candidate capability.
    proposed = parse_response(
        request=request, response=completed.response, binding=binding, domain=domain
    )
    if phases != ["admitted", "stopped"] or [x[0] for x in identities] != [
        "started",
        "stopped",
    ]:
        raise ValueError("Actual SDK identity/stop phases missing")
    return {
        "qualification": "installed_sampler_sdk_localhost_non_pg_no_engine_authority",
        "sampler_sha256": entry["before_sha256"],
        "request_schema": request.schema_version,
        "rendered_context_sha256": request.rendered_context_sha256,
        "request_sha256": request.sha256,
        "candidate_sha256": proposed.candidate_sha256,
        "program_sha256": proposed.program_sha256,
        "candidate": proposed.candidate_bytes.decode(),
        "parent_candidate_sha256": proposed.parent_candidate_sha256,
        "input_tokens": completed.response.input_tokens,
        "output_tokens": completed.response.output_tokens,
        "total_tokens": completed.response.total_tokens,
        "usage_source": completed.response.usage_source,
        "billing_status": completed.response.billing_status,
        "sdk_pid": completed.physical.stop_evidence.identity.pid,
        "sdk_start_ticks": completed.physical.stop_evidence.identity.start_ticks,
        "physical_group_stopped": completed.physical.contained,
        "engine_units": 0,
        "supplied_seed_metrics": False,
    }


if __name__ == "__main__":
    try:
        raw = sys.stdin.buffer.read(1048577)
        if len(raw) > 1048576:
            raise ValueError("Probe input bound")
        result = run(json.loads(raw))
    except Exception:  # noqa: BLE001 - never print private endpoint/credential diagnostics
        sys.stdout.write('{"error":"structured_provider_probe_failed"}')
        raise SystemExit(1) from None
    sys.stdout.write(
        json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
    )

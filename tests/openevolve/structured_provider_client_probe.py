"""Installed upstream Config/client consistency probe; no DB/process authority."""

import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def run(payload):
    from packages.factors.contracts import canonical_bytes, digest
    from packages.openevolve_structured import provider_broker_client as client
    from packages.openevolve_structured.contracts import SearchOperationContext
    from packages.openevolve_structured.observer import CandidateSourceRejected
    from packages.openevolve_structured.provider_bootstrap import (
        ProviderStructuredBootstrap,
    )
    from packages.openevolve_structured.provider_contracts import (
        OEChatRequest,
        OEChatResponse,
    )
    from packages.openevolve_structured.provider_runtime_config import (
        build_config,
        config_manifest,
    )
    from packages.openevolve_structured.runtime_config import (
        build_config as fixture_config,
    )
    from packages.openevolve_structured.runtime_config import (
        config_manifest as fixture_manifest,
    )
    from packages.research_runtime.control import BrokerAbort

    initial = ProviderStructuredBootstrap.model_validate_json(
        json.dumps(payload["bootstrap"])
    )
    request = OEChatRequest.model_validate_json(json.dumps(payload["request"]))
    context = SearchOperationContext.model_validate_json(json.dumps(payload["context"]))
    parent = payload["parent"].encode()
    config = build_config(initial.spec, parent, initial.model_binding)
    manifest = config_manifest(config)
    assert manifest["language"] == "json" and manifest["diff_based_evolution"] is False
    assert manifest["llm"]["models"][0]["name"] == initial.model_binding.model
    assert (
        manifest["llm"]["models"][0]["max_tokens"]
        == initial.model_binding.max_output_tokens
    )
    assert (
        fixture_manifest(fixture_config(initial.spec, parent))["llm"]["models"][0][
            "name"
        ]
        == "openevolve-structured-fixture.v1"
    )
    client._bootstrap = lambda: initial
    calls = []
    mode = payload["mode"]
    if mode == "artifact_storage":
        from openevolve.database import Program, ProgramDatabase

        from packages.openevolve_structured.checkpoint import snapshot

        # This is an upstream artifact storage mechanism probe, not a measured
        # financial result. A fixed dummy metric is explicitly fixture data.
        database = ProgramDatabase(config.database)
        program = Program(
            id=str(uuid4()),
            code=parent.decode(),
            language="json",
            metrics={"combined_score": 1.0},
            iteration_found=0,
        )
        child_dict = program.to_dict()
        assert child_dict["artifacts_json"] is None
        assert database.add(program, iteration=0, target_island=0) == program.id
        artifact = {"structured_feedback": "artifact-storage-mechanism-only"}
        database.store_artifacts(program.id, artifact)
        assert json.loads(database.get(program.id).artifacts_json) == artifact
        assert database.get_artifacts(program.id) == artifact
        saved = snapshot(database, initial.spec.domain)
        assert json.loads(saved["programs"][program.id]["artifacts_json"]) == artifact
        return {
            "before_none": True,
            "retained_exact": True,
            "calls": 0,
            "engine_units": 0,
        }

    def exchange(raw):
        wire = json.loads(raw)
        assert canonical_bytes(wire) == raw
        assert (
            wire["schema_version"] == "mineagent.oe-structured-provider-broker-call.v1"
        )
        assert wire["request"] == request.model_dump(mode="json")
        calls.append(wire["request_sha256"])
        child = json.loads(parent)
        child["program"]["threshold"] = 0.07
        response = OEChatResponse(
            request_sha256=request.sha256,
            content=canonical_bytes(child).decode(),
            finish_reason="length" if mode == "truncated" else "stop",
            input_tokens=None if mode == "unknown_usage" else 7,
            output_tokens=None if mode == "unknown_usage" else 11,
            total_tokens=None if mode == "unknown_usage" else 18,
            usage_source="synthetic_provider"
            if initial.model_binding.synthetic
            else "provider",
        )
        reply = {
            "schema_version": "mineagent.oe-structured-provider-broker-result.v1",
            "request_sha256": request.sha256,
            "context_sha256": digest(context),
            "receipt_id": str(uuid4()),
            "response": response.model_dump(mode="json"),
        }
        if mode == "legacy_reply":
            reply["schema_version"] = "mineagent.oe-structured-broker-result.v1"
        if mode == "wrong_context":
            reply["context_sha256"] = "0" * 64
        return canonical_bytes(reply)

    client._exchange = exchange
    model = client.init_client(config.llm.models[0])
    grant = client.ProviderOperationGrant(
        admission_id=initial.admission_id,
        context=context,
        request=request,
        binding=initial.model_binding,
        domain=initial.spec.domain,
        process_ticket="a" * 64,
        context_ticket="b" * 64,
    )
    if mode == "wrong_admission":
        grant = client.ProviderOperationGrant(
            admission_id=UUID(int=1),
            context=context,
            request=request,
            binding=initial.model_binding,
            domain=initial.spec.domain,
            process_ticket="a" * 64,
            context_ticket="b" * 64,
        )
    system = request.messages[0].content
    if mode == "changed_prompt":
        system += "changed"
    if mode in {"concurrent_tasks", "concurrent_threads"}:

        async def invoke():
            try:
                await model.generate_with_context(
                    system, [request.messages[1].model_dump(mode="json")]
                )
                return "success"
            except BrokerAbort:
                return "rejected"

        async def tasks():
            return await asyncio.gather(invoke(), invoke())

        with client.bind_operation(grant):
            if mode == "concurrent_tasks":
                outcomes = asyncio.run(tasks())
            else:
                from concurrent.futures import ThreadPoolExecutor
                from contextvars import copy_context
                from threading import Barrier

                barrier = Barrier(2)

                def thread_call():
                    barrier.wait(timeout=5)
                    return asyncio.run(invoke())

                contexts = [copy_context(), copy_context()]
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(ctx.run, thread_call) for ctx in contexts]
                    outcomes = [future.result(timeout=10) for future in futures]
        assert sorted(outcomes) == ["rejected", "success"]
        assert len(calls) == 1
        return {"rejected": "BrokerAbort", "calls": 1, "engine_units": 0}
    try:
        with client.bind_operation(grant):
            result = asyncio.run(
                model.generate_with_context(
                    system, [request.messages[1].model_dump(mode="json")]
                )
            )
            if mode == "replay":
                asyncio.run(
                    model.generate_with_context(
                        system, [request.messages[1].model_dump(mode="json")]
                    )
                )
    except (BrokerAbort, CandidateSourceRejected) as error:
        if mode in {"valid", "unknown_usage"}:
            raise
        assert len(calls) == (0 if mode in {"changed_prompt", "wrong_admission"} else 1)
        return {
            "rejected": type(error).__name__,
            "calls": len(calls),
            "engine_units": 0,
        }
    assert mode in {"valid", "unknown_usage"}
    assert json.loads(result)["program"]["threshold"] == 0.07
    assert model.last_usage == (
        None
        if mode == "unknown_usage"
        else {"prompt_tokens": 7, "completion_tokens": 11, "total_tokens": 18}
    )
    return {
        "generated": True,
        "calls": len(calls),
        "unknown_usage": model.last_usage is None,
        "engine_units": 0,
    }


if __name__ == "__main__":
    print(json.dumps(run(json.loads(sys.stdin.buffer.read(1048577)))))

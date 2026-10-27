"""Real typed staging/input documents with explicitly doubled DB/authority."""

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

import pytest

from backend.app import openevolve_native_process as m
from backend.app.openevolve_models import StrategySearchSpec
from backend.app.openevolve_operational_models import (
    SearchControllerLaunch,
    SearchEvaluationInput,
)
from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.contracts import SeedPreparation, SourceBinding
from packages.openevolve_adapter.runtime_profile import PROFILE as RP
from packages.research_runtime.broker_contracts import digest
from packages.research_runtime.process_contracts import ProcessError
from packages.research_runtime.process_contracts import digest as process_digest
from packages.research_runtime.process_declarations_v2 import (
    NoIPC,
    ProcessDeclarationV2,
)
from runtimes.openevolve_evaluator.journal import NativeInputBinding, NativeIntent
from runtimes.openevolve_operational.bootstrap import OperationalBootstrap
from runtimes.openevolve_operational.profile import profile_sha256
from runtimes.openevolve_operational.runtime_binding import NativeRuntimeBinding
from tests.openevolve.test_contracts import complete_spec
from tests.openevolve.test_search_staging import fixture
from workers.openevolve.input_inventory import inventory_staged_input
from workers.openevolve.staging import stage_search_input


@pytest.fixture
def case(monkeypatch, tmp_path):
    args = fixture(tmp_path)
    native = args["inputs"]
    args["inputs"] = native
    args["plan"] = args["plan"].model_copy(
        update={"engine_config_sha256": digest(native.execution_projection())}
    )
    stage_search_input(**args)
    eid = uuid4()
    manifest = inventory_staged_input(
        args["target"],
        evaluation_id=eid,
        plan=args["plan"],
        source_sha256=args["source_sha256"],
        window=args["window"],
    )
    raw = canonical(manifest.model_dump(mode="json"))
    spec = complete_spec()
    source = SourceBinding(
        strategy_version_id=uuid4(),
        source_sha256=args["source_sha256"],
        manifest_sha256="f" * 64,
    )
    spec = type(spec).model_validate(
        spec.model_copy(
            update={
                "preparation": SeedPreparation(
                    kind="none", original=source, prepared=source
                ),
                "execution": args["plan"],
                "objective": spec.objective.model_copy(
                    update={"windows": args["plan"].windows}
                ),
            }
        )
    )
    fence = NS(
        owner_id=spec.owner_id, run_id=spec.run_id, attempt_id=uuid4(), generation=1
    )
    scope = fence.__dict__
    epoch = uuid4()
    now = datetime.now(UTC)
    bootstrap = OperationalBootstrap(
        admission_id=uuid4(),
        epoch_id=epoch,
        spec=spec,
        native_inputs=native,
        native_runtime=NativeRuntimeBinding(
            native_parent_image_id="sha256:" + "e" * 64,
            protected_image_id=spec.execution.evaluator_image,
            dependency_lock_sha256="d" * 64,
            wrapper_inventory_sha256="c" * 64,
        ),
        issued_at=now,
        not_after=now + timedelta(seconds=600),
        physical_profile_sha256=profile_sha256(),
        runtime_profile_sha256=RP.sha256,
        config_sha256=spec.sha256,
        adapter_patch_sha256=spec.adapter_patch_sha256,
    )
    text = canonical(bootstrap.model_dump(mode="json")).decode()
    launch = NS(
        **scope,
        epoch_id=epoch,
        bootstrap=bootstrap.model_dump(mode="json"),
        bootstrap_canonical=text,
        bootstrap_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )
    bound = NativeInputBinding(
        evaluation_id=eid,
        manifest_object_id=uuid4(),
        manifest_sha256=manifest.sha256,
        manifest_size_bytes=len(raw),
        native_artifacts_sha256=manifest.native_artifacts_sha256,
        execution_plan_sha256=digest(spec.execution),
        source_sha256=manifest.source_sha256,
    )
    admission = ProcessDeclarationV2(
        schema_version="mineagent.process-admission.v2",
        id=uuid4(),
        **scope,
        role="native_evaluator",
        logical_operation_id=eid,
        launch_ordinal=1,
        profile=m.PROFILE,
        input_sha256=process_digest(bound.model_dump(mode="json")),
        reservation_id=uuid4(),
        image_id=spec.execution.evaluator_image,
        ipc=NoIPC(ipc_policy="none", ticket_sha256=None),
    )
    intent = NativeIntent(
        id=admission.id,
        admission=admission,
        inputs=bound,
        issued_at=now,
        not_after=now + timedelta(seconds=60),
    )
    evaluation = NS(
        id=eid,
        **scope,
        epoch_id=epoch,
        window_id=args["window"].id,
        process_admission_id=admission.id,
        reservation_id=admission.reservation_id,
        physical_ordinal=1,
        source_sha256=manifest.source_sha256,
        execution_sha256=digest(spec.execution),
        input_manifest_sha256=manifest.native_artifacts_sha256,
    )
    binding = NS(
        **scope,
        evaluation_id=eid,
        epoch_id=epoch,
        manifest_object_id=bound.manifest_object_id,
        manifest_kind="search_evaluation_input",
        manifest_sha256=manifest.sha256,
        manifest_size_bytes=len(raw),
    )

    class Session:
        def scalar(self, q):
            return {SearchEvaluationInput: binding, SearchControllerLaunch: launch}[
                q.column_descriptions[0]["entity"]
            ]

        def get(self, cls, key):
            return (
                NS(owner_id=fence.owner_id, spec=spec.model_dump(mode="json"))
                if cls is StrategySearchSpec
                else None
            )

        def flush(self):
            pass

    session = Session()
    monkeypatch.setattr(m, "require_neutral_transaction", lambda *a, **k: None)
    monkeypatch.setattr(m, "_current_search", lambda *a: None)
    monkeypatch.setattr(m, "_owned_row", lambda session, cls, key, fence: evaluation)
    monkeypatch.setattr(m, "read_research_object", lambda *a: raw)
    return NS(
        intent=intent,
        admission=admission,
        session=session,
        fence=fence,
        evaluation=evaluation,
        binding=binding,
        launch=launch,
        manifest=manifest,
        raw=raw,
    )


def test_actual_input_and_float_bearing_bootstrap_bytes(case):
    evaluation, manifest, runtime = m.resolve_native_input(
        case.session, case.fence, case.intent, None
    )
    assert evaluation is case.evaluation and manifest == case.manifest
    assert runtime.protected_image_id == case.admission.image_id
    assert "market_profile" in case.launch.bootstrap_canonical
    assert (
        case.intent.inputs.manifest_sha256 != case.intent.inputs.native_artifacts_sha256
    )


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("evaluation", "input_manifest_sha256", "0" * 64),
        ("evaluation", "execution_sha256", "0" * 64),
        ("evaluation", "source_sha256", "0" * 64),
        ("evaluation", "process_admission_id", uuid4()),
        ("evaluation", "reservation_id", uuid4()),
        ("evaluation", "physical_ordinal", 2),
        ("binding", "manifest_size_bytes", 1),
        ("binding", "epoch_id", uuid4()),
        ("binding", "owner_id", uuid4()),
        ("launch", "bootstrap_sha256", "0" * 64),
        ("launch", "bootstrap_canonical", "{}"),
    ],
)
def test_binding_drift_rejected(case, target, field, value):
    setattr(getattr(case, target), field, value)
    with pytest.raises((ValueError, ProcessError)):
        m.resolve_native_input(case.session, case.fence, case.intent, None)


@pytest.mark.parametrize("claim", [True, False])
def test_fresh_claim_requires_held_exact_reservation(case, monkeypatch, claim):
    state = NS(status="launch_intended" if claim else "uncertain")
    monkeypatch.setattr(
        m, "persisted", lambda *_: (case.intent, state, {"launch_intent": object()})
    )
    held = Mock(
        return_value=NS(
            logical_key=f"oe-native:{case.admission.logical_operation_id}:1"
        )
    )
    monkeypatch.setattr(m, "require_reservation", held)
    if claim:
        assert (
            m.claim_native_launch(case.session, case.fence, case.admission.id, None)
            == case.intent
        )
    else:
        assert (
            m.check_native_claim(
                case.session,
                case.fence,
                case.admission.id,
                expected_intent=case.intent,
                settings=None,
            )
            == case.intent
        )
    assert held.call_count == 1 and held.call_args.kwargs["minimum"] == m.FLOOR
    held.return_value.logical_key = "other-evaluation"
    if claim:
        state.status = "launch_intended"
    with pytest.raises(ProcessError, match="reservation_identity"):
        if claim:
            m.claim_native_launch(case.session, case.fence, case.admission.id, None)
        else:
            m.check_native_claim(
                case.session,
                case.fence,
                case.admission.id,
                expected_intent=case.intent,
                settings=None,
            )

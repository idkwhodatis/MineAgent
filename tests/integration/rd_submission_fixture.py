"""Explicit metadata-only pins OR actual built pins; never interchangeable claims."""

import os
from datetime import date

from backend.app.engine_run_preflight import RECOMMENDED_BUDGET
from backend.app.engine_run_submission_schemas import RDSubmissionRequest
from backend.app.rdagent_server_profile import ROLE_LOCKS, RDServerRuntimeProfile
from packages.factors.contracts import DateWindow
from packages.qlib_adapter.objective import DirectObjectiveDeclaration
from packages.rdagent_adapter.contracts import (
    DependencyLock,
    FactorTaskSpec,
    RuntimePins,
)


def profile(*, actual_images):
    if type(actual_images) is not bool:
        raise ValueError("Explicit runtime proof choice")
    images = {
        role: os.environ.get("MINEAGENT_" + role.upper() + "_IMAGE", "")
        if actual_images
        else "sha256:" + str(i) * 64
        for i, role in enumerate(
            ("orchestrator", "candidate", "normalizer", "analysis"), 1
        )
    }
    parent = (
        os.environ.get("MINEAGENT_QLIB_INSTALLED_IMAGE", "")
        if actual_images
        else "sha256:" + "5" * 64
    )
    return RDServerRuntimeProfile(
        runtime=RuntimePins(
            **{role + "_image": value for role, value in images.items()},
            dependency_locks=tuple(
                DependencyLock(role=role, sha256=sha)
                for role, sha in sorted(ROLE_LOCKS.items())
            ),
        ),
        qlib_installed_parent_image_id=parent,
    )


def body(dataset, policy):
    window = DateWindow(start=date(2024, 3, 13), end=date(2024, 3, 14))
    return RDSubmissionRequest(
        dataset_version_id=dataset.id,
        window=window,
        input_fields=("close",),
        lookback_sessions=2,
        lag_sessions=0,
        task=FactorTaskSpec(
            name="configured-close-return",
            description="Compute two-prior-session unadjusted close return from retained declared daily source.",
            formulation="close[t]/close[t-2]-1",
            factor_kind="time_series",
            output_unit="return",
            interpretation="unsigned",
        ),
        objective=DirectObjectiveDeclaration(
            window=window,
            label_horizon_sessions=1,
            metric="rank_ic",
            direction="at_least",
            threshold=0.9,
            minimum_defined_days=1,
            minimum_pairs=3,
        ),
        budget=RECOMMENDED_BUDGET,
        max_iterations=1,
        source_policy=policy,
    )


def login(client, account):
    response = client.post(
        "/api/v1/login/access-token",
        data={"username": account.user.email, "password": account.password},
    )
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def submit(client, headers, request, *, key):
    checked = client.post(
        "/api/v1/engine-runs/preflight",
        headers=headers,
        json=request.model_dump(mode="json"),
    )
    assert checked.status_code == 200 and checked.json()["can_submit"] is True
    exact = {
        **checked.json()["normalized_request"],
        "preflight_sha256": checked.json()["preflight_sha256"],
    }
    result = client.post(
        "/api/v1/engine-runs", headers={**headers, "Idempotency-Key": key}, json=exact
    )
    assert result.status_code == 201, result.text
    return result.json(), exact

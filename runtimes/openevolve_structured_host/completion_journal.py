"""Observed027 controller completion, separate from unchanged absence-only Stop.

Only the protected supervisor produces this after exact report EOF, original
unit deadline and independent whole-unit absence. Parsed bytes are evidence,
never an execution grant. Runtime monotonic timestamps are frozen integer ns.
"""

import hashlib
import json
from typing import Literal

from pydantic import Field, field_validator, model_validator

from packages.research_runtime.process_contracts import canonical, digest
from packages.research_runtime.process_declarations_v2 import SHA, Closed
from runtimes.openevolve_structured.trusted_report import report_bytes
from runtimes.openevolve_structured_host.process_journal import read_controller_journal
from runtimes.research_supervisor.numeric_journal import read_numeric_record
from runtimes.research_supervisor.supervisor import operation_path


class ControllerCompletion(Closed):
    schema_version: Literal["mineagent.oe-structured-controller-completion.v1"] = (
        "mineagent.oe-structured-controller-completion.v1"
    )
    launch_sha256: SHA
    identity_sha256: SHA
    child_sha256: SHA
    stop_sha256: SHA
    started_report: str = Field(min_length=1, max_length=1024)
    quiescent_report: str = Field(min_length=1, max_length=1024)
    started_report_sha256: SHA
    quiescent_report_sha256: SHA
    reports_eof: Literal[True]
    daemon_exit_code: Literal[124]
    daemon_oom_killed: Literal[False]
    original_deadline_monotonic_ns: int = Field(strict=True, gt=0)
    kernel_absent_monotonic_ns: int = Field(strict=True, gt=0)

    @field_validator("reports_eof", "daemon_oom_killed", mode="before")
    @classmethod
    def booleans(cls, value):
        if type(value) is not bool:
            raise ValueError("Exact observed boolean required")
        return value

    @field_validator("daemon_exit_code", mode="before")
    @classmethod
    def exit_integer(cls, value):
        if type(value) is not int:
            raise ValueError("Exact observed exit code required")
        return value

    @model_validator(mode="after")
    def exact_reports(self):
        started, finished = (
            json.loads(self.started_report),
            json.loads(self.quiescent_report),
        )
        for kind, value, raw, sha in (
            ("started", started, self.started_report, self.started_report_sha256),
            (
                "quiescent",
                finished,
                self.quiescent_report,
                self.quiescent_report_sha256,
            ),
        ):
            expected = report_bytes(
                kind,
                value["child_namespace_pid"],
                value["child_start_ticks"],
                value.get("child_exit_code"),
            )
            if (
                raw.encode("ascii") != expected
                or hashlib.sha256(expected).hexdigest() != sha
            ):
                raise ValueError("Exact original trusted report bytes required")
        if (
            finished["child_exit_code"] != 0
            or (started["child_namespace_pid"], started["child_start_ticks"])
            != (finished["child_namespace_pid"], finished["child_start_ticks"])
            or not self.original_deadline_monotonic_ns
            <= self.kernel_absent_monotonic_ns
            <= self.original_deadline_monotonic_ns + 1000000000
        ):
            raise ValueError(
                "Successful child and original whole-unit deadline required"
            )
        return self


def validate_completion(value, launch, identity, child, stop):
    value = ControllerCompletion.model_validate_json(
        canonical(value.model_dump(mode="json"))
    )
    if (
        child is None
        or stop is None
        or (
            value.launch_sha256,
            value.identity_sha256,
            value.child_sha256,
            value.stop_sha256,
            value.started_report_sha256,
        )
        != (
            digest(launch.model_dump(mode="json")),
            digest(identity.model_dump(mode="json")),
            digest(child.model_dump(mode="json")),
            digest(stop.model_dump(mode="json")),
            child.started_report_sha256,
        )
    ):
        raise ValueError("Completion differs from exact observed stopped chain")
    return value


def observed_completion(
    launch,
    identity,
    child,
    stop,
    *,
    reports,
    reports_eof,
    daemon_exit_code,
    daemon_oom_killed,
    deadline_ns,
    kernel_absent_ns,
):
    if len(reports) != 2:
        raise ValueError("Exactly two original trusted reports required")
    raws = tuple(canonical(report) + b"\n" for report in reports)
    value = ControllerCompletion(
        launch_sha256=digest(launch.model_dump(mode="json")),
        identity_sha256=digest(identity.model_dump(mode="json")),
        child_sha256=digest(child.model_dump(mode="json")),
        stop_sha256=digest(stop.model_dump(mode="json")),
        started_report=raws[0].decode("ascii"),
        quiescent_report=raws[1].decode("ascii"),
        started_report_sha256=hashlib.sha256(raws[0]).hexdigest(),
        quiescent_report_sha256=hashlib.sha256(raws[1]).hexdigest(),
        reports_eof=reports_eof,
        daemon_exit_code=daemon_exit_code,
        daemon_oom_killed=daemon_oom_killed,
        original_deadline_monotonic_ns=deadline_ns,
        kernel_absent_monotonic_ns=kernel_absent_ns,
    )
    return validate_completion(value, launch, identity, child, stop)


def read_controller_completion(root, admission):
    chain = read_controller_journal(root, admission, with_child=True, with_stop=True)
    raw = read_numeric_record(
        operation_path(root, admission) / "control" / "completion.json"
    )
    value = ControllerCompletion.model_validate_json(canonical(raw))
    return validate_completion(value, *chain)

"""Test-only observation of the unchanged portable availability check.

Never serialize command arguments, output, environment or exception messages.
No retry: each probe executes the original availability path exactly once.
"""

import json
import subprocess

import pytest

from runtimes.evaluator.portable_runner import PortableDockerEvaluator
from runtimes.evaluator.runner import IsolationUnavailable


class DiagnosticPortableDockerEvaluator(PortableDockerEvaluator):
    def _command(self, *args, timeout=10):
        if not getattr(self, "_availability_probe", False):
            return super()._command(*args, timeout=timeout)
        stage = "other"
        if args == ("info", "--format", "{{json .}}"):
            stage = "daemon_info"
        if args[:2] == ("image", "inspect"):
            index = self._availability_image_index
            self._availability_image_index += 1
            if index < 2:
                role = ("engine", "policy")[index]
                if args == (
                    "image",
                    "inspect",
                    self.images[role],
                    "--format",
                    "{{.Id}}",
                ):
                    stage = role + "_image"
        self._availability_result = {
            "stage": stage,
            "reason": "verification_rejected",
            "exit_code": None,
        }
        try:
            result = super()._command(*args, timeout=timeout)
        except IsolationUnavailable as exc:
            cause = exc.__cause__
            self._availability_result["reason"] = (
                "command_timeout"
                if isinstance(cause, subprocess.TimeoutExpired)
                else "command_os_error"
                if isinstance(cause, OSError)
                else "command_unavailable"
            )
            raise
        code = result.returncode
        if type(code) is int and -255 <= code <= 255:
            self._availability_result["exit_code"] = code
        if code:
            self._availability_result["reason"] = "command_nonzero"
        return result

    def availability_diagnostic(self):
        self._availability_result = {
            "stage": "other",
            "reason": "verification_rejected",
            "exit_code": None,
        }
        self._availability_image_index = 0
        self._availability_probe = True
        try:
            available = super().available()
        finally:
            self._availability_probe = False
        if available:
            return {
                "available": True,
                "stage": "complete",
                "reason": "ready",
                "exit_code": 0,
            }
        return {"available": False, **self._availability_result}


def assert_available(runner):
    diagnostic = runner.availability_diagnostic()
    if not diagnostic["available"]:
        pytest.fail(json.dumps(diagnostic, sort_keys=True), pytrace=False)

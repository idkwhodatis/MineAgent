import hashlib

import pytest

from runtimes.openevolve.patches.full_loop_evaluation_smoke import fixture_diff_response
from runtimes.openevolve.probe_diagnostics import summarize


@pytest.mark.parametrize(
    "history",
    ["probe_value = 0", "probe_value = 2", "```python\nprobe_value = 99\n```"],
)
def test_synthetic_diff_uses_exact_parent_not_first_history_value(history):
    code = "# EVOLVE-BLOCK-START\nprobe_value = 2\n# EVOLVE-BLOCK-END\n"
    prompt = f"# Program Evolution History\n{history}\n# Current Program\n```python\n{code}\n```\n# Task\nprobe_value = 91"
    reply = fixture_diff_response(
        prompt,
        {
            "native_iteration": 3,
            "parent_source_sha256": hashlib.sha256(code.encode()).hexdigest(),
        },
    )
    assert (
        "SEARCH\nprobe_value = 2\n" in reply and "=======\nprobe_value = 3\n" in reply
    )


@pytest.mark.parametrize("fault", ["missing", "duplicate", "wrong_hash", "bool"])
def test_synthetic_diff_rejects_wrong_current_source(fault):
    code = "probe_value = 2\n"
    prompt = f"# Current Program\n```python\n{code}\n```"
    context = {
        "native_iteration": 3,
        "parent_source_sha256": hashlib.sha256(code.encode()).hexdigest(),
    }
    if fault == "missing":
        prompt = "probe_value = 2"
    elif fault == "duplicate":
        prompt += "\n" + prompt
    elif fault == "wrong_hash":
        context["parent_source_sha256"] = "0" * 64
    else:
        context["native_iteration"] = True
    with pytest.raises(ValueError):
        fixture_diff_response(prompt, context)


def test_failure_diagnostic_never_exports_raw_exception_or_paths():
    secret = "secret-user-source-or-token"
    stderr = f'File "/{secret}/full_loop_evaluation_smoke.py", line 468, in main\n    {secret}\nAssertionError: {secret}\n'
    report = summarize("eligible", 1, stderr)
    assert report["exception"] == "AssertionError"
    assert report["frames"] == [
        {"module": "full_loop_evaluation_smoke.py", "line": 468}
    ]
    assert secret not in str(report)


def test_untrusted_failure_class_and_module_are_not_exported():
    report = summarize(
        "eligible", 1, 'File "/secret.py", line 1, in main\nSECRET_TOKEN: leaked'
    )
    assert report["exception"] == "unclassified" and report["frames"] == []

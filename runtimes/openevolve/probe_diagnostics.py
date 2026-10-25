"""Outcome-only failure summary. Never copy exception messages or payload bytes."""

import json
import re
from pathlib import Path

PROBES = {"installed", "eligible", "children", "seed", "fallback", "bridge"}
EXCEPTIONS = {
    "AssertionError",
    "ValueError",
    "TypeError",
    "RuntimeError",
    "PermissionError",
    "FileNotFoundError",
    "ModuleNotFoundError",
    "ImportError",
    "OSError",
    "SearchControlAbort",
    "BrokenProcessPool",
}
FILES = {
    "full_loop_evaluation_smoke.py",
    "evaluator_bridge_smoke.py",
    "verify_openevolve_proof.py",
    "observer.py",
    "observer_transport.py",
    "process_parallel.py",
    "evaluator.py",
    "database.py",
    "controller.py",
    "api.py",
    "runtime_config.py",
    "broker_client.py",
}


def summarize(name, exit_code, stderr):
    if name not in PROBES or type(exit_code) is not int or not 0 < exit_code <= 255:
        raise ValueError("Closed probe failure identity required")
    if type(stderr) is not str or len(stderr.encode()) > 1048576:
        stderr = ""
    exceptions = re.findall(r"^([A-Za-z_][A-Za-z_0-9]*)(?::|$)", stderr, re.MULTILINE)
    known = [value for value in exceptions if value in EXCEPTIONS]
    frames = re.findall(r'File "[^"\n]*/([^/"\n]+)", line ([0-9]{1,7}),', stderr)
    frames = [
        {"module": name, "line": int(line)} for name, line in frames if name in FILES
    ]
    return {
        "schema": "mineagent.openevolve-probe-failure.v1",
        "probe": name,
        "exit_code": exit_code,
        "exception": known[-1] if known else "unclassified",
        "frames": frames[-4:],
    }


if __name__ == "__main__":
    import sys

    name, code, filename = sys.argv[1:]
    path = Path(filename)
    raw = path.read_bytes() if path.stat().st_size <= 1048576 else b""
    result = summarize(name, int(code), raw.decode("utf-8", errors="replace"))
    output = path.parent / (name + ".failure.json")
    output.write_text(json.dumps(result, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))

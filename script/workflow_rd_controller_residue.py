"""Closed failure-only observation; never replaces physical containment gates."""

from argparse import ArgumentParser
from subprocess import DEVNULL, PIPE, Popen, SubprocessError, TimeoutExpired

from script.workflow_heartbeat_production_report import ROOT, outcome

_LABEL = "label=io.mineagent.managed=mineagent-rdagent-orchestrator-v2"
_PHASES = ("after_bridge", "after_restore")
_STATES = {
    b"created",
    b"restarting",
    b"running",
    b"removing",
    b"paused",
    b"exited",
    b"dead",
}
_CMD = ["docker", "ps", "--all", "--filter", _LABEL, "--format", "{{.State}}"]
_MAX_BYTES = 4096


def _classify(raw):
    if type(raw) is not bytes or len(raw) > _MAX_BYTES:
        return "unavailable"
    states = set(raw.splitlines())
    if not states:
        return "absent_observed"
    if not states <= _STATES:
        return "unavailable"
    if len(states) > 1:
        return "mixed"
    return states.pop().decode("ascii")


def _capture():
    # Only the fixed Docker selection above is queried. A dedicated timeout
    # bounds the single reader by killing its own CLI, not a container.
    from threading import Timer

    with Popen(_CMD, stdout=PIPE, stderr=DEVNULL) as proc:
        timer = Timer(8, proc.kill)
        timer.daemon = True
        timer.start()
        try:
            raw = proc.stdout.read(_MAX_BYTES + 1)
            if len(raw) > _MAX_BYTES:
                proc.kill()
                return None
            if proc.wait(timeout=2) != 0:
                return None
            return raw
        except TimeoutExpired:
            proc.kill()
            return None
        finally:
            timer.cancel()


def project(phase, root=ROOT):
    if phase not in _PHASES:
        raise ValueError("Closed teardown phase required")
    try:
        state = _classify(_capture())
    except (OSError, SubprocessError):
        state = "unavailable"
    # Do not bypass the gated report (no phase details, failure codes, raw
    # JUnit, or status message). This is only the exact case's coarse verdict.
    bridge_status = outcome(root, "bridge")[0]
    return (
        f"RD_CONTROLLER_RESIDUE_V1 phase={phase} "
        f"docker_state={state} bridge_status={bridge_status}"
    )


def main():
    parser = ArgumentParser()
    parser.add_argument("phase", choices=_PHASES)
    args = parser.parse_args()
    print(project(args.phase))


if __name__ == "__main__":
    main()

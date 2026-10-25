"""Trusted fixed-image PID1 wall limit, independent of the host worker's life."""

import os
import signal
import subprocess
import sys
import time

# N1 launches only the trusted fixed fixture, never uploaded/user code. Do not
# make PID1 nondumpable: host kernel namespace observation must remain possible.
# A future untrusted-engine profile needs reviewed child/parent memory isolation.
if len(sys.argv) != 2 or not sys.argv[1].isdigit():
    raise SystemExit(2)
remaining = min(60.0, int(sys.argv[1]) / 1000 - time.time())
if remaining <= 0:
    raise SystemExit(124)
deadline = time.monotonic() + remaining
child = subprocess.Popen(
    ["python", "-I", "/opt/mineagent/fixture.py"], start_new_session=True
)
try:
    child.wait(timeout=max(0.001, deadline - time.monotonic()))
except subprocess.TimeoutExpired:
    os.killpg(child.pid, signal.SIGKILL)
    child.wait()
    raise SystemExit(124) from None
finally:
    # Exit of PID1 additionally destroys every member of this PID namespace,
    # including descendants that created a different process group/session.
    if child.poll() is None:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()

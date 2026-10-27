# Explicit single-generation protected controller successor; default registry closed.
FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
LABEL io.mineagent.managed="mineagent-rdagent-orchestrator-v2"
COPY runtimes/rdagent/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements.lock
COPY .runtime/rdagent-wheels /tmp/base-wheels
COPY .runtime/rdagent-operational-wheels /tmp/operational-wheels
RUN python -c 'import hashlib,json,pathlib; p=pathlib.Path("/tmp/base-wheels"); r=json.loads((p/"build-report.json").read_text()); assert all(hashlib.sha256((p/r[k]["filename"]).read_bytes()).hexdigest()==r[k]["sha256"] for k in ("upstream","adapter")); p=pathlib.Path("/tmp/operational-wheels"); r=json.loads((p/"operational-build-report.json").read_text())["operational"]; assert hashlib.sha256((p/r["filename"]).read_bytes()).hexdigest()==r["sha256"]' \
 && pip install --no-cache-dir --no-deps /tmp/base-wheels/*.whl /tmp/operational-wheels/*.whl \
 && pip check && rm -rf /tmp/base-wheels /tmp/operational-wheels /tmp/requirements.lock
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /tmp
USER 0:0
ENTRYPOINT ["python", "-I", "-m", "runtimes.rdagent_operational.deadline"]

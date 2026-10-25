# Disabled compatibility proof only: no provider, host server or candidate runner.
FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
LABEL io.mineagent.managed="mineagent-rdagent-compatibility-v1"
COPY runtimes/rdagent/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements.lock
# CI builds these twice from the exact pinned source and checks the frozen proof.
COPY .runtime/rdagent-wheels /tmp/wheels
RUN python -c 'import hashlib,json,pathlib; p=pathlib.Path("/tmp/wheels"); r=json.loads((p/"build-report.json").read_text()); assert all(hashlib.sha256((p/r[k]["filename"]).read_bytes()).hexdigest()==r[k]["sha256"] for k in ("upstream","adapter"))' \
 && pip install --no-cache-dir --no-deps /tmp/wheels/*.whl \
 && pip check \
 && rm -rf /tmp/wheels /tmp/requirements.lock
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /tmp
USER 65532:65532
CMD ["python", "-I", "-m", "runtimes.rdagent.probe_costeer"]

# Lifecycle proof only. No application launcher admits this profile.
FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
LABEL io.mineagent.managed="mineagent-disabled-hostile-deadline-v2"
COPY runtimes/hostile_deadline/contract.py runtimes/hostile_deadline/deadline.py runtimes/hostile_deadline/protected_output.py /usr/local/lib/python3.12/site-packages/runtimes/hostile_deadline/
USER 0:0
WORKDIR /
ENTRYPOINT ["python", "-I", "-m", "runtimes.hostile_deadline.deadline"]

# Disabled numeric successor; does not inherit stdlib hostile-proof acceptance.
FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS dependencies
COPY .wheelhouse-factor-numeric /wheelhouse
COPY runtimes/factor_execution/requirements.lock /requirements.lock
RUN python -m pip install --no-index --find-links=/wheelhouse --require-hashes --only-binary=:all: --no-deps --prefix=/opt/numeric -r /requirements.lock
FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
LABEL io.mineagent.managed="mineagent-disabled-factor-numeric-candidate-v2"
COPY --from=dependencies /opt/numeric/ /usr/local/
COPY runtimes/factor_execution/requirements.lock /opt/mineagent/requirements.lock
COPY runtimes/hostile_deadline/contract.py runtimes/hostile_deadline/deadline.py /usr/local/lib/python3.12/site-packages/runtimes/hostile_deadline/
COPY runtimes/factor_execution/numeric_deadline.py runtimes/factor_execution/probe_dependencies.py runtimes/factor_execution/protected_output.py runtimes/factor_execution/candidate_deadline.py /usr/local/lib/python3.12/site-packages/runtimes/factor_execution/
USER 0:0
WORKDIR /
ENTRYPOINT ["python","-I","-m","runtimes.factor_execution.candidate_deadline"]

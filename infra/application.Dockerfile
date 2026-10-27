# Separate installed dependency closures; reviewed source is data, never imported
# candidate code. The CLI exists only in the trusted research/supervisor target.
ARG DOCKER_CLI_IMAGE
ARG PYTHON_IMAGE=python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
FROM ${DOCKER_CLI_IMAGE} AS docker-cli
# Separate locked SDK child. Never replace the trusted API/research closure.
FROM ${PYTHON_IMAGE} AS provider-sdk
COPY runtimes/rdagent_provider/requirements.lock /tmp/provider-requirements.lock
RUN python -m venv --copies /opt/mineagent-provider \
    && /opt/mineagent-provider/bin/python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r /tmp/provider-requirements.lock \
    && /opt/mineagent-provider/bin/python -m pip check \
    && /opt/mineagent-provider/bin/python -I -c 'import importlib.metadata as m; assert m.version("openai") == "2.54.0"; assert m.version("httpx") == "0.28.1"' \
    && rm /tmp/provider-requirements.lock

FROM ${PYTHON_IMAGE} AS source
WORKDIR /opt/tradegrad
COPY .dockerignore ./
COPY backend ./backend
COPY workers ./workers
COPY packages ./packages
COPY runtimes ./runtimes
COPY src ./src
COPY script ./script
COPY seed_strategies ./seed_strategies
COPY tests/baseline/fixtures ./tests/baseline/fixtures
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/tradegrad HOME=/tmp

FROM source AS api
COPY --from=provider-sdk /opt/mineagent-provider /opt/mineagent-provider
RUN pip install --no-cache-dir --require-hashes -r backend/requirements.lock \
    && python -m script.validate_runtime_split --runtime api
USER 65532:65532
ENTRYPOINT ["python", "-m", "script.compose_runtime"]
CMD ["run", "api"]

FROM source AS research
COPY --from=provider-sdk /opt/mineagent-provider /opt/mineagent-provider
RUN pip install --no-cache-dir --require-hashes -r runtimes/research/requirements.lock \
    && python -m script.validate_runtime_split --runtime research
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
USER 65532:65532
ENTRYPOINT ["python", "-m", "script.compose_runtime"]
CMD ["run", "research-worker"]

FROM source AS data
RUN pip install --no-cache-dir --require-hashes -r runtimes/data/requirements.lock \
    && HOME=/tmp/openbb-build-home openbb-build \
    && rm -rf /tmp/openbb-build-home \
    && python -m script.validate_runtime_split --runtime data
USER 65532:65532
ENTRYPOINT ["python", "-m", "script.compose_runtime"]
CMD ["run", "data-worker"]

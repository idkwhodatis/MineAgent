# Baseline dependency image only; no research admission or provider execution.
FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS build
COPY runtimes/openevolve/build-requirements.lock /tmp/build-requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/build-requirements.lock
# Created only by materialize_source.py from the exact verified external checkout.
COPY .runtime/openevolve-source /tmp/source
COPY runtimes/openevolve/verify_build_inputs.py runtimes/openevolve/upstream-source.json /tmp/verification/
RUN python -I /tmp/verification/verify_build_inputs.py /tmp/source
ENV SOURCE_DATE_EPOCH=1792818759
RUN python -m build --wheel --no-isolation /tmp/source --outdir /wheels \
 && echo 'b5d822abd49e9a977228838de593c1460dd61ae9bad21e44042bd53cf3b32e98  /wheels/openevolve-0.4.0-py3-none-any.whl' | sha256sum --check -

FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
LABEL io.mineagent.managed="mineagent-openevolve-baseline-v1"
WORKDIR /opt/mineagent
COPY runtimes/openevolve/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock
COPY --from=build /wheels/openevolve-0.4.0-py3-none-any.whl /tmp/upstream.whl
RUN mv /tmp/upstream.whl /tmp/openevolve-0.4.0-py3-none-any.whl \
 && pip install --no-cache-dir --no-deps /tmp/openevolve-0.4.0-py3-none-any.whl \
 && pip check \
 && rm /tmp/openevolve-0.4.0-py3-none-any.whl
COPY runtimes/openevolve /opt/mineagent/runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65532:65532
CMD ["python", "-I", "/opt/mineagent/runtime/verify_runtime.py"]

# Disabled, installed-only synthetic proof; never production engine admission.
FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS build
# Signed Debian compiler packages are recorded separately from Python hashlocks.
RUN apt-get update && apt-get install -y --no-install-recommends g++ libgomp1 \
 && mkdir /build-receipts \
 && dpkg-query -W > /build-receipts/debian-build-packages.txt \
 && rm -rf /var/lib/apt/lists/*
COPY runtimes/qlib/build-requirements.lock /locks/build.lock
COPY runtimes/qlib/requirements.lock /locks/native.lock
COPY runtimes/qlib/runner-requirements.lock /locks/runner.lock
COPY runtimes/qlib/verify_build.py runtimes/qlib/source-inventory.json runtimes/qlib/wheel-proof.json /verification/
COPY .runtime/qlib-source /source
COPY .runtime/qlib-wheels /profiles
RUN python -I /verification/verify_build.py source /source --inventory /verification/source-inventory.json \
 && python -I /verification/verify_build.py profile /profiles --inventory /verification/wheel-proof.json \
 && python -m venv /build-tools \
 && /build-tools/bin/pip install --no-cache-dir --require-hashes --only-binary=:all: -r /locks/build.lock
ENV CC=gcc CXX=g++ SOURCE_DATE_EPOCH=1760000000 SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PYQLIB=0.9.8
RUN mkdir -p /wheels/native /wheels/runner \
 && /build-tools/bin/pip wheel --no-cache-dir --no-deps --no-build-isolation --require-hashes -r /locks/native.lock -w /wheels/native \
 && /build-tools/bin/pip wheel --no-cache-dir --no-deps --no-build-isolation --require-hashes -r /locks/runner.lock -w /wheels/runner \
 && /build-tools/bin/pip wheel --no-cache-dir --no-deps --no-build-isolation /source -w /wheels/native \
 && cp /profiles/mineagent_qlib_native_adapter_profile-0.1.0-py3-none-any.whl /wheels/native/ \
 && cp /profiles/mineagent_qlib_runner_upstream_profile-0.1.0-py3-none-any.whl /profiles/mineagent_qlib_runner_adapter_profile-0.1.0-py3-none-any.whl /wheels/runner/ \
 && python -I /verification/verify_build.py wheelhouse /wheels/native \
 && python -I /verification/verify_build.py wheelhouse /wheels/runner \
 && cp /locks/* /build-receipts/ \
 && cp /verification/source-inventory.json /verification/wheel-proof.json /build-receipts/

FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
LABEL io.mineagent.managed="mineagent-qlib-disabled-proof-v1"
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
 && mkdir -p /opt/mineagent-build-receipts \
 && dpkg-query -W > /opt/mineagent-build-receipts/debian-runtime-packages.txt \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /wheels /wheels
COPY --from=build /build-receipts /opt/mineagent-build-receipts
RUN python -m venv /opt/qlib && python -m venv /opt/runner \
 && /opt/qlib/bin/pip install --no-index --find-links=/wheels/native --require-hashes --only-binary=:all: --no-deps -r /wheels/native/install.lock \
 && /opt/runner/bin/pip install --no-index --find-links=/wheels/runner --require-hashes --only-binary=:all: --no-deps -r /wheels/runner/install.lock \
 && /opt/qlib/bin/pip check && /opt/runner/bin/pip check \
 && cp /wheels/native/wheel-sha256.json /opt/mineagent-build-receipts/native-wheel-sha256.json \
 && cp /wheels/runner/wheel-sha256.json /opt/mineagent-build-receipts/runner-wheel-sha256.json \
 && rm -rf /wheels
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MLFLOW_DISABLE_TELEMETRY=true DO_NOT_TRACK=true OMP_NUM_THREADS=1
WORKDIR /tmp
USER 65532:65532
CMD ["/opt/runner/bin/python", "-I", "-m", "runtimes.qlib.probe_container"]

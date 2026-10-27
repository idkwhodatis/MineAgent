# New 024 protected controller, not historical phase6 compatibility authority.
FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS build
COPY runtimes/openevolve/build-requirements.lock /tmp/build-requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/build-requirements.lock
COPY .runtime/openevolve-source /tmp/source
COPY runtimes/openevolve /build/runtimes/openevolve
COPY runtimes/openevolve_phase7 /build/runtimes/openevolve_phase7
COPY runtimes/openevolve_phase8 /build/runtimes/openevolve_phase8
RUN python -I /build/runtimes/openevolve/verify_build_inputs.py /tmp/source \
 && python -I /build/runtimes/openevolve/patches/apply.py /tmp/source \
 && python -I /build/runtimes/openevolve/patches/apply.py --control /tmp/source \
 && python -I /build/runtimes/openevolve/patches/apply.py --observer /tmp/source \
 && python -I /build/runtimes/openevolve/patches/apply.py --prompt /tmp/source \
 && python -I /build/runtimes/openevolve/patches/apply.py --frontier /tmp/source \
 && python -I /build/runtimes/openevolve/patches/apply.py --evaluation /tmp/source \
 && python -I -c "import sys; sys.path.insert(0,'/build'); from runtimes.openevolve_phase7.apply import apply; apply('/tmp/source'); from runtimes.openevolve_phase8.apply import apply; apply('/tmp/source')"
ENV SOURCE_DATE_EPOCH=1792818759
RUN python -m build --wheel --no-isolation /tmp/source --outdir /wheels \
 && echo '01a3d7fe9f76d6d49dca5f55bde293727ff5f2d64f452f2e09d89282bbb1e47b  /wheels/openevolve-0.4.0-py3-none-any.whl' | sha256sum --check -

FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
LABEL io.mineagent.managed="mineagent-openevolve-orchestrator-v1"
COPY runtimes/openevolve/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements.lock
COPY --from=build /wheels/openevolve-0.4.0-py3-none-any.whl /tmp/openevolve.whl
RUN mv /tmp/openevolve.whl /tmp/openevolve-0.4.0-py3-none-any.whl \
 && pip install --no-cache-dir --no-deps /tmp/openevolve-0.4.0-py3-none-any.whl \
 && pip check && rm /tmp/openevolve-0.4.0-py3-none-any.whl /tmp/requirements.lock
# Closed installed runtime list; no host backend/server/SQL or provider credentials.
COPY packages/openevolve_adapter/__init__.py packages/openevolve_adapter/observer.py packages/openevolve_adapter/broker_client.py packages/openevolve_adapter/runtime_config.py packages/openevolve_adapter/runtime_profile.py packages/openevolve_adapter/evaluator_bridge.py packages/openevolve_adapter/evaluator_protocol.py packages/openevolve_adapter/contracts.py packages/openevolve_adapter/objective.py packages/openevolve_adapter/source.py packages/openevolve_adapter/checkpoint_native.py packages/openevolve_adapter/sampling_control.py packages/openevolve_adapter/placement_observer.py packages/openevolve_adapter/execution_inputs.py packages/openevolve_adapter/operational_profile.py /usr/local/lib/python3.12/site-packages/packages/openevolve_adapter/
# Explicit adapter seam overlay. Native phase8 algorithm/source/wheel is unchanged.
COPY runtimes/openevolve_operational/observer_transport_facade.py /usr/local/lib/python3.12/site-packages/packages/openevolve_adapter/observer_transport.py
COPY packages/research_runtime/__init__.py packages/research_runtime/broker_contracts.py packages/research_runtime/budget_contracts.py packages/research_runtime/control.py /usr/local/lib/python3.12/site-packages/packages/research_runtime/
COPY packages/tradegrad_adapter/independent_results.py packages/tradegrad_adapter/results.py packages/tradegrad_adapter/config.py packages/tradegrad_adapter/native_contract.py /usr/local/lib/python3.12/site-packages/packages/tradegrad_adapter/
COPY packages/research_trace/contracts.py /usr/local/lib/python3.12/site-packages/packages/research_trace/
COPY runtimes/__init__.py /usr/local/lib/python3.12/site-packages/runtimes/__init__.py
COPY runtimes/openevolve_operational/__init__.py runtimes/openevolve_operational/deadline.py runtimes/openevolve_operational/trusted_report.py runtimes/openevolve_operational/entrypoint.py runtimes/openevolve_operational/evaluator.py runtimes/openevolve_operational/bootstrap.py runtimes/openevolve_operational/profile.py runtimes/openevolve_operational/runtime_binding.py runtimes/openevolve_operational/transport.py runtimes/openevolve_operational/fixed_input.py runtimes/openevolve_operational/checkpoint_transfer.py /usr/local/lib/python3.12/site-packages/runtimes/openevolve_operational/
COPY runtimes/hostile_deadline/deadline.py /usr/local/lib/python3.12/site-packages/runtimes/hostile_deadline/deadline.py
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /tmp
USER 0:0
ENTRYPOINT ["python", "-I", "-m", "runtimes.openevolve_operational.deadline"]

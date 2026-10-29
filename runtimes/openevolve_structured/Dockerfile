# Distinct027 derivative of the measured protected024 controller image.
# No dependency install/provider configuration; never claims the old wheel/source hash.
ARG OPENEvolve_PARENT_IMAGE
FROM ${OPENEvolve_PARENT_IMAGE}
USER 0:0
LABEL io.mineagent.managed="mineagent-oe-structured-orchestrator-v1"
COPY packages/openevolve_structured /usr/local/lib/python3.12/site-packages/packages/openevolve_structured/
COPY packages/factors/__init__.py packages/factors/contracts.py packages/factors/execution_snapshot.py /usr/local/lib/python3.12/site-packages/packages/factors/
COPY packages/structured_factor_strategy/__init__.py packages/structured_factor_strategy/contracts.py /usr/local/lib/python3.12/site-packages/packages/structured_factor_strategy/
COPY packages/strategy_sdk /usr/local/lib/python3.12/site-packages/packages/strategy_sdk/
COPY runtimes/openevolve_structured /usr/local/lib/python3.12/site-packages/runtimes/openevolve_structured/
RUN python -I /usr/local/lib/python3.12/site-packages/runtimes/openevolve_structured/install_hooks.py /usr/local/lib/python3.12/site-packages
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /tmp
ENTRYPOINT ["python","-I","-m","runtimes.openevolve_structured.deadline"]

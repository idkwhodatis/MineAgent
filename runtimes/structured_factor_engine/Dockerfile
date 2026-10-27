# Distinct closed-operator successor of the measured unchanged trusted engine.
# Existing AKQuant/calendar/SDK/Controller bytes are retained, not reinstalled.
ARG TRUSTED_ENGINE_IMAGE
FROM ${TRUSTED_ENGINE_IMAGE}
LABEL io.mineagent.managed="mineagent-disabled-structured-factor-engine-v1"
COPY runtimes/structured_factor_engine/trusted_parent.pth /usr/local/lib/python3.12/site-packages/mineagent_structured_parent.pth
COPY packages/structured_factor_strategy/__init__.py packages/structured_factor_strategy/contracts.py packages/structured_factor_strategy/operator.py packages/structured_factor_strategy/wire.py /usr/local/lib/python3.12/site-packages/packages/structured_factor_strategy/
COPY packages/factors/__init__.py packages/factors/contracts.py packages/factors/execution_snapshot.py /usr/local/lib/python3.12/site-packages/packages/factors/
COPY runtimes/structured_factor_engine/__init__.py runtimes/structured_factor_engine/entrypoint.py runtimes/structured_factor_engine/runtime.py runtimes/structured_factor_engine/protected_deadline.py runtimes/structured_factor_engine/protected_output.py /usr/local/lib/python3.12/site-packages/runtimes/structured_factor_engine/
COPY runtimes/hostile_deadline/contract.py runtimes/hostile_deadline/deadline.py /usr/local/lib/python3.12/site-packages/runtimes/hostile_deadline/
USER 0:0
WORKDIR /
ENTRYPOINT ["/usr/local/bin/python","-I","-m","runtimes.structured_factor_engine.protected_deadline"]
CMD []

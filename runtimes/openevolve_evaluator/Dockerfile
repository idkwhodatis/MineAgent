# Disabled protected successor over an exact measured existing native evaluator.
# Existing /opt/tradegrad source and installed dependency bytes remain unchanged.
ARG NATIVE_EVALUATOR_IMAGE
FROM ${NATIVE_EVALUATOR_IMAGE}
LABEL io.mineagent.managed="mineagent-disabled-openevolve-native-evaluator-v1"
COPY runtimes/openevolve_evaluator/protected_deadline.py runtimes/openevolve_evaluator/protected_output.py /usr/local/lib/python3.12/site-packages/runtimes/openevolve_evaluator/
COPY runtimes/hostile_deadline/contract.py runtimes/hostile_deadline/deadline.py /usr/local/lib/python3.12/site-packages/runtimes/hostile_deadline/
USER 0:0
WORKDIR /
ENTRYPOINT ["python","-I","-m","runtimes.openevolve_evaluator.protected_deadline"]
CMD []

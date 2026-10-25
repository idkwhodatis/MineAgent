FROM python:3.12.14-slim-bookworm
LABEL io.mineagent.managed="mineagent-research-supervisor-v1"
COPY runtimes/research_supervisor/fixture.py runtimes/research_supervisor/deadline.py /opt/mineagent/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65532:65532
ENTRYPOINT ["python", "-I", "/opt/mineagent/deadline.py"]

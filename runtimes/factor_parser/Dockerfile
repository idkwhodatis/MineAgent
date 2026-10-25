FROM python:3.12.14-slim-bookworm
LABEL io.mineagent.managed="mineagent-factor-parser-v1"
WORKDIR /opt/mineagent
COPY backend/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock && rm /tmp/requirements.lock
COPY packages/factors ./packages/factors
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/mineagent
USER 65532:65532
ENTRYPOINT ["python", "-m", "packages.factors.parser"]

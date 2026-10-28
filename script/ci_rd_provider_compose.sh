#!/usr/bin/env bash
# HOSTED ONLY: actual application images/services. No host worker substitution.
set -euo pipefail
[ "${GITHUB_ACTIONS:-}" = true ] || { echo 'Disposable hosted job required' >&2; exit 1; }
: "${COMPOSE_PROJECT_NAME:?}" "${TRADEGRAD_STATE_ROOT:?}" "${DOCKER_CLI_IMAGE:?}"
docker_cli=(docker --host "unix://${TRADEGRAD_DOCKER_SOCKET:?}")
compose=("${docker_cli[@]}" compose -f infra/compose.full.yml)
cleanup() {
  outcome=$?
  trap - EXIT
  # Stop the actual trusted worker first; no label-based native-unit removal.
  "${compose[@]}" stop --timeout 45 research-worker evaluator-reconciler research-dispatcher || outcome=1
  for label in io.mineagent.numeric.admission io.mineagent.analysis.admission io.mineagent.managed=mineagent-rdagent-orchestrator-v2; do
    if ids=$(timeout 15s "${docker_cli[@]}" ps --all --quiet --no-trunc --filter "label=$label"); then
      [ -z "$ids" ] || { echo 'Packaged RD native containment not confirmed' >&2; outcome=1; }
    else
      outcome=1
    fi
  done
  python -m script.ci_compose_cleanup || outcome=1
  # Publish a positive summary only after all flow assertions AND exact cleanup.
  if [ "$outcome" = 0 ]; then
    cp "$TRADEGRAD_STATE_ROOT/normal-compose-provider.json" var/numeric-proof/normal-compose-provider.json
  fi
  exit "$outcome"
}
trap cleanup EXIT
python -m script.compose_runtime prepare
"${compose[@]}" config --quiet
# These are genuine legacy images required by ordinary trusted-worker startup,
# not placeholders. Existing normal checks and security profile stay unchanged.
"${compose[@]}" --profile images build native-evaluator portable-engine portable-policy
"${compose[@]}" build api research-worker
"${compose[@]}" up -d --wait --wait-timeout 120 database
"${compose[@]}" run --rm --no-deps migrate
BOOTSTRAP_EMAIL="$E2E_EMAIL" BOOTSTRAP_PASSWORD="$E2E_PASSWORD" BOOTSTRAP_SYNTHETIC=0 BOOTSTRAP_E2E=0 BOOTSTRAP_SUPERUSER=0 \
  "${compose[@]}" run --rm --no-deps bootstrap >/dev/null
"${compose[@]}" up -d --no-deps --wait --wait-timeout 120 api
fixture="$TRADEGRAD_STATE_ROOT/rd-compose-fixture"
mkdir -m700 "$fixture"
cp script/ci_rd_compose_inside.py "$fixture/"
cp tests/integration/rd_provider_fixture.py tests/integration/rd_configured_dataset_fixture.py tests/integration/rd_submission_fixture.py tests/integration/rd_composed_profile_fixture.py "$fixture/"
# Read-only fixture source only. No checkout/credential/host-venv bind into apps.
chmod 644 "$fixture/"*.py
copy_fixture() {
  local container="$1" name
  [[ "$container" =~ ^[0-9a-f]{64}$ ]]
  # Let the configured nonroot container user own the writable private folder.
  # Write in the container mount namespace: daemon-side docker cp cannot reliably
  # address this private /tmp tmpfs. No host metadata or root user is imported.
  "${docker_cli[@]}" exec "$container" mkdir -m700 /tmp/mineagent-rd-compose
  for name in ci_rd_compose_inside.py rd_provider_fixture.py rd_configured_dataset_fixture.py rd_submission_fixture.py rd_composed_profile_fixture.py; do
    "${docker_cli[@]}" exec -i "$container" python -c '
import os, sys
raw = sys.stdin.buffer.read(1048577)
if len(raw) > 1048576:
    raise ValueError("Fixture source exceeds fixed bound")
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, "wb") as output:
    output.write(raw)
' "/tmp/mineagent-rd-compose/$name" < "$fixture/$name"
  done
}
api=$("${compose[@]}" ps --quiet api)
[[ "$api" =~ ^[0-9a-f]{64}$ ]]
copy_fixture "$api"
export MINEAGENT_COMPOSE_EMAIL="$E2E_EMAIL" MINEAGENT_COMPOSE_PASSWORD="$E2E_PASSWORD"
owner=$("${docker_cli[@]}" exec -e MINEAGENT_COMPOSE_EMAIL "$api" python /tmp/mineagent-rd-compose/ci_rd_compose_inside.py owner)
python -m script.ci_rd_compose_config "$owner"
export MINEAGENT_PROVIDER_ENV_FILE="$TRADEGRAD_STATE_ROOT/provider.env"
compose=("${docker_cli[@]}" compose --env-file "$MINEAGENT_PROVIDER_ENV_FILE" -f infra/compose.full.yml -f infra/compose.providers.yml)
"${compose[@]}" config --quiet
"${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 180 api research-worker evaluator-reconciler research-dispatcher
api=$("${compose[@]}" ps --quiet api)
worker=$("${compose[@]}" ps --quiet research-worker)
[[ "$api" =~ ^[0-9a-f]{64}$ && "$worker" =~ ^[0-9a-f]{64}$ ]]
copy_fixture "$api"
copy_fixture "$worker"
dispatcher=$("${compose[@]}" ps --quiet research-dispatcher)
reconciler=$("${compose[@]}" ps --quiet evaluator-reconciler)
# Inspect only the four exact disposable service IDs, never a daemon inventory.
"${docker_cli[@]}" exec "$worker" python -m script.ci_rd_compose_identity applications "$api" "$dispatcher" "$worker" "$reconciler"
export MINEAGENT_COMPOSE_OWNER="$owner"
"${docker_cli[@]}" exec --detach -e MINEAGENT_COMPOSE_OWNER "$worker" python -m script.ci_rd_compose_identity probe
# Localhost requirement remains exact: the synthetic HTTP fixture lives inside
# this one trusted worker's network namespace, not an externally allowed host.
"${docker_cli[@]}" exec --detach "$worker" python /tmp/mineagent-rd-compose/ci_rd_compose_inside.py provider
ready=0
for attempt in $(seq 1 30); do
  if "${docker_cli[@]}" exec "$worker" python -c 'from pathlib import Path; assert Path("/tmp/mineagent-rd-compose/provider-ready").read_text()=="ready\n"' >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[ "$ready" = 1 ] || { echo 'In-worker synthetic provider was not ready' >&2; exit 1; }
timeout 420s "${docker_cli[@]}" exec -e MINEAGENT_COMPOSE_EMAIL -e MINEAGENT_COMPOSE_PASSWORD "$api" \
  python /tmp/mineagent-rd-compose/ci_rd_compose_inside.py run > "$TRADEGRAD_STATE_ROOT/normal-compose-provider.json"
python - <<'PY'
import json, os
from pathlib import Path
value=json.loads((Path(os.environ['TRADEGRAD_STATE_ROOT'])/'normal-compose-provider.json').read_text())
assert value['status']=='passed' and value['native_units']==4
assert value['host_worker_used'] is False and 4 < value['sdk_transports'] <= 32
assert value['qualification']=='actual_compose_application_subset_not_full_frontend_or_live_provider'
PY
# Exact synthetic request observations remain inside the owned worker namespace;
# only bounded booleans/counts are checked, never raw prompts or credentials.
"${docker_cli[@]}" exec "$worker" python -c 'import json; from pathlib import Path; v=json.loads(Path("/tmp/mineagent-rd-compose/provider-observed.json").read_text()); assert v["guidance_seen"] is True and v["embedding_seen"] is True and 4 < v["requests"] <= 32'

# A real peer receipt alone is insufficient: the concurrent trusted-host probe
# also matched live /proc start ticks and exact container membership.
"${docker_cli[@]}" exec "$worker" python -c 'import json; from pathlib import Path; v=json.loads(Path("/tmp/mineagent-rd-compose/identity-observed.json").read_text()); assert v == {"actual_peer_and_host_identity":True,"unrelated_pid_rejected":True,"candidate_private_namespace":True,"candidate_privileges_dropped":True}'

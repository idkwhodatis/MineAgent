# Disabled RD-Agent compatibility profile

This is **not runtime admission, a production image, a durable broker, or candidate
containment acceptance**. The actual pinned `FactorCoSTEER.develop` / RAGEvoAgent
control flow is exercised with explicitly in-memory host execution/publication
stand-ins. No generated source is executed by these probes.

## Source and packaging

Upstream: Microsoft/RD-Agent commit
`484776c211e4fbbeef03e0ec00d6bbee7362a4f4` (MIT, preserved in LICENSE.rdagent).
`source-inventory.json` hashes the original tracked rdagent subtree. Original
`pyproject.toml` declares `packages=["rdagent"]`; a pristine local build produced
`rdagent-0.0.0-py3-none-any.whl`, 17,117 bytes, SHA256
`57e72ea1576d1a15163334d191f1ea4fbd263cf6b41f36ad33262c7812d78338`, with **only
six dist-info/license entries and no Python modules**. 0.0.0 was an explicit
source-build label, not an upstream release claim.

`build_profile.py` is an explicit packaging-only alternative, not a silent patch
to upstream metadata. It produces distinct distributions
`mineagent-rdagent-upstream-profile` and `mineagent-rdagent-adapter-profile`.
The former contains selected original core/log/oai/utils/knowledge/CoSTEER/factor
modules, kaggle/conf (an import dependency), and original YAML resources, all
byte-verified against source inventory. No upstream search/evaluation algorithm
is edited. It deliberately does not claim full RD-Agent CLI/dependency coverage.
The latter contains named platform adapter/bootstrap/probe modules and the small
contract/control dependencies, not the API, database, or credentials.

`requirements.lock` is the separate, hash-locked 134-distribution Python 3.12
Linux x86_64 compatibility closure. It does not replace the legacy application
lock. The build recipe uses Python standard library plus Git commit/worktree verification, sorted ZIP_STORED
members and fixed timestamps. `wheel-proof.json` records two-build-equal wheel
hashes, exact included bytes, source pin, recipe and dependency-lock hashes.
Re-run this proof after any included adapter change; an old proof is not current.

```sh
python runtimes/rdagent/build_profile.py --source /path/to/clean-pinned-git-checkout --output /tmp/rd-build1
python runtimes/rdagent/build_profile.py --source /path/to/clean-pinned-git-checkout --output /tmp/rd-build2
cmp /tmp/rd-build1/build-report.json /tmp/rd-build2/build-report.json
/path/to/fresh-python -m pip install --require-hashes --only-binary=:all: -r runtimes/rdagent/requirements.lock
/path/to/fresh-python -m pip install --no-deps /tmp/rd-build1/*.whl
cd /tmp
/path/to/fresh-python -I -m runtimes.rdagent.probe_costeer
/path/to/fresh-python -I -m runtimes.rdagent.probe_runtime
```

Both probes passed from installed wheels under `-I`, with no source checkout or
working directory on the import path. The CoSTEER probe asserts imported adapter,
bootstrap and upstream module paths are installed site-packages paths.

## Actual behavior and explicit seams

The baseline asserts malformed-but-JSON missing-code repair, three retained
source versions, actual upstream decisions false/false/true, 12 chat requests,
5 embedding requests, 3 host evaluation tickets and 11 repeated value reads.
Synthetic execution results and persistence are **stand-ins**, not actual
candidate failures or accepted evaluator resource accounting.

Bootstrap pins settings before first upstream import, disables remote LiteLLM
cost-map discovery, inherited custom log storage/UI, persistent caches and RAG
pickle recovery. Public logger storage is replaced by bounded outcome-only
logging. Platform durable source/feedback/history is still required; logging
is not its substitute. Tests refuse network connections and pickle file access.

Method wrappers attach explicit generation/output-format/critique/final-decision/
knowledge-query/knowledge-update phases without deriving identity from prompts
or stack frames. The host persists generation start **before knowledge query**.
Every outer call has a host call-context/exchange ID; **each inner invocation
allocates a distinct immutable broker request operation**. Continuation and
semantic repair can change request bytes and therefore cannot be transport
retries of the same operation. The host must validate these relations against
previous finish reason/parse outcome; response_format belongs in the immutable
versioned request envelope. Physical transport retries remain host-internal with
the same operation/request hash. There is no local ordinal authority or cache.

The transport probe asserts two continuation exchanges become four distinct
request operations, plus one embedding request. Malformed JSON then invalid
schema then valid JSON is one exchange but three request operations. Cancellation
between semantic repairs stops before another request. JSON parsing/refinement
uses actual upstream parser; bounded semantic retry omits upstream sleep and
catches only JSON/schema failures, never terminal BrokerAbort or transport errors.

## Not yet accepted

No production Docker image/digest, hostile-child-safe deadline wrapper, real
broker/PG persistence bridge, per-cutoff candidate or isolated HDF normalizer,
checkpoint/restart/restore acceptance, or paid/live transport is provided here.
New-purpose admission remains disabled. Never run arbitrary candidate Python or
parse untrusted native HDF in the API/orchestrator using these local probes.

## Prefix history correction before admission

The earlier disabled `mineagent.factor-prefix-input.v1` projector incorrectly
mounted L total sessions while shared `FactorDefinition.lookback_sessions=L`
means L **prior** sessions plus the current session. No RD runs were admitted
under that profile. This is not silently reinterpreted: the corrected serializer
is `mineagent.factor-prefix-input.v2`, explicit
`history_semantics=prior_sessions_plus_current`, with `lookback_prior_sessions`
1..63 and at most64 mounted sessions. Candidate profile is
`mineagent.factor-prefix-candidate.v2`; old prefix schema/profile values are
rejected. The generic legacy FactorDefinition and its fixed materializer are
unchanged. Tests show L=2 mounts3 prices and reproduces the fixed materializer's
numeric result, plus L=63→64 mounted and rejection of64 prior sessions.

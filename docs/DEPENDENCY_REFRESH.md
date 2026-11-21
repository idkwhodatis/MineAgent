# Dependency refresh qualification

The Python runtime boundaries remain separate. Regenerate the API, data,
evaluator, research, RD-Agent and provider locks in their own environments.
Do not resolve a single combined environment: the released numerical and
provider profiles have different closure and evidence requirements.

## Runtime changes

- API: the hashed Python 3.12 lock updates FastAPI, Starlette, Pydantic,
  SQLAlchemy, Procrastinate and their dependencies. The DuckDB 2.0.1
  comparison SQL uses the required `lambda` syntax and still applies the same
  numeric and decoded-size checks. Pytest 9.2.0 remains exactly pinned for
  reproducible test selection.
- Data/evaluator: AKShare's renamed bars endpoint continues to run inside the
  bounded SDK worker. The data, evaluator, research and portable-policy locks
  are separate; OpenBB and the packaged numerical contracts stay explicit.
- RD-Agent: Microsoft RD-Agent 1.0.0 is already the stable source pin. The
  reduced installed CoSTEER profile refreshes its closure while the live
  provider profile switches to the locked OpenAI 3/HTTPX2 client without
  adding retry, redirects or a new trust path. See [provider configuration](RD_CONFIGURED_PROVIDER.md).
- Frontend: the table and graph use the new TanStack Table and React Flow
  APIs. TypeScript 7 drives the build, while OpenAPI generation retains the
  TypeScript 6 JavaScript compiler API separately. See [frontend setup](../frontend/README.md).

## Compatibility holds

- OpenEvolve 0.4.1 is qualified in an [isolated candidate](../runtimes/openevolve_candidate/README.md).
  The patched production 0.4.0 runtime is not replaced: the 0.4.1 worker,
  database and checkpoint changes need a patch rebase and native acceptance.
  Mixed-mode evolution is not enabled on the production profile.
- AKQuant 0.3.27 and exchange-calendars 4.13.2 remain unchanged in contracts
  that carry the exact engine or calendar version in persisted evidence.
  Updating them requires migration and parity evidence, not just new wheels.
- Qlib, factor executor/normalizer source and platform wheels, historical
  baseline dependencies, and container/toolchain pins with native acceptance
  requirements are not changed in this candidate. Retain their reproducibility
  and rerun protected native workflows before broader promotion.

Local dependency, compilation and focused regression tests are not a
substitute for Docker/PostgreSQL, Windows or browser acceptance.

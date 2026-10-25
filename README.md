# MineAgent

A research workbench for persistent factors and observable strategy evolution.
RD-Agent develops factors; OpenEvolve evolves strategies; the application owns
immutable inputs, budgets, evidence, comparisons, recovery and visualization.

## Implementation status

Implementation follows [the staged plan](docs/MINEAGENT_IMPLEMENTATION_PLAN.md).
The deployment target is this repository, superseding the plan's original
same-TradeGrad-repository wording. Stage 0 imports the independently developed
application foundation and pins the legacy compatibility dependency. New factor
and search capabilities stay disabled until their own acceptance gates pass.
An imported test suite or a successful source checkout is not acceptance.

The reusable application baseline is TradeGrad commit
`6828f3bd429fc754648021bae703a14fa300b62e`, tree
`add06334d6cbcd9e5f359f2cb4a91f6902fed589`. Its four mandatory fixture jobs passed
in [run 40134103045](https://github.com/idkwhodatis/TradeGrad/actions/runs/40134103045).
MineAgent independently passed Stage 0 at commit
`d082c622ccdf2f20213ffbe63413bcc3c426949e`: all four mandatory jobs in
[run 40148585129](https://github.com/idkwhodatis/MineAgent/actions/runs/40148585129)
and the fresh-checkout migration boundary passed. ZIP artifact digests were
independently verified. This establishes the migrated legacy foundation, not
acceptance of the later factor/OpenEvolve/RD-Agent functionality.

Stage 1 adds the independent factor library and supervised package-import path.
It remains feature-gated until its own real database, containment, browser and
cold-restore acceptance completes. The old execution profile stays unchanged.

## Development

Read [provenance and compatibility setup](docs/PROVENANCE.md) first. Legacy source
is an explicitly pinned external dependency, not relicensed under MineAgent's
MIT license. Retained third-party notices continue to apply.

The existing [development and operations guide](docs/V1_OPERATIONS.md) describes
the isolated API, data, research and evaluator environments. Keep their lockfiles
separate. Historical `tradegrad.*` schema names and stored identities are compatibility
contracts, not product-brand strings to replace globally.

New feature settings are `FACTOR_LIBRARY_ENABLED`, `FACTOR_IMPORT_ENABLED`, `RDAGENT_RESEARCH_ENABLED` and
`OPENEVOLVE_RESEARCH_ENABLED`, all false by default during migration. Existing
records, deep links, comparison and notes remain readable under their original
semantics. Disabling a new feature must never delete its saved objects.

## Validation boundaries

Unit and contract tests supplement real PostgreSQL, isolated-runner, Playwright,
restart and cold-restore checks. Deterministic model/data fixtures are explicitly
separate from online provider and paid-model acceptance. There is no live trading,
broker connection, profitability claim or production performance promise.

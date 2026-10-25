# MineAgent source and compatibility provenance

MineAgent retains its initial commit
`799e28ca52cc156710485d574631537c52f0bd1d` and original MIT `LICENSE`.
The newly authored application platform was developed for the repository owner
in `idkwhodatis/TradeGrad`. Its migration baseline is remote commit
`6828f3bd429fc754648021bae703a14fa300b62e`, Git tree
`add06334d6cbcd9e5f359f2cb4a91f6902fed589`. The corresponding local development
commit was `54ec99ca1fe0e5a8cab40419faa89d992d23ffee`; these share identical trees,
not commit metadata.

The accepted baseline passed all four required jobs in
https://github.com/idkwhodatis/TradeGrad/actions/runs/40134103045.
That is evidence for the source baseline, **not acceptance of new MineAgent
changes**. Old reports that say pending describe their historical state.
Synthetic fixture acceptance does not establish live-provider availability,
production-model reliability, investment performance or production capacity.

## Scope of the MIT license

The root MIT license covers MineAgent's new owner-authored code. It does not
relicense third-party material. Existing adapted FastAPI template and
Procrastinate notices remain in `backend/NOTICE.md` and `backend/LICENSE.*`.
Frontend third-party notices and Lightweight Charts attribution remain in
`frontend/THIRD_PARTY_NOTICES.md`, `frontend/FASTAPI-TEMPLATE-LICENSE` and
`frontend/LIGHTWEIGHT-CHARTS-NOTICE` and their required visible presentation.

The original upstream TradeGrad baseline had no root license establishing
general redistribution rights. Being publicly readable is not a new MIT grant.
The 21 original source/runtime paths necessary for compatibility are therefore
**external dependencies**, not tracked MineAgent source. MineAgent does not
claim ownership or a new license over those paths or their upstream-derived
changes. `compatibility/tradegrad.json` identifies the exact public fork,
commit, tree, allowed paths, byte sizes, SHA-256 and Git blob hashes. The two
remaining original paths (`README.md`, `.gitignore`) are not imported.

## Reproducible dependency materialization

Before running the legacy-compatible app, tests or Docker builds:

```sh
python script/bootstrap_tradegrad_compat.py
```

This fetches the fixed public commit into a disposable Git object store,
validates its complete tree identity and every required blob, and materializes
only allowlisted Git-ignored dependency paths. It executes no dependency program
and asks for no credentials. It refuses tracked destinations, symlinks and
differing local files. Descriptor-relative no-follow operations are required;
use a Unix environment supporting these primitives (including Linux containers
or WSL), not an unsafe fallback on an unsupported platform.

Complete bytes are staged privately under the ignored
`.runtime/compatibility-bootstrap/` directory, then linked without replacing any
existing destination. Staging hardlinks are deliberately retained: pathname
cleanup cannot safely delete only our inode during arbitrary concurrent user
replacement. An identical repeat creates no new staging files. On failure,
already-created dependency files may remain; no destination is deleted as a
rollback. Root/leaf inode and content checks detect changes before success, but
cannot freeze a checkout against later edits by another same-user process.
Do not force-add the external or staging files to Git. Docker builds consume
the locally materialized bytes, with no external-path symlinks.

For an existing local clone with the same tree, use:

```sh
python script/bootstrap_tradegrad_compat.py --source-dir /path/to/verified/TradeGrad
```

Only immutable blobs at that clone's HEAD are read; working-tree modifications
are not accepted as dependency inputs. This mode intentionally accepts the
documented local commit with the same exact tree. Remote mode additionally
requires the exact remote commit. All V1 compatibility workflow jobs run this
bootstrap after checkout before installing/executing the legacy runtime.

An unavailable dependency fails closed rather than silently replacing the
historical optimizer, economics, seed code, schema identifiers or checkpoints.
Legacy TradeGrad checkpoint execution remains dependent on its original fixed
environment; OpenEvolve checkpoints must never be substituted for it.

## New engine dependencies

OpenEvolve, RD-Agent and Qlib have separate upstream distributions and licenses.
Their adapters must pin revisions/environments and retain applicable notices.
This document does not grant permission to copy those projects into MineAgent.
New engine acceptance, genuine calls and fixture calls are recorded separately.

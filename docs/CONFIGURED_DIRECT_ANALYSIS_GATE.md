# Configured protected direct-analysis gate (revision 022)

This gate is distinct from the historical fixed ABI and controller-only gates.
`mineagent-configured-analysis.yml` builds exact locked candidate/normalizer and
controller images, reproduces the pinned installed Qlib parent, and derives the
fixed analysis image without dependency installation or network during that
build. Image builds are not runtime admission evidence.

The one required case is
`test_actual_configured_source_snapshot_direct_analysis_generation` in
`tests/integration/test_rdagent_configured_analysis.py`. Its completed composition
requires the revision022 migration, configured controller fixture/callback, and
historical result reader; collecting the case is not execution acceptance.
Missing images, database, fixture implementation or any skipped required case
must fail the hosted gate, never silently reduce its inventory.

The real retained marketdata fixture has three listings and four dated sessions.
The declared output window is one session with two prior sessions available;
forward labels come from the separate retained next session. Actual protected
units must create the controller source, candidate HDF, normalizer snapshot and
Qlib direct result. Four exact persisted admissions must be stopped. Expected
Pearson IC is independently calculated as `0.9845785700149436`, RankIC is `1`,
and all three pairs and the predeclared threshold must be represented. No host
Qlib import, native file parser, live provider or paid model is involved.

Repeated historical reads must not create another admission. Copying the small
stopped storage tree to a new inode must preserve historical result usability,
reject another owner, and fail any attempt to issue from the terminal old fence
before Docker inspection. This storage-relocation check is **not** a PostgreSQL
backup/restore proof; the separate full cold-restore drill must cover the actual
restored database and normal historical API.

Live process result readers retain strict original root identity. Historical
readers return distinct typed data after terminal DB/receipt/settlement and file
closure checks; neither a cold manifest nor a historical result is launch
permission. Default production catalog/entrypoints remain closed until the
required exact-head composed acceptance has passed.

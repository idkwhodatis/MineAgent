# Historical reconstruction provenance and limits (c993367)

This file describes the published reconstruction baseline. The later harness
continuation and current measured test result are in README.md, CONTINUATION.md
and RESULTS.json; the original 56-test result is historical here.

Recovered on 2026-11-03 after the previous working directory, pinned checkout,
prototype ZIP and test logs were no longer available in the current filesystem.

## What was rebuilt

- The Python abstract model and its API were reconstructed from retained source
  text and the final design, including the two prior review corrections.
- The component tests were independently reconstructed from retained test bodies
  and finite domains. Their fresh hash differs from the original recorded hash.
- The synthetic global-gate SQL, opt-in two-session runner and source/adapter tests
  were reconstructed from retained text. They are still unexecuted against SQL.
- Documentation was rewritten for this recovery, retaining explicit assumptions
  and unresolved gates rather than presenting old reports as current validation.
- Every reported current test result comes from the reconstructed files.

Fresh review also improved the reconstructed runner rather than preserving two
old weaknesses: its evidence checks cannot disappear under `python -O`, and it
rejects ambient libpq settings or service/hostaddr routing. Four new offline
checks cover these changes. The published reconstruction total was 56, not the
earlier historical 52.

## What was not recovered or claimed

The original ZIP bytes, old manifests, old review reports and old test logs were
not recovered. Exact byte equality, equivalence of all lost work and continuity of
the previous artifact identity are not established. Matching test counts alone
cannot establish any of those things.

The original whole-repository dependency extractor, its 17-function inventory and
full writer/trigger catalogue are not included as reconstructed evidence. The
design carries their relevant limitations but does not claim a complete dependency
proof. No production migration or actual concurrency acceptance has been recovered
because none was demonstrated by this prototype.

The source baseline associated with the design is
`83895f4efe4fc9062e59a2af571b36348ed51248` in
[idkwhodatis/MineAgent](https://github.com/idkwhodatis/MineAgent/commit/83895f4efe4fc9062e59a2af571b36348ed51248).
Read-only repository inspection during reconstruction confirmed that commit and
the current master ref. The separate existing repair branch is not modified by
this work.

## Publication boundary

Suitable for a separate experimental branch as code and design evidence, with
all files under `experiments/validation_concurrency/`. It is not a production
rollout, a replacement for current validators or an assertion of throughput.
Publication must preserve that distinction.

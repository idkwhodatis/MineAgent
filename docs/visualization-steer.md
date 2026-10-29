# Reading research results and preparing checkpoint guidance

This UI displays retained evidence. A chart, a saved steer, and a submitted
model request are different states; none proves financial improvement or model
compliance.

## Follow an actual workflow

Open a workflow and select a recorded stage in the timeline. Its input revision,
definition commitment, exact outputs, errors, containment and request-consumption
receipts are read separately. The URL fragment preserves the selected historical
stage on reload. Selecting history never retargets the current-stage controls.
A start without a terminal receipt is not shown as verified running or success.

The installed workflow stage kinds do not include OpenEvolve. An independently
recorded OE run is not silently drawn as an RD→OE workflow transition. A failed
or cancelled child without verified containment/settlement remains blocked;
there is no implied safe fork or resume.

## Read RD factor diagnostics

After explicitly releasing an exact historical analysis, view daily IC/rank IC
and paired-observation counts. Undefined correlations remain gaps, with the
retained reason in the table. Select actual sessions by chart point or table
checkbox (up to 32). These are factor/forward-label correlations, not portfolio
returns, and pair counts are not a fabricated coverage percentage.

The exact factor-version page continues to show retained source and declared
metadata. Arbitrary Python does not imply an available symbolic formula.
Group-label spread is marked **not computed** until a production-bound, retained
result exists. No fake NAV curve is generated. Aggregate RD diagnostics do not
provide asset IDs, so this view does not invent asset selection.

## Inspect structured OE candidates

The candidate view groups versions by their retained parent relationship, not by
score. Missing parents, duplicate identities and cycles do not become valid
roots or authorized comparisons. Each result still needs its own explicit
release; only supported direct-parent/same-snapshot comparisons are offered.

A released structured result can provide observation context for its exact
candidate/program/factor/snapshot IDs and commitments. If it has actual fills,
select a fill to bind its retained listing ID, fill ID and session. No fills
means no inferred asset/session context. Structured results are distinct from
native Python-search checkpoints: this UI does not provide native source ZIP or
checkpoint downloads. Unexposed strategy source/parameters remain unavailable.

## Save, select, then inspect consumption

1. Choose **Prepare guidance** from the stage or released result. Review the
   bounded exact identity/hash/session context; write your own research direction.
2. Confirm review and save an immutable user steer version. The context is saved
   within its hashed text, not as a new structured graph-selection API.
3. In the desired workflow's future-stage input editor, refresh available manual
   versions, explicitly select the returned version, and save the selection using
   the current workflow revision. Manual inputs remain pinned by origin, not by
   inferred approval.
4. Use existing pause-after-stage controls when a safe checkpoint is needed.
   Changes only affect a subsequent eligible stage. Current and completed stages
   keep their frozen inputs. Inspect the later stage's actual consumption receipt
   to distinguish selected, prepared, submitted, uncertain and non-model use.

No graph operation automatically releases a result, starts an engine, resumes a
blocked child or applies saved guidance. Natural-language context is not an
enforced instrument/time constraint. Oversized context is rejected rather than
truncated. Account changes hide the previous observation/editor. An uncertain
save is not automatically resent; inspect persistent steer history first.

## Acceptance boundary

The unit/SSR and type/build checks are separate from real browser acceptance.
Existing real PostgreSQL/API RD, OE and workflow Playwright cases cover the new
views and observation preparation; the workflow case additionally checks an
explicit save and immutable historical state. Real Caddy tests cover mount-aware
workflow fragment deep links and absent-history behavior. These cases require
their real fixture stacks; collection or mocked tests do not certify execution.
Full observation→safe-checkpoint→subsequent-provider consumption remains governed
by the existing workflow receipt/dispatcher acceptance, not a UI-only assertion.

### Remaining must-pass connected acceptance

The new observation-save browser case does **not** yet prove that a newly saved
observation reaches a subsequent engine request. Before claiming the four-part
flow end-to-end, extend the real provider workflow fixture (not a fabricated
consumption row) to:

1. Pause at an actual completed-stage boundary; retain the prior stage's input
   revision, definition hash and output hashes.
2. Select a real released chart observation, explicitly review and save its
   immutable steer version through the UI.
3. Refresh available manual inputs, select that exact version, and save the
   future-stage selection with the current revision. Assert an obsolete revision
   is rejected and leaves the selection unchanged.
4. Resume the existing permitted checkpoint and run the actual dispatcher and
   offline provider double through their normal request path.
5. Assert the next stage's provider request and consumption audit contain the
   exact saved version, text hash and observation context. Assert the completed
   stage and its outputs remain unchanged; saving alone must not cause any of
   these actions.

The current retained-history fixture is intentionally read-only with respect to
execution and may be blocked at feedback release. Do not bypass that gate or
insert audit records to make this scenario pass. This connected scenario remains
unverified until a suitable real execution fixture is run successfully.

The follow-on `observation-checkpoint.spec.ts` is wired into the existing real
opt-in two-child workflow fixture. It explicitly releases the first child's
analysis, saves and selects graph context, tests stale-revision rejection and
resumes the safe checkpoint; the original dispatcher/queue/provider path then
checks the next request and actual consumption record. No consumption records
are inserted by the test. This new scenario has not run successfully yet.
Offline prompt-size tests exposed a fixed-message-capacity issue in the seeded,
multi-source workflow. New admissions now explicitly use the versioned
`generation_user.v1` layout: trusted rules and binding commitments remain in the
system message, while complete manual and untrusted generated evidence is
partitioned in the existing user message. Both original 8192-character message
limits and total request/token/budget guards remain unchanged; oversized complete
inputs are rejected, never truncated. Token estimation and actual sending use
the same generation-only SDK message builder. Other evaluation phases and the
original task/knowledge identity are unchanged. Retained configurations with no
layout field continue to use their byte-exact legacy renderer; no existing run is
rewritten. Local actual-SDK normal, JSON-repair and continuation checks are not
substitutes for the still-required real container/SQL/browser scenario.

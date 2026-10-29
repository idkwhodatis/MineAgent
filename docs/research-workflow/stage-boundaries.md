# Between-stage workflow successor — isolated implementation draft

**Not registered or activated.** Current 026 and historical 025 keep their original
contracts. This is not evidence that a workflow, model call, or native unit ran.
The next migration number and assembled runtime are not assigned by this draft.

## Minimal graph

- `research_workflows`: owner, revision CAS, continuous / pause-after-stage mode,
  current immutable input revision, current actual stage and latest checkpoint.
  An immutable finite budget ceiling limits total declared child allocations;
  this is an admission ceiling, not a duplicate spending ledger or refund.
- `research_workflow_revisions`: ordered immutable `WorkflowInputSnapshot` with
  the single shared `InputVersionRef` vocabulary. Manual references point to
  existing steer versions; external and experiment feedback references point to
  their separately owned, provenance-checked versions. Unknown provenance cannot
  silently become an eligible new selection.
- `research_workflow_stages`: one actual attempt per ordinal. Exact owned input
  revision and definition commitments. Engine stages require a genuine child run;
  deterministic external normalization and feedback projection each have their own
  receipt, not a fake model run. Feedback projection separately references the
  actual source engine stage/run/output and never inherits its success status.
- `research_workflow_checkpoints`: immutable terminal graph references. A saved
  checkpoint ID is never a resume capability; its real stop and settlement graph
  must be revalidated. A completed output is not rewritten by an edited input.
- `research_workflow_model_consumption`: exact actual model-operation scoped FK,
  input revision/hash and request hash; populated only by the genuine submitted
  completed-provider request adapter, never inferred from selection or a successful
  child status. Pending/unknown submission is derived from its real transport,
  not rewritten into a completed consumption row.
- `research_workflow_commands`: exact bounded request/response commitments and
  unique owner/workflow/idempotency key. Replay checks original bytes before CAS.

The stage composite key `(owner_id, workflow_id, id)` is the FK target for the
input team's normalization receipts. A batch may produce ordered independent
input records (up to the normalizer's explicit item limit), not overwrite one
record repeatedly. Formatting success does not imply trust, approval, selection,
prompt inclusion, or a successful research outcome.

## Actual child boundary required before activation

Existing 026 config-v2 has only its manual steer snapshot. It cannot truthfully
represent the new combined external/feedback selection. A successor child
submission/bootstrap must include `StageExecutionBinding` **before** any work:
owner, workflow, stage, input revision/hash, definition hash. The submitted model
request must be reconstructed from those exact immutable versions and linked by
actual operation ID/request SHA. Merely finding an old same-owner run, or adding
post-hoc metadata to its result, is forbidden.

Three distinct read states remain separate:

1. selected/frozen inputs, not yet submitted;
2. uncertain submission shown separately from exact completed-provider-request
   inclusion evidence (operation, transport and request SHA);
3. actual retained completed outcome, verified independently.

Numeric-only stages do not consume natural-language guidance. They must report
`not_consumed_numeric`, leaving guidance for the next genuine model stage.
Prompt inclusion never proves that a model obeyed a hypothesis or constraint.

## Controls and native limits

Continuous mode is default, but may advance only through existing authorization,
release and development-eligibility gates. Sealed/final/independent results do not
become search feedback. Missing disclosure authorization produces a visible
blocked reason, not an automatic GET release or fabricated consumption record.

`pause_after_stage` while running records a pending request. The already-running
stage keeps its original input and original deadline. Cancellation similarly
requests stop; it is not a stop receipt. The successor coordinator must use the
existing owned child control path, then verify every admitted process (including
provider SDK groups) has positive stop evidence and exact required settlement.
Unknown physical identity or unsettled conservative holds block checkpoint/resume.

A safe checkpoint ends the old unit. Resume creates a **new** stage and a **new**
child run under its own explicit budget and immutable input binding. It does not
extend a 60-second deadline, revive a native checkpoint, refund unknown usage, or
replay old launch receipts. Current control code intentionally rejects resume
following an actual stage until that genuine adapter is installed. Initial pause
and resume before any stage may change ready state without inventing stop proof.

## Required acceptance, still pending

- Real PostgreSQL concurrent edit/start and pause/completion races: one CAS winner,
  immutable old input preserved, exact idempotent retry and foreign-owner denial.
- Real child submission matches frozen workflow/input/request/model binding;
  another same-owner old run cannot be attached as a replacement.
- Actual stop and full settlement before safe checkpoint; missing/late/unknown
  evidence never permits next-stage authority or a refund.
- Actual model request includes selected version text/hash; numerical stages do
  not claim textual application. Later edits/disable leave prior stage unchanged.
- Continuous and paused/resumed user flows use real outputs and existing release
  receipts, with no additional process admission on read/replay.
- Backup/restore preserves immutable versions, commands, stages and terminal
  references, and revalidates eligibility/stop/settlement rather than a marker.

Current unit tests exercise transaction decisions with explicit doubles only.
They do not establish SQL race correctness, worker dispatch, or runtime acceptance.


## First finite plan and child interface

`RDGuidancePlan` (`packages/research_workflow/plan.py`) fixes the initial profile:
optional deterministic normalization, then 1–8 **new** one-generation RD children,
with eligible direct-analysis feedback projection between children. It does not
include implicit OE search or require structured execution to close the RD loop.
The template has an explicit operator model binding and empty legacy manual
snapshot; all selected guidance instead enters `ModelStageInputSnapshot` exactly
once. Unknown pricing cannot fund the plan, and its ceiling must cover every
child's full declared budget bound. Additional local input publication remains
subject to its real quota and workflow allocation checks, not an assumed refund.

The successor RD adapter owns `submit_workflow_child(session, owner_id,
workflow_id, stage_id, settings)`. It reads the actual locked graph itself; callers
cannot supply an authority config or attach a previous run. Stage and new child
are inserted atomically, with the child FK deferred only until commit. A committed
stage without its genuine child is invalid. The new run's idempotency key and
versioned config/bootstrap retain exact stage, input revision/hash and definition
commitments. `submission_origin=workflow_stage` never turns external/feedback
inputs into human-authored input: each reference retains its own origin.

The current RD owner has accepted this successor interface, but current 026
configuration, receipt and prompt semantics remain unchanged. No worker dispatch
or safe-checkpoint adapter is claimed by this declaration freeze.

Local verification: 24 finite-plan, combined-input and control decision tests
passed. These include stale revision rejection, exact command replay, pending
pause/cancel semantics, mixed-origin preservation and swapped/partial input
rejection. PostgreSQL races and real child execution remain required gates.

### Normal owner admission (uninstalled successor)

`research_workflow_admission.create_workflow` retains the exact finite request,
original input revision and creation receipt under an owner-scoped idempotency
key. Retry returns that original receipt, not a fabricated current status. An
optional first normalization stage binds an actual owned raw input object and
immutable definition; invalid JSON remains raw input for a real rejected receipt.
Without normalization, creation remains explicitly blocked until the new child
adapter is installed. No old run is attached and no provider is invoked here.

`select_workflow_inputs` resolves active/current versions for a new selection,
then appends a revision under owner-first locking and root revision CAS. It never
changes the existing stage's input FK, even before that stage's local work starts.
Exact request retries precede CAS and do not re-resolve today's active head.
Declared normalization storage must fit in the plan ceiling in addition to all
child budget bounds; actual physical publication still uses the existing quota
admission. This metadata allocation is not a settlement or checkpoint.

These services are flush-only and remain capability-closed. Their transaction
unit tests do not substitute for installed PostgreSQL races or a real child flow.

### Development feedback release choice

The initial plan includes strict Boolean
`allow_workflow_development_feedback_release`, default **false**, committed by the
plan/request hash. This is only an in-app choice for that workflow's genuinely
bound eligible development outputs through the existing release ledger. It is
not global, retroactive, or a grant for final/independent/unknown outputs. Without
this explicit choice and an existing qualified release, the feedback edge waits
for release. Continuous mode, a selected input, or manual pinning never supplies
that permission. Merely implementing this option does not authorize us to
release actual outputs during development or testing.

Normalization's **incremental** publication bound is 262144 bytes of canonical
aggregate outputs (including their metadata) plus at most one 32768-byte atomic
temporary object: **294912 bytes**, at most 32 retained files plus one temp.
Individual payload sizes sum to no more than the bounded aggregate array; its
brackets/separators make the aggregate slightly larger. Raw input up to 65536
bytes was separately admitted/retained before workflow creation and is already
in baseline physical quota, so it is not added again. SQL metadata rows do not
create additional object files. Dedup may reduce actual new bytes, but this is a
conservative allocation, not an observed peak or a basis for refunding units.

### Genuine local publication checkpoint

`checkpoint_external_stage` re-enters the existing publication/quota guard with
zero new file demand, takes owner/root CAS, and revalidates the exact terminal
normalization event, raw/output bytes and complete registered physical inventory.
It records the immutable graph and original allocation commitment, not a caller
`settled` flag. Raw bytes remain separately counted; no child, process stop or
model-cost receipt is invented. A duplicate observation rereads the original
facts and returns the same checkpoint without changing a newer root revision.

Only that local stage closes. Pending cancellation/pause is reflected after its
real terminal boundary; continuous success remains explicitly blocked on the
new child adapter. Outputs are not automatically selected, released or passed to
a model. Engine-stage resume remains unavailable until its separate actual
submission/terminal graph adapter is installed.

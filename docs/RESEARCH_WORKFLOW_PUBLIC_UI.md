# Finite research workflow entry and audit

Integrated against the actual registered owner routers and DTOs at backend
candidate `d4499ee`. The main OpenAPI SDK and workflow DTO projection are
regenerated from that backend, not copied from the older UI branch. Installed
capabilities and request-specific preflight remain authoritative. Registration
is not evidence of successful PostgreSQL, worker, container, or browser acceptance.
The existing normal 027 search, result release, comparison, cancellation and demo
onboarding routes remain present.

## Implemented boundary

- Authenticated bounded streamed JSON wrappers under `/research-workflows`.
  Callers cannot supply internal plans, owner IDs, runtime images, or operator
  pins. Public preflight/create hold the shared lifecycle guard and acknowledge
  a write only after one metadata transaction commits.
- Current selected input revision is read independently from frozen stage inputs.
  Manual options expose the verified full immutable version commitment, not the
  text-only hash, without changing the sealed steer/config serialization.
- The entry component reuses the normal configured RD declaration form in a
  declaration-only mode: no fresh RD run, no existing RD retry envelope, one
  generation per child, no hidden embedded steer selection. Workflow guidance is
  selected separately in explicit order. Display pinning never reorders it.
- Manual inputs are shown first; normalized external and internal feedback
  proposals keep their exact source refs. Raw input uses the existing explicit
  retained-byte intake, literal JSON mapping, and opt-in content viewer.
- Continuous/pause-after-stage mode, finite iteration ceiling, explicit budget
  ceiling, and immutable **default-off** feedback-release option. Feedback scope
  remains only this workflow's eligible owned development outputs through the
  existing ledger; it is not global, retroactive, or a sealed-output release.
- Original creation key/body survives reload and uncertain replies. Exact owner
  is rechecked before sending. Creation receipt is not current status or a
  queued-child acknowledgement. Commands likewise retain exact key/revision;
  only explicit `workflow_revision_conflict` permits an acknowledged discard and
  refresh, never automatic reapplication.
- Actual history separates selection, frozen stage inputs, source receipt state,
  provider-response/request evidence, containment, settlement, and checkpoint.
  No witness means no inferred submission or compliance. Current backend controls
  alone decide whether pause/cancel/resume buttons exist.

The private `/research-workflows` and `/$workflowId` frontend pages now provide
normal navigation and owner-keyed history. Missing backend routes return an
explicit unavailable state. The capability wrapper exposes only
`can_read` / `preflight_available` for the exact installed schema, never execution.
The saved-future-selection editor uses actual current manual versions and actual
stage-derived proposal refs, preserving order and explicit CAS application.
Real browser reload/Back acceptance remains pending. The current normal
RD preflight prerequisite does not itself grant workflow execution. A saved
manual edit is not an applied future selection and cannot rewrite a running
stage.

## Bounded historical stage navigation

The workflow detail now lists owner-scoped stage metadata in pages of 32. A
selection loads the existing exact per-stage reader; the list itself does not
claim completion, disclosure eligibility, model consumption, or execution
permission. Earlier stage inputs/results remain inspectable as the current
stage advances. “Follow current stage” explicitly returns to the current stage.

Historical selection never retargets steering controls: those retain the current
stage and its immutable consumed revision, while future-input selection remains
separate. Refresh updates both the selected historical read and current controls;
query caches include the owner/workflow/stage identity. Previous/next page
controls are bounded and do not mutate the workflow.

Actual multi-stage browser/PG acceptance is still required on the final integrated head.

Automatic feedback is shown separately from manual guidance, with the bound
predecessor checkpoint. It is never described as user-selected or user approval.
An explicit View/download uses the dedicated feedback-version content endpoint,
not the generic normalized-output reader. The client verifies the exact SHA,
32KiB bound and current session, preserves root/subpath routing, and starts with
no content loaded. Backend companion `4e9ddb1` revalidates the existing source and
release ledger; viewing never creates a release. Current focused UI/content
contracts: 29 passed with full strict types. Actual 028 browser acceptance remains
pending final activation.

Engine-run inputs retain a separate workflow-stage receipt origin: a workflow
selection is not a new user submission and its receipt is not pinned as human
input. Within its frozen snapshot, explicit manual guidance is displayed first
and pinned, followed by normalized external evidence, derived experiment
feedback and unknown provenance. Original frozen positions remain visible;
presentation never rewrites the engine input order, revision or commitment.
Text is escaped, and neither display nor pinning establishes model consumption,
compliance, approval or release. Refresh preserves this grouping. The public
projection requires companions `f2b63d4` and `3f7c34e` (fresh retained source,
release and restore-state checks), not merely cached input metadata.

Full strict types and 37 focused UI/content contracts passed for this companion;
actual coherent 028 browser acceptance is still pending. Generated SDK changes
in this development branch must be regenerated on the final integrated schema,
not copied over a newer 027/028 contract wholesale.

Stage history also exposes each retained output identity and exact guarded
analysis/structured-execution link. It does not fetch result bodies or release
anything on navigation; the destination retains its independent source/release
checks. This closes the previous metadata-only output visibility gap.

The 028 actual-fixture browser companion runs synchronously while each real
PostgreSQL/retained-data fixture is alive, via the same production app and built
frontend. It consumes observed stage/child identities, navigates historical
stages and outputs, checks explicit content viewing and frozen manual pinning,
reloads/Back, tests other-owner rejection, and never creates runs/releases.
The enclosing fixture compares run/model-operation/exposure rows afterward.
Local strict types and Playwright collection pass (one case invoked by each of
two real fixture variants); this is **not** an executed browser/PG acceptance.
Final integration must require `MINEAGENT_RD_WORKFLOW_BROWSER_REQUIRED=1` and
retain the two fixture nodes plus their existing physical execution budgets.

Failed/cancelled child status is an explicit blocked diagnostic, not protected
process-stop or settlement evidence. The bounded tick verifies the immutable
current child receipt and updates the root through its revision CAS; repeated
observations are read-only. Existing pause/cancel requests remain unchanged.
Without a verified safe checkpoint, failure continuation, retry, feedback release,
and the next child remain closed. Terminal-unverified waits have lower selection
priority than actionable work (bounded best effort, not strong fairness).

The same two required actual workflow fixtures now create their single workflow
through the authenticated production entry form, including real declaration
preflight, manual selection, mode/finite limits, and optional raw intake. The
creation browser captures the submitted envelope and receipt; the fixture
independently parses the actual typed request, checks retained root/stage bindings,
and replays that exact envelope. It does not create an additional child or use an
HTTP-only fallback. Creation and read-only history are separately named browser
cases, each selected and required exactly once by its helper. Both cases are
collected locally; actual coherent PostgreSQL/browser execution remains pending.

## Controller-profile successor inheritance (local candidate)

This successor preserves the sealed visualization implementation while placing
structured search at migration `028_oe_structured_search` and guided workflow at
`029_research_workflow`. Earlier numbered validation notes above describe the
original unpublished slices, not acceptance of this successor. Published
migrations 001–026 and the separate `027_rd_controller_v3` are unchanged.
Research/source restore tuples are exact: controller 11/6, structured search
12/7, and workflow 13/8. They are not interchangeable historical versions.

A workflow plan freezes the server-selected `controller_profile` with its full
research template. Missing retains the original 60-second controller; explicit
`mineagent.rdagent-orchestrator.v3` selects the approved 120-second composed
controller. A later change to the server default neither upgrades nor downgrades
an accepted plan's next child. Current enablement, executable availability,
provider credentials and transport permissions are still checked; changed image
or model pins still fail the immutable-plan comparison. The field is not accepted
as public request authority. Child deadlines and all other resource/call/token
limits remain unchanged.

The normal workflow acceptance fixture explicitly selects the composed profile,
checks each real child's persisted admission, launch and reservation, then changes
the current default back to legacy before allocating the next child. It must
prove the second child still uses the frozen selection alongside the observation
→ saved guidance version → safe checkpoint → actual provider-consumption checks.
These container/browser assertions require hosted execution; local unit, SQL and
browser collection results do not establish that complete runtime acceptance.

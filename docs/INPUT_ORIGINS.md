# Input origin and pinned user declarations

The input/history view groups **user-provided submission**, **agent-generated
proposal**, and **origin not established**, in that order. These are provenance
labels, not supervised/unsupervised machine-learning categories. Displaying a
proposal never implies human approval, adoption, or execution authorization.

User submissions are pinned first and expanded by a deterministic display rule.
The pin is rebuilt from the immutable receipt after reload; it does not mutate
request hashes, runtime policy, receipts, or approval state. No extra database
migration, local input copy, or new retention object is needed. Within each group,
creation time and immutable identity provide stable ordering. The user group is
retained above every page of proposals.

## Persisted mappings

* 023: `factor_research_submissions.request_canonical/request_sha256`, validated
  against the exact owner/run/analysis plan/preflight/profile through the existing
  receipt reader. The normal production writer is reached through authenticated
  `POST /engine-runs`. This means recorded submitted declarations, **not** proof
  that each field was manually authored, nor approval of later model output.
* Agent: exact `factor_generation_sources` plus source-read witness, assembly
  part, model operation, transport and completed response bindings in the same
  owner/run/attempt/generation. The DTO exposes IDs and commitments, not raw model
  messages, provider credentials/headers, or unreleased results. Missing historical
  witness remains unknown; contradictory provenance fails closed.
* Legacy or 026 draft: ownership alone never assigns user origin. Until 026's
  normal authenticated receipt route and dedicated binding validator are composed,
  its declarations remain unestablished in this projection. 027 must likewise
  separate its immutable authenticated request from generated candidate/model
  response provenance. Pin preferences must never enter execution hashes.

`GET /engine-runs/{run_id}/inputs` is owner-checked and bounded to 25 source
identities per page plus one original declaration. It does not read physical
result bytes, create an exposure receipt, approve a proposal, or start a process.
Existing canonical receipt/source backup closures retain the provenance. No new
claim of cold-restore acceptance is made by these portable tests.

The existing genuine queued-submission browser case now checks exact retained
input/hash, pinning and reload/Back. The existing completed-result case checks
actual model provenance grouping and another owner's denial. These assertions
remain pending execution in the composed hosted gate; no additional units or
mock routes are introduced.

## Deferred idea: TrendRadar

A future integration could present TrendRadar-derived topics as external,
unverified research-direction suggestions for RD-Agent, separately from pinned
user instructions. This is roadmap only: no fetching, connector integration,
automatic submission, validated-factor claim, or provider expenditure is enabled.

# Execution snapshot metadata navigation

This successor to `5de12f3` adds a dedicated owner/run-scoped metadata endpoint and
UI page for revision021 execution snapshots. It never aliases those IDs to
legacy feature snapshots, parses retained files, re-executes a factor, or grants
process authority. Publication-pending and recorded-completed states are distinct.
The projection validates canonical manifest commitments, exact owner/run/attempt/
generation/source/evaluation/version identity, and completed input/output hashes.

The public DTO excludes storage addresses, value rows, analysis bodies and metrics.
Its qualification is `persisted_metadata_not_reexecuted_or_reverified`;
`values_available` and `analysis_results_available` remain false. Recorded row
counts are labelled as saved metadata, not evidence that bytes are present now.
The native storage owner's shared-guard historical reader and applicable release
checks must be integrated before showing retained values or analysis results.
No new restore marker, inode rewrite or live-launch verifier is introduced.

The actual CoSTEER complete-snapshot integration gate now reads the dedicated
metadata endpoint over authenticated HTTP and checks exact hashes, reload and
other-owner 404. Pure tests cover identity/hash substitution, absent completion,
owner-before-query ordering and exclusion of result/storage contents. The ordinary
real persisted-source browser gate exercises the metadata route's missing-object
error and return path; it does not claim a successful completed-snapshot browser
read. Actual completed-snapshot browser coverage requires that runtime fixture.

User-run creation remains disabled. Future source preflight must use the retained
dataset and backend-defined objective/plan, explicitly acknowledging declared
availability/fixed membership (not PIT certification). Runtime profiles are not
chosen by the browser. These next contracts are owned by the RD backend worker.

Actual implementation began 2026-10-26 around 14:57 UTC, separate from requested
local commit metadata of 02:00 EDT.

Local validation: full backend 1893 passed (3 existing warnings); frontend 1063
passed, TypeScript and production build passed. Focused metadata projection tests
14 passed. Real complete-snapshot HTTP and browser tests were authored/extended,
but actual Docker/PostgreSQL/Chromium execution remains hosted-only and unrun here.

# Persistent user steer inputs

Local implementation, pending coherent 026 PostgreSQL, provider and browser acceptance.

`/rd-steers` is an authenticated management page. Its owner-scoped API supports
list/detail/history, creating a steer, appending an immutable version with an
expected head revision, and changing availability for future selection. The
capability remains closed unless the installed exact schema advertises
`mineagent.rd-steer-closure.v1`. Schema availability is not provider execution
availability.

Directions, hypotheses and natural-language constraints are user guidance.
Recording them or passing them to a prompt does not prove model compliance;
free-text constraints are not platform-enforced rules. User inputs stay visually
pinned ahead of generated proposals, without implying approval.

Only a NEW submission resolves explicit, ordered active/current version IDs.
An accepted run freezes the entire owner-bound version/text/hash snapshot. Later
edits or disable operations do not invalidate it. Worker, replay and cold restore
verify immutable versions, not the current head or enabled flag. A run must be
cancelled separately. The editor retains its original expected revision across
background refreshes, so stale edits cannot silently overwrite a newer head.

The reusable `SteerVersionSelection` preserves explicit order and missing/stale
selected IDs until the user removes them; it never upgrades an old selection to
a new head. It is not connected to old fixture submission schemas. The genuine
provider request union and prompt consumption are owned by the 026 integration;
only that form may send `selected_steer_version_ids`.

CRUD writes commit before returning. A network error can leave the outcome
uncertain: inspect refreshed immutable history before repeating a create. Editing
uses expected revisions; a stale conflict requires reopening the editor.

Local evidence: 14 immutable-core tests, 8 HTTP parsing/auth/commit tests with
transaction doubles, 5 component/ordering/root-and-SUBPATH tests, generated
OpenAPI/client and standalone strict page typing. These are not actual SQL or
Chromium acceptance. Coherent 026 still needs Unicode/escape INSERT checks,
owner isolation, edit/disable-after-queue prompt/worker continuity, cold restore,
and genuine create/edit/history/selection browser coverage. TrendRadar remains a
future direction-source idea; no feed integration or external fetching exists.

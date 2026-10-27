# Retained engine results and configured preflight

Local successor to frozen `be8322e`, composed on revision022. Actual implementation
and local checks began 2026-10-26 15:43 UTC; commit metadata follows the requested
Toronto outside-work-hours convention. This document is not hosted acceptance.

## Revision022 user paths

- Engine history links to `/engine-runs/preflight`. The form lists actual owned
  retained datasets, explicit declared-source acknowledgement, task, output
  window and predeclared objective. The server supplies fixed profile/budget and
  authoritative eligibility/reason codes. It creates no run/plan; creation remains
  visibly disabled until the separate023 durable submission/worker cut is accepted.
- Independent Factor Library index/version pages distinguish execution evidence
  from legacy snapshots, with exact typed links to snapshot and analysis pages.
- Snapshot metadata withholds all data-derived counts. Values and analysis require
  explicit release; GET never silently creates a missing receipt. The page explains
  full factor/future-label extent exposure before the user clicks release.
- Release privately verifies terminal immutable historical data under native
  storage guards, then separately rechecks DB bindings and commits existing
  `DataUsageExposure` records under owner-first locking. Only then may verified
  detached values leave the process. Ledger owner/run/experiment/split bindings
  are checked in addition to existing semantic replay checks. No new ledger,
  restore marker, live execution grant, or legacy snapshot coercion is introduced.
- Snapshot values are paginated; authenticated dedicated JSON export is bounded
  to32MiB. Analysis displays actual DirectResult/measurement, not a fake score.
  Reload repeats current retained-byte verification and exact receipt checks.

## Required gates, not locally claimed

The existing full-app source browser now also checks real retained-data preflight,
oversized-workload rejection and reload invalidation. Existing Caddy acceptance
covers the new form's prefixed navigation/deep reload without replacing old cases.

The configured-analysis workflow builds the locked frontend and matched Chromium.
Its first existing actual four-unit case invokes the genuine result browser AFTER
storage relocation and BEFORE any release replay helper. The single exact browser
case must execute, never skip: Factor Library navigation, missing-release409,
explicit release, actual values/export, independently expected IC/rankIC, reload,
Back and foreign-owner404. Follow-up HTTP checks validate stable receipt replay
and full factor/future-label exposure classification. The original terminal
no-launch assertions and second cold pg_dump/restore case remain unchanged.

Local portable/unit tests use inert persistence or HTTP interception only where
explicitly stated. They do not prove PostgreSQL, Docker, Caddy or Chromium runtime
acceptance. Genuine completed result browser acceptance remains hosted-only here.

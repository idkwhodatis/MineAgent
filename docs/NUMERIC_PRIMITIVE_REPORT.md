# Bounded normalized primitive report successor

This successor does not activate an application entrypoint or certify a complete
RD evaluation. It requires the explicit revision021 execution snapshot, research
v6 and native v2 capabilities. Revision019 and older closures reject the new
input/report evidence rather than silently widening their historical meanings.

## Actual path

The report-required normalizer input is `mineagent.factor-normalization.v2`.
Legacy v1 remains unchanged. The isolated trusted normalizer creates the same
fixed-schema `values.parquet` plus canonical `rows.json` from the exact validated
primitive rows. The report binds SHA256 and byte size of the Parquet, SHA256 of
the full input manifest and listings, and every row. It contains no native object
or executable value. There are at most10,000 rows and the combined output is at
most16MiB. Rows cover exactly the declared sorted unique listing codes, use the
one exact output event, and carry either a finite float/state0 or null/state1.
Booleans, integer-valued float substitutes, duplicate/missing/extra/reordered
listings, nonfinite values and context drift are rejected.

After descendant quiescence, the existing protected exporter sends explicit
MANU2 kind4 with `numeric-quiescence.v3` metadata binding both outputs. Legacy
kind2 metadata and bytes remain unchanged. A report-required admission rejects
legacy-only output, and a legacy admission rejects unsolicited kind4. The host
supervisor validates bounded JSON only; it does not parse HDF or Parquet. It
persists both owned0600 files and extraction.v3 with the existing full launch,
identity, input and timely whole-unit-stop binding. Overdue containment is never
successful report authority.

`read_normalized_cutoff(session, fence, normalizer_admission_id, settings)` needs
a fresh `factor_numeric` transaction and retained lifecycle/native storage guard.
It re-resolves the actual source/evaluation/cutoff/native-publication/unique
normalizer claim, actual protected journals, Parquet and report. The returned
frozen data is not a reusable grant; a publisher must call this resolver again
under its own retained scope before finalization. Completed process accounting
may remain held or be exactly full-charge conservatively settled; prelaunch
claims still require held reservations.

The new DDL rendering helper replaces only the normalizer inventory predicate in
an owning successor migration. It accepts exactly v1 or v2 reconstructed full
inventories, preserving every prior owner/fence/source/image/input/reservation
check. It never alters the frozen019 migration. Numeric backup permits report
files only with explicit `allow_reports=True`; the owning021 closure must also
validate the canonical report and exact reconstructed inputs after independently
checking each actual file digest. Legacy closure opt-in defaults false.

## Acceptance status and scope

The maintained composed test now requires the existing independently established
Parquet digest and the three independently calculated momentum rows, then rereads
the cached completed report in a fresh transaction without executing again.
The source-generation precursor remains storage-only in that test. A single
cutoff is not a full multi-cutoff evaluation. A separately frozen single-day
output window can truthfully have one required cutoff while retaining two prior
input sessions; full-window scheduling/completion belongs to the RD protocol.

Local structural/parser/protected-file tests are not actual Docker/PG acceptance.
Actual composed PG+Docker execution and successor cold restore remain required;
this source change installs no production catalog, provider, deployment or launch
route. Pinned source inventory changes require newly built/measured normalizer
images; an older accepted image does not inherit report acceptance.

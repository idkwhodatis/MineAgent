# Declared upload research profile

This is a bounded, explicitly unverified data path, not a market-data provider or
point-in-time certification. Two owned, retained schema2 CSV uploads may be
composed only when both declare XNYS/USD daily raw OHLCV, shares volume, and no
corporate actions throughout the complete common interval (including warmup).
Every listing must contain the same complete retained session sequence. Missing
rows, unknown units, adjustments, duplicate listings, contradictory action
columns, and partial overlapping ranges are rejected. Nothing is filled in.

The dataset form requires an unchecked acknowledgement. Composition creates an
immutable schema3 dataset, not a run or result release. Original dataset/listing
identities, CSVs, mappings, declaration bytes and hashes are retained. No actions
is a user's declaration, not independently established fact. Availability at
session close and fixed membership remain unverified research assumptions.

Select the resulting dataset in the existing RD form. The existing explicit
source acknowledgement, model configuration, budget and output release controls
remain required. Normal RD output may feed the existing structured OE path only
after explicit release; its fixed-inputs v2 qualification is
`user_declaration_unverified`, never synthetic. Workflow-v3-to-OE orchestration
is not introduced by this profile.

A saved composition key is reused after uncertain responses. A database rollback
can leave immutable orphan files: retries fail closed rather than adopting or
deleting them. At revision030 all factor publication admissions conservatively
count every file below root `datasets`, including original uploads and orphan
bytes, in the existing factor capacity pool. The separate data ingestion quota
and admission are unchanged; this does not claim all ingestion writers acquire
the factor publisher lock. Lower available capacity can therefore block a new
composition or factor publication without removing existing evidence.

Revision030 adds append-only two-source lineage and restore closure v1, research
closure14/source9. Older schema/closure versions do not accept this profile.
Recovery recomputes the basket from retained original CSVs and compares physical
and relationship evidence; hashes alone are not a semantic validation shortcut.

Local CSV/parser, SQL single-user matrices and browser collection are separate
from the required hosted HTTP upload/data-worker -> RD -> release -> structured
OE/browser chain. The chain must actually pass before claiming end-to-end
acceptance. No external data, provider credentials or paid API calls are enabled.

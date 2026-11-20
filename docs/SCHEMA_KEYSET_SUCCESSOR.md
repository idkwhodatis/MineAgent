# PostgreSQL key-set successor experiment

`032g_schema_keyset_cost` is an additive, unpromoted experiment based on published
`860cd536b19098dfc5b3abdfdb7b481c55b1138e`. This source was reconstructed and must
be reviewed and tested as a new tree. It is not the lost earlier candidate.
Parquet, transport experiments and row-CHECK adoption are not included.

## Exact scope

The only SQL predicate replacement is the existing isolated experiment in
`script/schema_keyset_candidate.py`: compare canonical `jsonb_object_keys`
sequences directly using `ARRAY`, replacing two sorted `array_agg` expressions in
`mineagent_rd_schema`. Equal key sets have the same canonical JSONB key sequence.
No values are copied into another JSON container. Empty/absent properties,
malformed rules, SQL NULL, recursive fallback and exception boundaries retain the
predecessor semantics. This is not a new generic JSON Schema implementation.

The frozen recursive helper and `mineagent_structured_json_value` are unchanged.
The three-function dependency closure is pinned by full predecessor source and
exact catalog definitions/attributes/defaults. The observed CI catalog export is
not an allowlist: its role names and allocation-dependent RI names are irrelevant.
All 45 predecessor migration files remain unchanged. Downgrade installs the exact
032f main function body, preserving function identity rather than dropping it.

The migration retains all 187 maintenance locks, their NOWAIT behavior, original
advisory key, and READ COMMITTED requirement. A safe catalog-first path is used
for DDL, then restored. Temporary declaration-type shadows are refused. The
production contract remains PostgreSQL18 with its deterministic default
collation; no CI patch-version, UTF8, schema-owner or CI-owner restriction is
introduced. Only the replaced main function requires usable ownership. Unchanged
helpers can be owned by unrelated roles. Their bodies, attributes, OIDs, owners
and ACLs are checked before and after replacement.

Every accepted-head consumer explicitly adds the new revision while retaining
historical revisions. Capabilities and normalizer mappings are unchanged from
032f. Backup receipt/OpenAPI/client enums include the exact successor. Historical
isolated SQL fixtures explicitly select/assert032f before admitting historical
rows. Terminal repair dependencies stop at032f. Diagnostics select the revision
first and retain an exact58-function closure; only the main schema hash changes.
The workflow persistence proof retains031/032/032f gates before testing the same
actual graph at032g, read parity and a third independent cold restore.

## Required evidence

`tests/structured/schema_keyset_successor_inventory.json` lists every named source
and successor PostgreSQL case. `script/schema_keyset_successor_proof.py` verifies
both collection and JUnit identities and rejects missing, duplicate, failed,
errored or skipped cases. Evidence exports exclude captured stdout and private
assertion text. The configured Qlib workflow runs this bounded proof before its
existing real configured-results, browser, restore and two-policy gates.

PostgreSQL tests cover genuine full-chain installation/downgrade, all three
functions' predecessor drift, exact identity/owner/ACL/default/body preservation,
name-resolution shadows, maintenance refusal, reader/writer exclusion, failure
after actual replacement and the complete generic differential corpus. Separate
focused real-role cases use SET LOCAL ROLE with non-superusers: direct main owner,
inherited owner, rejected non-inherited owner, and independently owned executable
helpers. A real LATIN1 database installs the full032f chain and tests032g,
non-ASCII differential behavior and rollback in both directions.

Administrative tests are tightly opt-in to the already owned disposable CI
service. They verify the GitHub workspace, exact loopback URL, CID file identity,
container ID/name/class/ports/runtime, database login and PostgreSQL system/start
identity. Roles and focused schemas are uniquely generated and transactionally
rolled back. LATIN1 cleanup requires the original service plus the exact created
database name/OID/owner; it never uses FORCE, backend termination or DROP OWNED.
V1's blanket integration collection is explicitly wired to its own service token;
its separate final-native service is unchanged. These helpers perform no I/O at
import or collection and never provision a PostgreSQL server.

A successful source suite or collected PostgreSQL inventory is not execution
proof. Real PostgreSQL18, native Qlib and OpenEvolve, browser and cold-restore
acceptance remain required on the exact published draft-PR tree. Existing
120-second RD / 8-second OE deadlines, receipt/identity/containment checks,
provider policies, frozen runtime image inventories, workflow triggers and
permissions are unchanged. The added proof step has its own five-minute bound;
no existing timeout is raised. No merge, master update or production promotion
is authorized by this experiment.

The published baseline has native Qlib/OE deadline failures. Isolated key-set
measurements are motivation only, not evidence this optimization is sufficient.
Overlapping timing spans must not be summed. Without the separate Parquet fix,
a Parquet-specific failure can still occur independently; classify it separately
rather than weakening this experiment's acceptance gates.

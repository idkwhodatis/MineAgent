"""Closed persistence constraint reference, with an offline fragment for review.

Revision016 freezes these constants in historical SQL and ORM uses the same
closed grammar. The reference SQL is not an execution or activation API.
The empty v2 profile gate intentionally rejects ALL v2 inserts, even valid grammar.
Replacing that gate requires accepted role/profile/image/launcher mappings.
"""

# IS TRUE around the complete disjunction closes PostgreSQL CHECK's NULL loophole.
GRAMMAR = r"""(
 (schema_version = 'mineagent.process-admission.v1'
  AND role = 'neutral_fixture' AND profile = 'neutral_fixture.v1'
  AND ipc_policy = 'host_ticket' AND ticket_sha256 IS NOT NULL
  AND ticket_sha256 ~ '^[a-f0-9]{64}$')
 OR
 (schema_version = 'mineagent.process-admission.v2'
  AND generation BETWEEN 1 AND 2147483647
  AND launch_ordinal BETWEEN 1 AND 2147483647
  AND profile ~ '^[a-z][a-z0-9_.-]{1,59}$'
  AND (
   (role IN ('rd_orchestrator','openevolve_orchestrator')
    AND ipc_policy = 'host_ticket' AND ticket_sha256 IS NOT NULL
    AND ticket_sha256 ~ '^[a-f0-9]{64}$')
   OR
   (role IN ('factor_cutoff_candidate','factor_normalizer','native_evaluator')
    AND ipc_policy = 'none' AND ticket_sha256 IS NULL)
  ))
) IS TRUE"""
PROFILE_GATE = "(schema_version = 'mineagent.process-admission.v1') IS TRUE"
HASHES = (
    "(input_sha256 ~ '^[a-f0-9]{64}$' AND image_id ~ '^sha256:[a-f0-9]{64}$') IS TRUE"
)

SQL = f"""-- INCOMPLETE OFFLINE FRAGMENT: V2 PERSISTENCE IS NOT ACTIVATED.
LOCK TABLE research_process_admissions IN ACCESS EXCLUSIVE MODE;
ALTER TABLE research_process_admissions
 ADD COLUMN schema_version varchar(40) NOT NULL DEFAULT 'mineagent.process-admission.v1',
 ADD COLUMN ipc_policy varchar(20) NOT NULL DEFAULT 'host_ticket';
ALTER TABLE research_process_admissions
 ALTER COLUMN schema_version DROP DEFAULT,
 ALTER COLUMN ipc_policy DROP DEFAULT,
 ALTER COLUMN ticket_sha256 DROP NOT NULL,
 DROP CONSTRAINT ck_research_process_profile,
 DROP CONSTRAINT ck_research_process_hashes,
 ADD CONSTRAINT ck_process_versioned_grammar CHECK ({GRAMMAR}),
 ADD CONSTRAINT ck_process_versioned_hashes CHECK ({HASHES}),
 ADD CONSTRAINT ck_process_v2_unregistered CHECK ({PROFILE_GATE});
"""


def migration_fragment() -> dict:
    return {
        "activation_ready": False,
        "registered_v2_profiles": (),
        "sql": SQL,
        "requires": (
            "explicit versioned model/service/issuer/IPC dispatch",
            "unchanged v1 commitments and legacy restore",
            "closed accepted v2 role/profile/image and launcher mapping",
            "real PostgreSQL upgrade and direct-SQL rejection acceptance",
        ),
    }

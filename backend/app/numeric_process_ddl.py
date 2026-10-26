"""Closed persistence capabilities, explicitly not runtime launch grants."""

PROFILE_GATE = """(schema_version = 'mineagent.process-admission.v1' OR
 (schema_version = 'mineagent.process-admission.v2' AND ipc_policy = 'none'
  AND ticket_sha256 IS NULL AND (
   (role = 'factor_cutoff_candidate' AND profile = 'mineagent.factor-prefix-candidate.v2') OR
   (role = 'factor_normalizer' AND profile = 'mineagent.factor-numeric-hdf.v1')))) IS TRUE"""

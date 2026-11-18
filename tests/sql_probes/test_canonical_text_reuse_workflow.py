"""Exact additive SQL-only proof wiring. No production or native acceptance."""

import hashlib
import json
from pathlib import Path

from script import sql_candidate_proof as proof


def test_pure_wiring_preserves_original_sql_steps_and_old_inventories():
    source = Path(".github/workflows/mineagent-sql-candidates.yml").read_text()
    first = "      - name: Require pure canonical text reuse differential without runtime activation\n"
    last = (
        "      - name: Always remove only this job's exact owned PostgreSQL service\n"
    )
    before, tail = source.split(first, 1)
    _, after = tail.split(last, 1)
    original = (before + last + after).replace(
        "branches: [master, experiment/pure-validator-proof-20261118]",
        "branches: [master]",
        1,
    )
    original = original.replace(
        '      - "tests/integration/test_canonical_text_reuse_probe.py"\n', "", 1
    )
    assert (
        hashlib.sha256(original.encode()).hexdigest()
        == "aeb4e222dd4dbc65869ba4fd0726343a95acffc50aaaa4e6f4fcae082cd66850"
    )
    manifest = json.loads(proof.MANIFEST.read_text())
    del manifest["pure_canonical"], manifest["pure_canonical_cost"]
    assert (
        hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        == "d8217cbd2028f600e5a50969363cfda310217c57f3124a9bf05ebedb3c4fae76"
    )


def test_pure_cost_needs_independent_differential_success_and_private_inventory():
    source = Path(".github/workflows/mineagent-sql-candidates.yml").read_text()
    pure = source.split(
        "      - name: Require pure canonical text reuse differential without runtime activation\n",
        1,
    )[1].split(
        "      - name: Always remove only this job's exact owned PostgreSQL service\n",
        1,
    )[0]
    assert pure.count("steps.source_contracts.outcome == 'success'") == 2
    assert pure.count("steps.candidate_postgres.outcome == 'success'") == 2
    assert pure.count("steps.pure_canonical.outcome == 'success'") == 1
    assert pure.count('MINEAGENT_PURE_CANONICAL_REUSE_REQUIRED: "1"') == 2
    assert pure.count("timeout-minutes: 5") == 2
    assert "continue-on-error" not in pure
    assert "heartbeat_native" not in pure and "reverse_heartbeat_native" not in pure
    for group in ("pure_canonical", "pure_canonical_cost"):
        assert "--group " + group in pure
        for node in proof.inventory(group):
            assert node.split("::")[0] in pure
    assert len(proof.inventory("pure_canonical_cost")) == 1

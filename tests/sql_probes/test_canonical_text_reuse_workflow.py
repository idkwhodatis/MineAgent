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
    for path in [
        ".github/workflows/v1-validation.yml",
        "script/experimental_integration_partition.py",
        "tests/runner/experimental_integration_partition.json",
        "tests/runner/test_experimental_integration_partition.py",
        "tests/runner/test_ci_acceptance_boundary.py",
    ]:
        original = original.replace('      - "' + path + '"\n', "", 1)
    partition = "      - name: Require exact four experimental partition contracts separately\n        timeout-minutes: 3\n        shell: bash\n        run: |\n          set -euo pipefail\n          python -m pytest tests/runner/test_experimental_integration_partition.py -q -p no:cacheprovider --junitxml=\"$RUNNER_TEMP/sql-candidate-private/partition.xml\"\n          python - <<'PYPARTITION'\n          import os\n          from pathlib import Path\n          from xml.etree import ElementTree as ET\n          source=Path(os.environ['RUNNER_TEMP'])/'sql-candidate-private/partition.xml'\n          cases=list(ET.parse(source).getroot().iter('testcase'))\n          assert len(cases)==4\n          assert {case.get('name') for case in cases}=={'test_each_dedicated_experimental_group_requires_exact_successful_junit', 'test_partition_rejects_preexisting_exclusions_missing_nodes_or_changed_gates', 'test_experimental_partition_preserves_original_modules_and_full_collection', 'test_current_head_baseline_exact_inline_xml_gate_still_rejects_bad_results'}\n          assert not any(case.find(tag) is not None for case in cases for tag in ('failure','error','skipped'))\n          PYPARTITION\n"
    assert original.count(partition) == 1
    original = original.replace(partition, "", 1)
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

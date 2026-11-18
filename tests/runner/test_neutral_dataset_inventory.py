"""Dataset baseline cases remain mandatory, exact, and no-skip in neutral PG."""

from xml.etree import ElementTree as ET

import pytest

from script.neutral_acceptance import REQUIRED, verify_report
from script.sql_candidate_proof import inventory
from tests.mineagent.test_neutral_acceptance import report, save

DATASET_CASES = tuple(inventory("dataset_admission"))


def test_dataset_baseline_has_one_exact_neutral_owner(tmp_path):
    assert len(DATASET_CASES) == 5
    for node in DATASET_CASES:
        assert REQUIRED["postgres"].count(node) == 1
        assert all(
            node not in cases for key, cases in REQUIRED.items() if key != "postgres"
        )
    path, _, _ = report(tmp_path, "postgres")
    verify_report(path, "postgres", 0)


@pytest.mark.parametrize("node", DATASET_CASES)
@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "skipped", "failure", "error"]
)
def test_each_dataset_case_is_mandatory_and_cannot_hide_failure(
    tmp_path, node, mutation
):
    path, root, suite = report(tmp_path, "postgres")
    source, name = node.split("::")
    module = source.removesuffix(".py").replace("/", ".")
    case = next(
        c for c in suite if c.get("classname") == module and c.get("name") == name
    )
    if mutation == "missing":
        suite.remove(case)
        suite.set("tests", str(len(suite)))
    elif mutation == "duplicate":
        ET.SubElement(suite, "testcase", **case.attrib)
        suite.set("tests", str(len(suite)))
    else:
        ET.SubElement(case, mutation)
    save(path, root)
    with pytest.raises(ValueError):
        verify_report(path, "postgres", 0)

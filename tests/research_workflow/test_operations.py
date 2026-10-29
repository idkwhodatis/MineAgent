"""Exact dispatch hooks, not a physical restore acceptance fixture."""

from types import SimpleNamespace

import pytest

from backend.app import research_workflow_operations as m


def cap(**changes):
    return SimpleNamespace(
        **(
            {
                "workflow_schema": m.WORKFLOW_CAP,
                "research_input_schema": m.INPUT_CAP,
                "research_closure_schema": m.PAIR[0],
                "rd_source_closure_schema": m.PAIR[1],
            }
            | changes
        )
    )


def snapshot():
    return dict(
        workflow_closure={"schema_version": m.research_workflow_backup.SCHEMA},
        research_input_closure={"schema_version": m.research_input_backup.SCHEMA},
        **{
            k: {}
            for k in (
                "research_closure",
                "rd_source_closure",
                "execution_closure",
                "direct_analysis_closure",
                "factor_closure",
                "rd_steer_closure",
            )
        },
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"workflow_schema": None},
        {"research_input_schema": None},
        {"rd_source_closure_schema": "mineagent.rd-source-restore-closure.v6"},
        {"research_closure_schema": "future"},
    ],
)
def test_mixed_tuple_rejected(changes):
    with pytest.raises(ValueError):
        m.closures_for(snapshot(), cap(**changes))


def test_old_tuple_cannot_smuggle_new_closures():
    old = SimpleNamespace(
        research_closure_schema="mineagent.research-restore-closure.v11",
        rd_source_closure_schema="mineagent.rd-source-restore-closure.v6",
    )
    assert m.prepare_context({}, old, None, 10) is None
    with pytest.raises(ValueError):
        m.closures_for(snapshot(), old)


def test_final_pass_preserves_actual_inputs_and_inventory(monkeypatch):
    s = snapshot()
    reader = object()
    inventory = []
    seen = []

    def final(closure, **kw):
        assert closure is s["workflow_closure"]
        assert kw["read_bytes"] is reader and kw["inventory"] is inventory
        assert kw["input_closure"] is s["research_input_closure"]
        assert kw["steer_closure"] is s["rd_steer_closure"]
        assert kw["maximum_rows"] == 17
        seen.append(True)

    monkeypatch.setattr(m.research_workflow_backup, "validate_closure", final)
    m.validate_snapshot(s, cap(), reader, 17, inventory=inventory)
    assert seen == [True]


def test_missing_source_closure_rejected_before_context():
    s = snapshot()
    del s["research_input_closure"]
    with pytest.raises(ValueError):
        m.prepare_context(s, cap(), None, 10)

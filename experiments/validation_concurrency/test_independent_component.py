"""Independent declarative regressions for the existing offline component model.

These tests do not use candidate violations/apply/plan/seeds/closure as expected
outcome oracles. The adjacent candidate model is imported normally.
They are model tests, not PostgreSQL execution or concurrency evidence.
"""
from copy import deepcopy
import unittest

import component_model as m


def graph_errors(graph):
    """Independent set relations expressing each of the three invariants."""
    workflow_ids = set(graph.workflows)
    missing_parent = {(wid, 'missing_parent') for wid, row in graph.workflows.items()
                      if row.parent not in graph.parents}
    missing_receipt = {(wid, 'missing_receipt') for wid in workflow_ids - graph.receipts}
    orphan_receipt = {(wid, 'receipt_without_workflow') for wid in graph.receipts - workflow_ids}
    return missing_parent | missing_receipt | orphan_receipt


def base():
    return m.Graph({1, 2}, {1: m.Workflow(1, 1), 2: m.Workflow(2, 2)}, {1, 2})


def enter(db, changes):
    tx = db.begin()
    tx.admit(changes)
    tx.execute(changes)
    return tx


class IndependentInvariantTests(unittest.TestCase):
    def assert_rejected_unchanged(self, before, changes, category):
        untouched = deepcopy(before)
        db = m.Coordinator(before)
        tx = enter(db, changes)
        with self.assertRaisesRegex(m.Rejected, '^' + category + '$'):
            tx.commit()
        self.assertEqual(db.graph, untouched)
        self.assertEqual(db.locks, {})
        self.assertTrue(tx.closed)
        self.assertEqual(before, untouched)

    def assert_commit_graph(self, before, changes, expected, expected_errors=frozenset()):
        untouched = deepcopy(before)
        db = m.Coordinator(before)
        enter(db, changes).commit()
        self.assertEqual(db.graph, expected)
        self.assertEqual(graph_errors(db.graph), set(expected_errors))
        self.assertEqual(db.locks, {})
        self.assertEqual(before, untouched)

    def test_missing_parent_referenced_parent_delete_rejected(self):
        self.assert_rejected_unchanged(base(), [m.Change('parent', 1, None)], 'missing_parent')

    def test_missing_parent_workflow_and_receipt_insert_rejected(self):
        self.assert_rejected_unchanged(m.Graph(), [
            m.Change('workflow', 7, m.Workflow(3, 42)), m.Change('receipt', 7, True)], 'missing_parent')

    def test_missing_parent_endpoint_move_rejected(self):
        self.assert_rejected_unchanged(base(), [
            m.Change('workflow', 1, m.Workflow(1, 999))], 'missing_parent')

    def test_missing_receipt_delete_rejected(self):
        self.assert_rejected_unchanged(base(), [m.Change('receipt', 1, None)], 'missing_receipt')

    def test_missing_receipt_new_workflow_rejected(self):
        self.assert_rejected_unchanged(m.Graph({1}, {}, set()), [
            m.Change('workflow', 7, m.Workflow(3, 1))], 'missing_receipt')

    def test_orphan_receipt_insert_rejected(self):
        self.assert_rejected_unchanged(m.Graph(), [m.Change('receipt', 7, True)], 'receipt_without_workflow')

    def test_orphan_receipt_workflow_delete_rejected(self):
        self.assert_rejected_unchanged(base(), [m.Change('workflow', 1, None)], 'receipt_without_workflow')

    def test_parent_repair_commits(self):
        self.assert_commit_graph(
            m.Graph(set(), {7:m.Workflow(3, 42)}, {7}), [m.Change('parent', 42, True)],
            m.Graph({42}, {7:m.Workflow(3, 42)}, {7}))

    def test_receipt_repair_commits(self):
        self.assert_commit_graph(
            m.Graph({42}, {7:m.Workflow(3, 42)}, set()), [m.Change('receipt', 7, True)],
            m.Graph({42}, {7:m.Workflow(3, 42)}, {7}))

    def test_orphan_repair_by_workflow_insertion_commits(self):
        self.assert_commit_graph(
            m.Graph({42}, {}, {7}), [m.Change('workflow', 7, m.Workflow(3, 42))],
            m.Graph({42}, {7:m.Workflow(3, 42)}, {7}))

    def test_workflow_and_receipt_delete_commits(self):
        self.assert_commit_graph(base(), [m.Change('workflow', 1, None), m.Change('receipt', 1, None)],
            m.Graph({1, 2}, {2:m.Workflow(2, 2)}, {2}))

    def test_delete_complete_component_commits(self):
        self.assert_commit_graph(base(), [m.Change('parent', 1, None),
            m.Change('workflow', 1, None), m.Change('receipt', 1, None)],
            m.Graph({2}, {2:m.Workflow(2, 2)}, {2}))

    def test_orphan_delete_repairs(self):
        self.assert_commit_graph(m.Graph(set(), {}, {7}), [m.Change('receipt', 7, None)], m.Graph())

    def test_reparent_between_existing_parents_commits(self):
        self.assert_commit_graph(base(), [m.Change('workflow', 1, m.Workflow(1, 2))],
            m.Graph({1, 2}, {1:m.Workflow(1, 2), 2:m.Workflow(2, 2)}, {1, 2}))

    def test_old_component_corruption_is_checked_after_reparent(self):
        before = m.Graph({1, 2}, {1:m.Workflow(1, 1), 2:m.Workflow(2, 1)}, {1})
        self.assert_rejected_unchanged(before, [m.Change('workflow', 1, m.Workflow(1, 2))], 'missing_receipt')

    def test_unrelated_corruption_remains_acceptable(self):
        before = m.Graph({1}, {1:m.Workflow(1, 1), 2:m.Workflow(2, 2)}, {1, 2})
        expected = m.Graph({1}, {1:m.Workflow(8, 1), 2:m.Workflow(2, 2)}, {1, 2})
        self.assert_commit_graph(before, [m.Change('workflow', 1, m.Workflow(8, 1))],
                                 expected, {(2, 'missing_parent')})

    def test_transient_edge_still_checks_unchanged_corrupt_peer(self):
        before = m.Graph({1, 2}, {1:m.Workflow(1, 1), 2:m.Workflow(2, 2)}, {1})
        self.assert_rejected_unchanged(before, [m.Change('workflow', 1, m.Workflow(1, 2)),
            m.Change('workflow', 1, m.Workflow(1, 1))], 'missing_receipt')

    def test_later_node_rewrite_invalidates_already_checked_earlier_node(self):
        before = base()
        db = m.Coordinator(before)
        tx = enter(db, [m.Change('workflow', 1, m.Workflow(8, 1)),
                        m.Change('workflow', 2, m.Workflow(9, 2))])
        visited = []
        def rewrite_after_second_check(current, wid):
            visited.append(wid)
            if wid == 2:
                current.execute([m.Change('receipt', 1, None)])
        with self.assertRaisesRegex(m.Rejected, '^missing_receipt$'):
            tx.validate(after_check=rewrite_after_second_check)
        self.assertEqual(visited, [1, 2])
        self.assertNotEqual(tx.validated.get(1), tx.dirty[1])
        self.assertTrue(tx.failed)
        tx.rollback()
        self.assertEqual(db.graph, before)
        self.assertEqual(db.locks, {})

    def test_gate_is_held_during_validation_callback(self):
        db = m.Coordinator(base())
        tx = enter(db, [m.Change('workflow', 1, m.Workflow(8, 1))])
        conflicts = []
        def competing_writer(current, wid):
            other = db.begin()
            with self.assertRaisesRegex(m.Rejected, '^writer_busy$'):
                other.admit([m.Change('receipt', 1, None)])
            conflicts.append(wid)
            other.rollback()
        tx.validate(after_check=competing_writer)
        self.assertEqual(conflicts, [1])
        tx.commit()
        self.assertEqual(graph_errors(db.graph), set())
        self.assertEqual(db.locks, {})

    def test_failed_immediate_statement_rollback_restores_direct_invariants(self):
        db = m.Coordinator(base())
        tx = enter(db, [m.Change('workflow', 1, m.Workflow(8, 1))])
        tx.set_immediate()
        sp = tx.savepoint()
        with self.assertRaisesRegex(m.Rejected, '^missing_parent$'):
            tx.execute([m.Change('parent', 1, None)])
        self.assertTrue(tx.failed)
        tx.rollback_to(sp)
        self.assertFalse(tx.failed)
        tx.commit()
        self.assertEqual(db.graph, m.Graph({1, 2}, {1:m.Workflow(8, 1), 2:m.Workflow(2, 2)}, {1, 2}))
        self.assertEqual(graph_errors(db.graph), set())
        self.assertEqual(db.locks, {})


if __name__ == '__main__':
    unittest.main()

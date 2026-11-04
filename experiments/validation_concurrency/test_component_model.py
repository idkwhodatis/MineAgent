"""Reconstructed from retained conversation, 2026-11-03; not original-byte evidence.
Offline abstract-model falsification only. No PostgreSQL or production proof.
"""
import itertools
import unittest

from component_model import Change, Coordinator, Graph, Rejected, Workflow, apply, plan, violations


def base(*, shared=False):
    return Graph({1, 2}, {1: Workflow(1, 1), 2: Workflow(2, 1 if shared else 2)}, {1, 2})


def edit(wid, owner=9, parent=None):
    return Change("workflow", wid, Workflow(owner, wid if parent is None else parent))


def enter(db, changes):
    tx = db.begin()
    tx.admit(changes)
    tx.execute(changes)
    return tx


def finite_domain():
    choices = [None] + [Workflow(owner, parent) for owner in (1, 2) for parent in (1, 2, 3)]
    changes = [Change(table, key, value) for table in ("parent", "receipt")
               for key in (1, 2) for value in (None, True)]
    changes += [Change("workflow", key, value) for key in (1, 2) for value in choices]
    return choices, changes


class RoutingTests(unittest.TestCase):
    def test_old_and_new_parent_components(self):
        self.assertEqual(plan(base(), [edit(1, parent=2)]),
                         {("workflow", 1), ("workflow", 2), ("parent", 1), ("parent", 2)})

    def test_deleted_workflow_retains_receipt_identity_and_old_parent(self):
        g = base(shared=True)
        change = Change("workflow", 1, None)
        self.assertEqual(plan(g, [change]), {("workflow", 1), ("workflow", 2), ("parent", 1)})
        self.assertIn((1, "receipt_without_workflow"), violations(apply(g, change)))

    def test_missing_parent_is_still_a_lock_key(self):
        g = Graph(set(), {1: Workflow(1, 77)}, {1})
        self.assertEqual(plan(g, [Change("parent", 77, True)]), {("workflow", 1), ("parent", 77)})

    def test_receipt_with_missing_workflow_never_routes_to_empty_set(self):
        self.assertEqual(plan(Graph(), [Change("receipt", 88, True)]), {("workflow", 88)})

    def test_intermediate_edge_is_not_lost(self):
        keys = plan(base(), [edit(1, parent=2), edit(1, parent=1)])
        self.assertIn(("workflow", 2), keys)
        self.assertIn(("parent", 2), keys)

    def test_exhaustive_changed_predicates_are_routed(self):
        """1,568 states x 22 mutations = 34,496 bounded comparisons."""
        choices, changes = finite_domain()
        checked = 0
        for pb, rb, rows in itertools.product(itertools.product((False, True), repeat=3),
                                              itertools.product((False, True), repeat=2),
                                              itertools.product(choices, repeat=2)):
            g = Graph({i+1 for i, flag in enumerate(pb) if flag},
                      {i+1: row for i, row in enumerate(rows) if row is not None},
                      {i+1 for i, flag in enumerate(rb) if flag})
            previous = set(violations(g))
            for change in changes:
                changed = previous ^ set(violations(apply(g, change)))
                selected = plan(g, [change])
                self.assertTrue(all(("workflow", wid) in selected for wid, _ in changed))
                checked += 1
        self.assertEqual(checked, 34496)

    def test_bad_new_only_routing_counterexample(self):
        g = base(shared=True)
        change = Change("workflow", 1, None)
        after = apply(g, change)
        naive = {("workflow", wid) for wid in after.workflows if wid == change.key}
        self.assertEqual(naive, set())
        self.assertIn((1, "receipt_without_workflow"), violations(after))
        self.assertIn(("workflow", 1), plan(g, [change]))


class CoordinationTests(unittest.TestCase):
    def test_2904_bounded_two_transaction_parameter_cases(self):
        """Parameter cases, not unique interleavings; shared full-graph oracle."""
        _, changes = finite_domain()
        checked = 0
        for graph, first, second, reverse in itertools.product(
                (Graph(), base(), base(shared=True)), changes, changes, (False, True)):
            db = Coordinator(graph)
            a = enter(db, [first])
            b = db.begin()
            overlap = bool(plan(graph, [first]) & plan(graph, [second]))
            if overlap:
                with self.assertRaisesRegex(Rejected, "writer_busy"):
                    b.admit([second])
                b.rollback()
                admitted = [(a, first)]
            else:
                b.admit([second])
                b.execute([second])
                admitted = [(a, first), (b, second)]
                if reverse:
                    admitted.reverse()
            expected = graph
            for tx, change in admitted:
                candidate = apply(expected, change)
                if violations(candidate):
                    with self.assertRaises(Rejected):
                        tx.commit()
                else:
                    tx.commit()
                    expected = candidate
            self.assertEqual(db.graph, expected)
            self.assertEqual(violations(db.graph), [])
            self.assertEqual(db.locks, {})
            checked += 1
        self.assertEqual(checked, 2904)

    def test_disjoint_model_components_commit_without_lost_updates(self):
        db = Coordinator(base())
        a, b = enter(db, [edit(1, owner=8)]), enter(db, [edit(2, owner=7)])
        b.commit()
        a.commit()
        self.assertEqual(db.graph.workflows, {1: Workflow(8, 1), 2: Workflow(7, 2)})
        self.assertEqual(db.locks, {})

    def test_shared_parent_cross_owner_conflict(self):
        db = Coordinator(base(shared=True))
        a = enter(db, [edit(1, parent=1)])
        b = db.begin()
        with self.assertRaisesRegex(Rejected, "writer_busy"):
            b.admit([edit(2, parent=1)])
        self.assertFalse(b.changes)
        b.rollback()
        a.commit()
        enter(db, [edit(2, parent=1)]).commit()

    def test_parent_lock_excludes_new_reverse_reference_phantom(self):
        db = Coordinator(base())
        a = enter(db, [edit(1)])
        b = db.begin()
        with self.assertRaisesRegex(Rejected, "writer_busy"):
            b.admit([edit(3, parent=1), Change("receipt", 3, True)])
        self.assertNotIn(3, db.graph.workflows)
        b.rollback()
        a.rollback()

    def test_reverse_reference_between_discovery_and_lock_aborts(self):
        db = Coordinator(base())
        change = edit(1)
        stale = plan(db.graph, [change])
        enter(db, [edit(3, parent=1), Change("receipt", 3, True)]).commit()
        a = db.begin()
        with self.assertRaisesRegex(Rejected, "scope_expanded"):
            a.admit([change], discovered=stale)
        self.assertFalse(a.changes)
        a.rollback()
        self.assertEqual(db.locks, {})
        enter(db, [change]).commit()

    def test_scope_expansion_after_mutation_requires_whole_retry(self):
        db = Coordinator(base())
        a = enter(db, [edit(1, owner=8)])
        with self.assertRaisesRegex(Rejected, "scope_expanded"):
            a.execute([edit(2, owner=7)])
        with self.assertRaisesRegex(Rejected, "whole_transaction_restart_required"):
            a.commit()
        a.rollback()
        self.assertEqual(db.graph, base())
        enter(db, [edit(1, owner=8), edit(2, owner=7)]).commit()

    def test_endpoint_move_conflicts_with_both_sides(self):
        for locked in (1, 2):
            with self.subTest(locked=locked):
                db = Coordinator(base())
                a = enter(db, [edit(locked)])
                b = db.begin()
                with self.assertRaisesRegex(Rejected, "writer_busy"):
                    b.admit([edit(1, parent=2)])
                b.rollback()
                a.rollback()

    def test_no_write_before_admission(self):
        db = Coordinator(base())
        a = db.begin()
        with self.assertRaisesRegex(Rejected, "writer_not_admitted"):
            a.execute([edit(1)])
        self.assertEqual(db.graph, base())
        a.rollback()

    def test_preheld_lock_rejected_by_abstract_assumption(self):
        db = Coordinator(base())
        a = db.begin(preheld_incompatible_lock=True)
        with self.assertRaisesRegex(Rejected, "preheld_incompatible_lock"):
            a.admit([edit(1)])
        self.assertEqual(db.locks, {})
        a.rollback()

    def test_scope_expansion_cannot_retry_inside_savepoint(self):
        db = Coordinator(base())
        a = db.begin()
        sp = a.savepoint()
        a.admit([edit(1)])
        with self.assertRaisesRegex(Rejected, "scope_expanded"):
            a.execute([edit(2)])
        with self.assertRaisesRegex(Rejected, "whole_transaction_restart_required"):
            a.rollback_to(sp)
        with self.assertRaisesRegex(Rejected, "whole_transaction_restart_required"):
            a.admit([edit(1), edit(2)])
        a.rollback()
        b = enter(db, [edit(1), edit(2)])
        self.assertNotEqual(a.ident, b.ident)
        b.commit()

    def test_dangling_parent_insertion_repair_locks_workflow(self):
        db = Coordinator(Graph(set(), {1: Workflow(1, 9)}, {1}))
        a = enter(db, [Change("parent", 9, True)])
        b = db.begin()
        with self.assertRaisesRegex(Rejected, "writer_busy"):
            b.admit([Change("receipt", 1, None)])
        b.rollback()
        a.commit()
        self.assertEqual(violations(db.graph), [])

    def test_failed_multi_key_admission_does_not_partially_acquire(self):
        db = Coordinator(base())
        a = enter(db, [edit(2)])
        original = dict(db.locks)
        b = db.begin()
        with self.assertRaisesRegex(Rejected, "writer_busy"):
            b.admit([edit(1), edit(2)])
        self.assertEqual(db.locks, original)
        b.rollback()
        a.rollback()


class ValidationTests(unittest.TestCase):
    def test_unrelated_preexisting_corruption_is_not_rejected(self):
        graph = base()
        graph.receipts.remove(2)
        db = Coordinator(graph)
        enter(db, [edit(1)]).commit()
        self.assertEqual(violations(db.graph), [(2, "missing_receipt")])

    def test_corruption_in_same_shared_component_is_rejected(self):
        graph = base(shared=True)
        graph.receipts.remove(2)
        db = Coordinator(graph)
        a = enter(db, [edit(1)])
        with self.assertRaisesRegex(Rejected, "missing_receipt"):
            a.commit()
        self.assertEqual(db.graph, graph)
        self.assertEqual(db.locks, {})

    def test_commit_rejects_deferred_orphan(self):
        db = Coordinator(base())
        a = enter(db, [Change("receipt", 1, None)])
        with self.assertRaisesRegex(Rejected, "missing_receipt"):
            a.commit()
        self.assertEqual(db.graph, base())

    def test_one_statement_can_make_and_repair_intermediate_orphan(self):
        db = Coordinator(base())
        a = db.begin()
        changes = [Change("receipt", 1, None), Change("receipt", 1, True)]
        a.admit(changes)
        a.set_immediate()
        a.execute(changes)
        a.commit()

    def test_set_immediate_rejects_at_boundary(self):
        db = Coordinator(base())
        a = enter(db, [Change("receipt", 1, None)])
        with self.assertRaisesRegex(Rejected, "missing_receipt"):
            a.set_immediate()
        a.rollback()

    def test_later_write_invalidates_previously_successful_check(self):
        db = Coordinator(base())
        a = enter(db, [edit(1)])
        a.set_immediate()
        a.set_deferred()
        a.execute([Change("receipt", 1, None)])
        with self.assertRaisesRegex(Rejected, "missing_receipt"):
            a.commit()

    def test_immediate_mode_applies_to_next_statement(self):
        db = Coordinator(base())
        a = enter(db, [edit(1)])
        a.set_immediate()
        with self.assertRaisesRegex(Rejected, "missing_receipt"):
            a.execute([Change("receipt", 1, None)])
        a.rollback()

    def test_redundant_check_skips_only_unchanged_generation(self):
        db = Coordinator(base())
        a = enter(db, [edit(1)])
        a.validate()
        count = a.validation_count
        a.validate()
        self.assertEqual(a.validation_count, count)
        a.execute([edit(1, owner=8)])
        a.validate()
        self.assertEqual(a.validation_count, count+1)
        a.commit()

    def test_later_deferred_mutation_cannot_be_marked_already_checked(self):
        db = Coordinator(base())
        a = enter(db, [edit(1)])
        invoked = []
        def later_write(tx, wid):
            if not invoked:
                invoked.append(wid)
                tx.execute([Change("receipt", 1, None)])
        with self.assertRaisesRegex(Rejected, "missing_receipt"):
            a.validate(after_check=later_write)
        self.assertNotEqual(a.validated.get(1), a.dirty[1])
        a.rollback()

    def test_finite_deferred_rewrite_reaches_new_fixed_point(self):
        db = Coordinator(base())
        a = enter(db, [edit(1)])
        invoked = []
        def later_write(tx, wid):
            if not invoked:
                invoked.append(wid)
                tx.execute([edit(1, owner=6)])
        a.validate(after_check=later_write)
        self.assertEqual(a.validation_count, 2)
        a.commit()
        self.assertEqual(db.graph.workflows[1].owner, 6)

    def test_repeated_trigger_rewrite_hits_fail_closed_limit(self):
        db = Coordinator(base())
        a = enter(db, [edit(1)])
        with self.assertRaisesRegex(Rejected, "validation_rewrite_limit"):
            a.validate(after_check=lambda tx, wid: tx.execute([edit(1)]), max_checks=3)
        a.rollback()
        self.assertEqual(db.graph, base())

    def test_large_unchanged_component_is_not_mislabeled_cycle(self):
        graph = Graph({1}, {wid: Workflow(wid, 1) for wid in range(1, 101)}, set(range(1, 101)))
        db = Coordinator(graph)
        a = enter(db, [edit(1)])
        a.validate()
        self.assertEqual(a.validation_count, 100)
        a.commit()

    def test_invalid_write_categories_fail_closed(self):
        for change in (Change("truncate", 1, True), Change("parent", True, True),
                       Change("workflow", 1, Workflow(1, 0)), Change("workflow", 1, True),
                       Change("parent", 1, False)):
            with self.subTest(change=change), self.assertRaises(Rejected):
                apply(base(), change)

    def test_deterministic_affected_error_precedence(self):
        db = Coordinator(base())
        a = enter(db, [Change("parent", 2, None), Change("receipt", 1, None)])
        with self.assertRaisesRegex(Rejected, "missing_receipt"):
            a.commit()


class SavepointTests(unittest.TestCase):
    def test_savepoint_rolls_back_data_and_generation(self):
        db = Coordinator(base())
        a = enter(db, [edit(1)])
        a.validate()
        saved = (dict(a.dirty), dict(a.validated), a.generation)
        sp = a.savepoint()
        a.execute([Change("receipt", 1, None)])
        a.rollback_to(sp)
        self.assertEqual((a.dirty, a.validated, a.generation), saved)
        a.commit()
        self.assertEqual(violations(db.graph), [])

    def test_failed_statement_can_rollback_to_prior_savepoint(self):
        db = Coordinator(base())
        a = enter(db, [edit(1)])
        a.set_immediate()
        sp = a.savepoint()
        with self.assertRaisesRegex(Rejected, "missing_receipt"):
            a.execute([Change("receipt", 1, None)])
        a.rollback_to(sp)
        a.commit()

    def test_post_savepoint_admission_locks_are_released_in_model(self):
        db = Coordinator(base())
        a = db.begin()
        sp = a.savepoint()
        a.admit([edit(1)])
        a.execute([edit(1)])
        a.rollback_to(sp)
        self.assertEqual(db.locks, {})
        enter(db, [edit(1, owner=6)]).commit()
        a.commit()
        self.assertEqual(db.graph.workflows[1].owner, 6)

    def test_pre_savepoint_locks_and_dirty_work_are_retained(self):
        db = Coordinator(base())
        a = enter(db, [edit(1)])
        sp = a.savepoint()
        a.execute([Change("receipt", 1, None)])
        a.rollback_to(sp)
        b = db.begin()
        with self.assertRaisesRegex(Rejected, "writer_busy"):
            b.admit([edit(1)])
        b.rollback()
        self.assertTrue(a.dirty)
        a.commit()

    def test_nested_rollback_discards_later_savepoint(self):
        db = Coordinator(base())
        a = enter(db, [edit(1)])
        first = a.savepoint()
        a.execute([edit(1, owner=6)])
        second = a.savepoint()
        a.execute([edit(1, owner=7)])
        a.rollback_to(first)
        with self.assertRaisesRegex(Rejected, "unknown_savepoint"):
            a.rollback_to(second)
        a.commit()
        self.assertEqual(db.graph.workflows[1].owner, 9)

    def test_constraint_mode_rolls_back_with_savepoint_in_model(self):
        db = Coordinator(base())
        a = enter(db, [edit(1)])
        sp = a.savepoint()
        a.set_immediate()
        a.rollback_to(sp)
        self.assertFalse(a.immediate)
        a.execute([Change("receipt", 1, None), Change("receipt", 1, True)])
        a.commit()


if __name__ == "__main__":
    unittest.main()

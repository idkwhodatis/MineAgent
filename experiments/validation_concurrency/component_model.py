"""Reconstructed abstract specification, not PostgreSQL or production code.

Rebuilt 2026-11-03 from retained conversation after the original workspace was
lost. No claim of byte equivalence to the lost artifact is made. Lock atomicity,
fresh views, complete writer admission and rollback are explicit assumptions.
Only a synthetic parent/workflow/receipt graph is represented.
"""
from copy import deepcopy
from dataclasses import dataclass, field

Key = tuple[str, int]


class Rejected(Exception):
    """Bounded model category, never a raw database/user payload."""


@dataclass(frozen=True)
class Workflow:
    owner: int
    parent: int


@dataclass
class Graph:
    parents: set[int] = field(default_factory=set)
    workflows: dict[int, Workflow] = field(default_factory=dict)
    receipts: set[int] = field(default_factory=set)


@dataclass(frozen=True)
class Change:
    table: str
    key: int
    value: Workflow | bool | None


def apply(graph, change):
    """A tiny keyed upsert/delete grammar, not arbitrary SQL."""
    result = deepcopy(graph)
    if type(change.key) is not int or change.key < 1:
        raise Rejected("invalid_key")
    if change.table == "workflow":
        if change.value is None:
            result.workflows.pop(change.key, None)
        elif isinstance(change.value, Workflow):
            if any(type(x) is not int or x < 1 for x in
                   (change.value.owner, change.value.parent)):
                raise Rejected("invalid_endpoint")
            result.workflows[change.key] = change.value
        else:
            raise Rejected("invalid_value")
    elif change.table in {"parent", "receipt"}:
        target = result.parents if change.table == "parent" else result.receipts
        if change.value is True:
            target.add(change.key)
        elif change.value is None:
            target.discard(change.key)
        else:
            raise Rejected("invalid_value")
    else:
        raise Rejected("unsupported_write")
    return result


def seeds(before, after, change):
    # Retain identities even when rows or intermediate edges are absent.
    if change.table == "parent":
        return {("parent", change.key)}
    result = {("workflow", change.key)}
    if change.table == "workflow":
        for state in (before, after):
            row = state.workflows.get(change.key)
            if row is not None:
                result.add(("parent", row.parent))
    return result


def closure(states, roots):
    """Union OLD/intermediate/NEW edges, including dangling parent identities."""
    adjacent = {}
    for state in states:
        for wid, row in state.workflows.items():
            w, p = ("workflow", wid), ("parent", row.parent)
            adjacent.setdefault(w, set()).add(p)
            adjacent.setdefault(p, set()).add(w)
    selected, pending = set(roots), list(roots)
    while pending:
        current = pending.pop()
        for neighbor in adjacent.get(current, ()):
            if neighbor not in selected:
                selected.add(neighbor)
                pending.append(neighbor)
    return frozenset(selected)


def plan(graph, changes):
    states, roots = [graph], set()
    for change in changes:
        after = apply(states[-1], change)
        roots.update(seeds(states[-1], after, change))
        states.append(after)
    return closure(states, roots)


def violations(graph):
    errors = []
    for wid in sorted(set(graph.workflows) | graph.receipts):
        row = graph.workflows.get(wid)
        if row is None:
            errors.append((wid, "receipt_without_workflow"))
        else:
            if row.parent not in graph.parents:
                errors.append((wid, "missing_parent"))
            if wid not in graph.receipts:
                errors.append((wid, "missing_receipt"))
    return errors


class Coordinator:
    def __init__(self, graph):
        self.graph = deepcopy(graph)
        self.locks = {}
        self.serial = 0

    def begin(self, *, preheld_incompatible_lock=False):
        self.serial += 1
        return Transaction(self, self.serial, preheld_incompatible_lock)


class Transaction:
    def __init__(self, db, ident, preheld):
        self.db, self.ident, self.preheld = db, ident, preheld
        self.keys = frozenset()
        self.admitted = False
        self.changes = []
        self.generation = 0
        self.dirty, self.validated = {}, {}
        self.immediate = False
        self.failed = self.closed = self.draining = False
        # Specification-only fatal latch. A real DB mapping that survives
        # savepoint rollback without caller spoofing remains an open gate.
        self.restart_required = False
        self.validation_count = 0
        self.savepoints, self.next_savepoint = [], 0

    def check_open(self):
        if self.restart_required:
            raise Rejected("whole_transaction_restart_required")
        if self.closed or self.failed:
            raise Rejected("transaction_unavailable")

    def fail(self, category):
        self.failed = True
        if category in {"scope_expanded_restart_transaction", "writer_busy",
                        "preheld_incompatible_lock"}:
            self.restart_required = True
        raise Rejected(category)

    def view(self):
        # Explicit fresh-committed-view assumption, not an MVCC implementation.
        graph = deepcopy(self.db.graph)
        for change in self.changes:
            graph = apply(graph, change)
        return graph

    def admit(self, changes, *, discovered=None):
        self.check_open()
        if self.admitted or self.changes:
            self.fail("admission_must_be_first")
        if self.preheld:
            self.fail("preheld_incompatible_lock")
        changes = tuple(changes)
        selected = plan(self.view(), changes) if discovered is None else discovered
        selected = frozenset(selected)
        # Atomic all-or-none abstraction. A real implementation must roll back
        # partial acquisitions; sorted locks alone do not prove nonblocking DML.
        if any(self.db.locks.get(key) not in (None, self.ident) for key in selected):
            self.fail("writer_busy")
        for key in sorted(selected):
            self.db.locks[key] = self.ident
        self.keys, self.admitted = selected, True
        if not plan(self.view(), changes) <= self.keys:
            self.fail("scope_expanded_restart_transaction")

    def execute(self, changes):
        self.check_open()
        if not self.admitted:
            self.fail("writer_not_admitted")
        changes = tuple(changes)
        required = plan(self.view(), changes)
        if not required <= self.keys:
            self.fail("scope_expanded_restart_transaction")
        self.changes.extend(changes)
        if changes:
            self.generation += 1
            for kind, ident in required:
                if kind == "workflow":
                    self.dirty[ident] = self.generation
        if self.immediate and not self.draining:
            self.validate()

    def validate(self, *, after_check=None, max_checks=32):
        self.check_open()
        self.draining = True
        visits = {}
        try:
            while True:
                pending = sorted(wid for wid, generation in self.dirty.items()
                                 if self.validated.get(wid) != generation)
                if not pending:
                    return
                wid = pending[0]
                if visits.get(wid, 0) >= max_checks:
                    self.fail("validation_rewrite_limit")
                generation = self.dirty[wid]
                errors = [error for error in violations(self.view()) if error[0] == wid]
                if errors:
                    self.fail(errors[0][1])
                self.validation_count += 1
                visits[wid] = visits.get(wid, 0) + 1
                if after_check is not None:
                    after_check(self, wid)
                if self.dirty[wid] == generation:
                    self.validated[wid] = generation
        finally:
            self.draining = False

    def set_immediate(self):
        self.check_open()
        self.validate()
        self.immediate = True

    def set_deferred(self):
        self.check_open()
        self.immediate = False

    def savepoint(self):
        self.check_open()
        self.next_savepoint += 1
        fields = (self.keys, self.admitted, self.changes, self.generation,
                  self.dirty, self.validated, self.immediate, self.validation_count)
        self.savepoints.append((self.next_savepoint, deepcopy(fields)))
        return self.next_savepoint

    def rollback_to(self, token):
        if self.restart_required:
            raise Rejected("whole_transaction_restart_required")
        if self.closed:
            raise Rejected("transaction_unavailable")
        for index, (saved_token, snapshot) in enumerate(self.savepoints):
            if saved_token == token:
                keep_keys = snapshot[0]
                for key in self.keys - keep_keys:
                    if self.db.locks.get(key) == self.ident:
                        del self.db.locks[key]
                (self.keys, self.admitted, self.changes, self.generation,
                 self.dirty, self.validated, self.immediate,
                 self.validation_count) = deepcopy(snapshot)
                self.savepoints = self.savepoints[:index + 1]
                self.failed = False
                return
        raise Rejected("unknown_savepoint")

    def rollback(self):
        for key in self.keys:
            if self.db.locks.get(key) == self.ident:
                del self.db.locks[key]
        self.changes.clear()
        self.closed = True

    def commit(self):
        self.check_open()
        try:
            self.validate()
            self.db.graph = self.view()
        except Rejected:
            self.rollback()
            raise
        self.rollback()

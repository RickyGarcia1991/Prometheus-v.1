"""Bounded non-project feedback receipts, isolated from legacy decision readers.

The schema has only exact receipt identities and intentional owner feedback.
This audit never restores a pending proposal, revision token or execution grant.
"""
from .decision_history import DecisionHistory


class CapabilityChangeRequests(DecisionHistory):
    _capability_only = True

    def __init__(self, history):
        self.root = history.root/'CapabilityChangeRequests'
        self.path = self.root/'decisions.sqlite3'
        self._plain()

    def record(self, event):
        return self._record(event)

    def read(self):
        return self._read_rows()

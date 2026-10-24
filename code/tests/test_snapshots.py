"""Which snapshots may prove that a subject's record is absent.   python -m unittest discover -s code/tests -v

A snapshot describes the batch it was taken for. Its silence about a subject proves absence only if it is that subject's batch
(it holds other records of the subject), or its coverage metadata declares system-wide scope; an unrelated batch proves nothing.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghost.state import EvidenceView  # noqa: E402
from test_temporal import AUDIT, CAP, REQ, fact, pipe  # noqa: E402

REQUEST = {"audit_at": AUDIT, "requested_at": REQ}
SNAP_AT = "2026-09-15T11:00:00Z"


def make_view(facts, snaps):
    """snaps: (file_id, system, scope or None)."""
    ev = [{"file_id": f, "captured_at": c} for f, c in CAP.items()]
    cov = [{"file_id": f, "system": s, "collection": "x", "obs_at": SNAP_AT, "n": 0, "kind": "snapshot", "scope": sc}
           for f, s, sc in snaps]
    return EvidenceView(facts, ev, cov)


def state(facts, snaps, subject_files, rid="search_9"):
    return pipe(make_view(facts, snaps), facts).resource_state(rid, REQUEST, set(), subject_files)


# the subject's batch: another of their resources is observed in file "early"
BATCH = [fact("early", "search_2", "2026-09-14T10:00:00Z", email="a@x.org")]


class SnapshotEligibility(unittest.TestCase):
    def test_snapshot_of_the_subjects_batch_proves_absence(self):
        st = state(BATCH, [("early", "search_service", None)], {"early"})
        self.assertEqual((st.presence, st.evidence_id), ("ABSENT", "early"))

    def test_declared_full_system_snapshot_proves_absence(self):
        st = state(BATCH, [("snap", "search_service", "system")], {"early"})
        self.assertEqual((st.presence, st.evidence_id), ("ABSENT", "snap"))

    def test_unrelated_batch_snapshot_alone_does_not_prove_absence(self):
        st = state(BATCH, [("snap", "search_service", None)], {"early"})      # "snap" holds none of the subject's records
        self.assertEqual((st.presence, st.evidence_id), ("UNKNOWN", ""))
        self.assertTrue(st.unresolved)

    def test_late_ledger_identifying_the_batch_without_an_eligible_snapshot_stays_unknown(self):
        # the subject's batch is only known from a later-captured ledger; the only citable snapshot belongs to someone else
        late = [fact("late", "search_2", "2026-09-14T10:00:00Z", email="a@x.org")]
        st = state(late, [("snap", "search_service", None), ("late", "search_service", None)], {"late"})
        self.assertEqual(st.presence, "UNKNOWN")                               # "late" is not citable, "snap" is not theirs
        self.assertEqual(st.evidence_id, "")

    def test_snapshot_of_another_system_never_counts(self):
        st = state(BATCH, [("early", "account_service", None)], {"early"})
        self.assertEqual(st.presence, "UNKNOWN")

    def test_snapshot_captured_after_audit_never_counts_even_if_declared_system_wide(self):
        st = state(BATCH, [("late", "search_service", "system")], {"early"})
        self.assertEqual(st.presence, "UNKNOWN")

    def test_subject_with_no_observation_anywhere_uses_the_systems_window_snapshot(self):
        # the shape of public samples case_01/case_02: no batch to compare against, so the in-window system snapshot is used
        st = state([], [("snap", "search_service", None)], set())
        self.assertEqual((st.presence, st.evidence_id), ("ABSENT", "snap"))

    def test_supersession_needs_the_same_batch_or_declared_scope(self):
        f = [fact("early", "search_1", "2026-09-14T10:00:00Z", email="a@x.org")]
        p = lambda snaps: pipe(make_view(f, snaps), f)    # noqa: E731
        self.assertIs(p([("snap", "search_service", None)]).resource_state("search_1", REQUEST, set(), {"snap"}).present, False)
        self.assertTrue(p([("snap", "search_service", None)]).resource_state("search_1", REQUEST, set(), {"early"}).present)
        self.assertIs(p([("snap", "search_service", "system")]).resource_state("search_1", REQUEST, set(), {"early"}).present, False)
        self.assertTrue(p([("snap", "search_service", None)]).resource_state("search_1", REQUEST, set(), None).present)


if __name__ == "__main__":
    unittest.main()

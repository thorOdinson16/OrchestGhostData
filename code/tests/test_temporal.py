"""Regression tests for temporal evidence eligibility.   python -m unittest discover -s code/tests -v

Rules under test: only files captured at or before audit_at may determine state or be cited; observations that exist
only in later-captured files may still be used to discover which resources belong to a subject; missing citable
evidence means "unresolved", never "absent"; a newer citable full snapshot of the subject's batch supersedes older
observations.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghost.identity import Subject  # noqa: E402
from ghost.mapping import Fact  # noqa: E402
from ghost.pipeline import Pipeline  # noqa: E402
from ghost.planner import build_steps  # noqa: E402
from ghost.policy import PolicyEngine  # noqa: E402
from ghost.state import EvidenceView  # noqa: E402

AUDIT = "2026-09-15T12:00:00Z"
REQ = "2026-09-01T00:00:00Z"
CAP = {"early": "2026-09-14T10:00:00Z", "snap": "2026-09-15T11:00:00Z", "late": "2026-09-17T10:00:00Z",
       "other": "2026-09-15T11:30:00Z"}


def fact(fid, rid, obs, exists=1, rtype="SEARCH_INDEX_ENTRY", email=None, user=None, state=None, loc="line1"):
    return Fact(0, fid, obs, None, rid, rtype, None, None, exists, state, email, None, None, None, user, {}, loc)


def view(facts, snapshots=()):
    ev = [{"file_id": f, "captured_at": c} for f, c in CAP.items()]
    cov = [{"file_id": f, "system": s, "collection": "x", "obs_at": at, "n": 0, "kind": "snapshot"} for f, s, at in snapshots]
    return EvidenceView(facts, ev, cov)


def pipe(v, facts):
    p = Pipeline.__new__(Pipeline)
    p.view, p.facts, p.policy = v, facts, PolicyEngine()
    p.act = type("A", (), {"in_window": lambda self, rid, rep: None})()
    return p


class TemporalEligibility(unittest.TestCase):
    def test_late_evidence_never_sets_state_or_citation(self):
        f = [fact("late", "search_1", "2026-09-15T09:00:00Z", email="a@x.org")]   # captured after audit, observed before
        v = view(f)
        self.assertEqual(v.state_facts("search_1", AUDIT, REQ), ([], None))
        st = pipe(v, f).resource_state("search_1", {"audit_at": AUDIT, "requested_at": REQ}, set())
        self.assertIsNone(st.present)               # unknown, not "absent" and not "present"
        self.assertTrue(st.unresolved)
        self.assertEqual(st.evidence_id, "")
        self.assertFalse(PolicyEngine().decide(st).violated)

    def test_mixed_evidence_uses_only_the_citable_file(self):
        f = [fact("early", "search_1", "2026-09-14T10:00:00Z", exists=0, state="NOT_FOUND"),
             fact("late", "search_1", "2026-09-15T10:00:00Z", exists=1, email="a@x.org")]
        st = pipe(view(f), f).resource_state("search_1", {"audit_at": AUDIT, "requested_at": REQ}, set())
        self.assertIs(st.present, False)
        self.assertEqual(st.evidence_id, "early")

    def test_unknown_state_is_not_absence_and_not_cited_in_complete(self):
        f = []
        st = pipe(view(f), f).resource_state("search_9", {"audit_at": AUDIT, "requested_at": REQ}, set())
        self.assertIsNone(st.present)
        self.assertTrue(st.unresolved)
        t = PolicyEngine().decide(st)
        self.assertFalse(t.violated)
        self.assertEqual(build_steps([t], True), [])            # nothing citable -> no COMPLETE verification step
        self.assertEqual(len(build_steps([t], False)), 1)       # future verification of an INCOMPLETE case is still planned

    def test_absence_requires_a_citable_full_snapshot(self):
        f = []
        snaps = [("snap", "search_service", "2026-09-15T11:00:00Z")]
        st = pipe(view(f, snaps), f).resource_state("search_9", {"audit_at": AUDIT, "requested_at": REQ}, set())
        self.assertIs(st.present, False)
        self.assertEqual(st.evidence_id, "snap")
        late_only = [("late", "search_service", "2026-09-15T11:00:00Z")]   # snapshot file captured after audit_at
        st = pipe(view(f, late_only), f).resource_state("search_9", {"audit_at": AUDIT, "requested_at": REQ}, set())
        self.assertIsNone(st.present)

    def test_superseding_snapshot(self):
        f = [fact("early", "search_1", "2026-09-14T10:00:00Z", email="a@x.org")]
        snaps = [("snap", "search_service", "2026-09-15T11:00:00Z")]
        req = {"audit_at": AUDIT, "requested_at": REQ}
        p = pipe(view(f, snaps), f)
        self.assertTrue(p.resource_state("search_1", req, set(), {"snap"}).present is False)    # same batch -> gone
        self.assertTrue(p.resource_state("search_1", req, set(), {"other"}).present)             # other batch -> no proof
        late = [("late", "search_service", "2026-09-15T11:00:00Z")]
        p = pipe(view(f, late), f)
        self.assertTrue(p.resource_state("search_1", req, set(), {"late"}).present)              # ineligible snapshot

    def test_discovery_still_uses_later_captured_files(self):
        s = Subject(user_id="user_1", emails={"a@x.org"}, current_email="a@x.org")
        f = [fact("late", "audit_record_5", "2026-09-15T09:00:00Z", rtype="SECURITY_AUDIT_LOG", user="user_1"),
             fact("late", "search_7", "2026-09-16T09:00:00Z", email="a@x.org")]            # observed after audit_at
        found = pipe(view(f), f).linked_resources(s, {"requested_at": REQ, "audit_at": AUDIT, "reported_at": REQ})
        self.assertIn("audit_record_5", found)
        self.assertNotIn("search_7", found)


if __name__ == "__main__":
    unittest.main()

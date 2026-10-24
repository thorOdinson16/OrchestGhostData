"""End-to-end completion semantics: an open mandatory check can never produce COMPLETE.   python -m unittest discover -s code/tests -v

Each scenario goes through Pipeline.run -> build_case -> planner, so the verdict, not just the treatment, is asserted.
Unknown (no citable observation) is neither absent nor a violation: it is an open check, so the case is INCOMPLETE with the
check planned as future verification (empty evidence_id) and no finding or remediation (nothing proves a violation).
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))

import ghost.pipeline as gp  # noqa: E402
from ghost.identity import Subject  # noqa: E402
from ghost.policy import UNKNOWN, PolicyEngine  # noqa: E402
from ghost.state import ResourceState  # noqa: E402
from test_temporal import AUDIT, REQ, fact, pipe, view  # noqa: E402

REPORTED = "2026-09-10T12:00:00Z"
REQUEST = {"case_id": "case_1", "email": "me@x.org", "requested_at": REQ, "audit_at": AUDIT, "reported_at": REPORTED,
           "reported_status": "COMPLETE", "request_text": "delete me"}
ME = Subject(user_id="user_1", emails={"me@x.org"}, current_email="me@x.org")


class _Ids:
    def __init__(self, subject):
        self.subject = subject

    def resolve(self, email, as_of=None):
        return self.subject

    def why_unresolved(self, email, as_of=None):
        return "stub"

    def candidates(self, email):
        return []


class _Act:
    rows = []
    by_res = {}

    def in_window(self, rid, rep):
        return None

    def case_events(self, rid):
        return []


def run_case(facts, linked, subject=ME, snapshots=()):
    """Run one DELETE case whose linked resources are `linked` (rid -> link kinds) over the injected evidence."""
    p = pipe(view(facts, snapshots), facts)
    p.ids, p.act, p.unresolved_identities = _Ids(subject), _Act(), {}
    with mock.patch.object(p, "linked_resources", return_value=dict(linked)):
        res = p.run([REQUEST], {"case_1": {"intent": "DELETE"}})
    return p, res["case_1"]


def keys(steps):
    return [(s["phase"], s["resource_id"], s["operation"], bool(s["evidence_id"])) for s in steps]


class OpenChecksBlockComplete(unittest.TestCase):
    def test_control_fully_known_compliant_case_is_still_complete(self):
        f = [fact("early", "account_1", "2026-09-14T10:00:00Z", exists=0, rtype="USER_PROFILE", state="NOT_FOUND")]
        _, (status, steps, _) = run_case(f, {"account_1": {"email"}})
        self.assertEqual(status, "COMPLETE")
        self.assertEqual(keys(steps), [("VERIFICATION", "account_1", "VERIFY_RECORD_ABSENT", True)])

    def test_unknown_user_profile_state_is_incomplete_not_complete(self):
        _, (status, steps, states) = run_case([], {"account_1": {"email"}})        # linked, but never observed
        self.assertIsNone(states[0].present)
        self.assertEqual(states[0].presence, "UNKNOWN")
        self.assertEqual(status, "INCOMPLETE")
        self.assertEqual(keys(steps), [("VERIFICATION", "account_1", "VERIFY_RECORD_ABSENT", False)])   # no finding invented

    def test_unknown_data_flow_state_is_incomplete_not_complete(self):
        _, (status, steps, states) = run_case([], {"data_flow_1": {"user_ref"}})
        self.assertIsNone(states[0].active)
        self.assertEqual(status, "INCOMPLETE")
        self.assertEqual(keys(steps), [("VERIFICATION", "data_flow_1", "VERIFY_DATA_FLOW_DISABLED", False)])

    def test_explicitly_inactive_data_flow_does_not_block(self):
        f = [fact("early", "data_flow_1", "2026-09-14T10:00:00Z", rtype="DATA_FLOW", state="DISABLED")]
        _, (status, steps, _) = run_case(f, {"data_flow_1": {"user_ref"}})
        self.assertEqual(status, "COMPLETE")

    def test_explicitly_active_data_flow_is_a_finding(self):
        f = [fact("early", "data_flow_1", "2026-09-14T10:00:00Z", rtype="DATA_FLOW", state="ACTIVE")]
        _, (status, steps, _) = run_case(f, {"data_flow_1": {"user_ref"}})
        self.assertEqual(status, "INCOMPLETE")
        self.assertEqual(steps[0]["operation"], "REPORT_ACTIVE_DATA_FLOW")

    def test_unknown_policy_condition_is_incomplete_not_complete(self):
        # an audit record seen only in a later-captured file: discovered, but its retention condition cannot be evaluated
        f = [fact("late", "audit_record_1", "2026-09-14T10:00:00Z", rtype="SECURITY_AUDIT_LOG", user="user_1")]
        _, (status, steps, states) = run_case(f, {"audit_record_1": {"user_ref"}})
        self.assertEqual(status, "INCOMPLETE")
        self.assertEqual(keys(steps), [("VERIFICATION", "audit_record_1", "VERIFY_RECORD_PRESENT", False)])

    def test_unresolved_subject_is_never_complete(self):
        p, (status, steps, _) = run_case([], {}, subject=None)
        self.assertEqual((status, steps), ("INCOMPLETE", []))
        self.assertIn("subject", p.unresolved_identities["case_1"])

    def test_not_applicable_is_unchanged(self):
        p = pipe(view([]), [])
        p.ids, p.act, p.unresolved_identities = _Ids(None), _Act(), {}
        res = p.run([REQUEST], {"case_1": {"intent": "NOT_DELETE"}})
        self.assertEqual(res["case_1"], ("NOT_APPLICABLE", [], []))
        self.assertEqual(p.unresolved_identities, {})                 # no identity work for a non-deletion request

    def test_unattributed_resource_blocks_complete_only_when_the_switch_is_on(self):
        f = [fact("early", "account_1", "2026-09-14T10:00:00Z", exists=0, rtype="USER_PROFILE", state="NOT_FOUND")]
        with mock.patch.object(gp.Pipeline, "assign_null_accounts", lambda self, pc, other=(): self.unresolved_identities.setdefault(
                "case_1", {}).update(account={"reason": "ambiguous", "candidates": ["account_9"]}) or {}):
            p, (status, _, _) = run_case(f, {"account_1": {"email"}})
            self.assertEqual(status, "COMPLETE")                       # default: the samples' reference verdicts require this
            self.assertEqual(p.complete_with_open_identity, ["case_1"])  # ... but it is never silent
            with mock.patch.object(gp, "UNRESOLVED_RESOURCE_BLOCKS_COMPLETE", True):
                _, (status, _, _) = run_case(f, {"account_1": {"email"}})
                self.assertEqual(status, "INCOMPLETE")


class TreatmentUncertainty(unittest.TestCase):
    def st(self, cat, **kw):
        return ResourceState("r_1", "sys", cat, **kw)

    def test_unknown_presence_is_neither_violation_nor_compliance(self):
        t = PolicyEngine().decide(self.st("USER_PROFILE", present=None))
        self.assertEqual((t.violated, t.uncertain, t.undetermined), (False, True, True))
        for present, violated in ((True, True), (False, False)):
            t = PolicyEngine().decide(self.st("USER_PROFILE", present=present))
            self.assertEqual((t.violated, t.uncertain), (violated, False))

    def test_unobserved_redact_target_is_uncertain_but_observed_clean_one_is_not(self):
        e = PolicyEngine()
        ctx = {"conversation": True}
        self.assertTrue(e.decide(self.st("SUPPORT_MESSAGE", present=None), ctx).uncertain)
        self.assertFalse(e.decide(self.st("SUPPORT_MESSAGE", present=True, evidence_id="e1"), ctx).undetermined)
        self.assertTrue(e.decide(self.st("SUPPORT_MESSAGE", present=True, identified=True, evidence_id="e1"), ctx).violated)

    def test_unknown_data_flow_activity_is_uncertain(self):
        e = PolicyEngine()
        self.assertTrue(e.decide(self.st("DATA_FLOW", active=None)).uncertain)
        self.assertFalse(e.decide(self.st("DATA_FLOW", active=False)).undetermined)
        self.assertTrue(e.decide(self.st("DATA_FLOW", active=True)).violated)

    def test_unresolved_condition_has_no_finding_but_keeps_its_check(self):
        t = PolicyEngine().decide(self.st("SECURITY_AUDIT_LOG", present=None))
        self.assertEqual((t.condition, t.unresolved, t.violated, t.verify_op), (UNKNOWN, True, False, "VERIFY_RECORD_PRESENT"))


if __name__ == "__main__":
    unittest.main()

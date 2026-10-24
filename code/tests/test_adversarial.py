"""Adversarial and cross-component tests: the safeguards of temporal eligibility, conditional policy, conservative identity
matching and record-level document association must hold TOGETHER.   python -m unittest discover -s code/tests -v

* TestAdversarialDataset runs the whole pipeline (in a subprocess, on a synthetic dataset) against hand-derived answers.
* The other classes combine several failure modes on injected facts.
Expectations are derived from the project rules, never from pipeline output.
"""
import csv
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))

from ghost.docs import doc_facts, pdf_facts  # noqa: E402
from ghost.identity import Subject  # noqa: E402
from ghost.slots import PHASE_POINTS, solve_group  # noqa: E402
from test_identity import case, event  # noqa: E402
from test_temporal import AUDIT, REQ, fact, pipe, view  # noqa: E402

ADV = TESTS.parent / "evaluation" / "adversarial"
KEY = lambda s: (s["phase"], s["system_id"], s["resource_id"], s["operation"], s["state"], s["evidence_id"])  # noqa: E731


class TestAdversarialDataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        proc = subprocess.run([sys.executable, "-X", "utf8", str(ADV / "run_adversarial.py")], capture_output=True, text=True)
        if proc.returncode != 0:
            raise unittest.SkipTest("adversarial dataset could not be built: " + proc.stderr[-300:])
        cls.out = json.loads(proc.stdout)
        with open(ADV / "expected_output.csv", encoding="utf-8", newline="") as f:
            cls.expected = {r["case_id"]: r for r in csv.DictReader(f)}

    def test_every_case_matches_its_hand_derived_answer(self):
        for cid, exp in self.expected.items():
            got = self.out["cases"][cid]
            self.assertEqual(got["status"], exp["deletion_status"], cid)
            self.assertEqual([KEY(s) for s in got["steps"]], [KEY(s) for s in json.loads(exp["steps_json"])], cid)

    def test_later_captured_ledger_cannot_change_state_or_be_cited(self):
        a1 = self.out["cases"]["case_a1"]
        self.assertEqual(a1["status"], "INCOMPLETE")                        # the late ledger says the search entry is gone
        cited = {s["evidence_id"] for c in self.out["cases"].values() for s in c["steps"] if s["evidence_id"]}
        self.assertEqual(cited, {"evidence_a1"})                             # evidence_a2 is never cited
        for cid, c in self.out["cases"].items():
            for s in c["steps"]:
                if s["evidence_id"]:
                    self.assertLessEqual(self.out["captured"][s["evidence_id"]], self.out["audit_at"][cid])

    def test_late_only_records_are_discovered_but_asserted_nowhere_except_as_uncited_checks(self):
        a1 = {s["resource_id"]: s for s in self.out["cases"]["case_a1"]["steps"]}
        self.assertEqual(a1["audit_record_1002"]["evidence_id"], "")                      # discovered, not cited
        self.assertEqual(a1["audit_record_1002"]["phase"], "VERIFICATION")
        a2 = self.out["cases"]["case_a2"]
        self.assertEqual(a2["status"], "INCOMPLETE")                    # an open (UNKNOWN) mandatory check blocks COMPLETE
        late = [s for s in a2["steps"] if s["resource_id"] in ("audit_record_2003", "legal_hold_2004")]
        self.assertEqual(len(late), 2)
        self.assertTrue(all(s["phase"] == "VERIFICATION" and s["evidence_id"] == "" for s in late))   # future checks, never findings

    def test_ambiguous_missing_and_decoy_account_events_leave_no_account_step(self):
        for n in (3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15):
            cid = f"case_a{n}"
            self.assertIn("account", self.out["unresolved"][cid], cid)
            self.assertFalse(any(s["system_id"] == "account_service" for s in self.out["cases"][cid]["steps"]), cid)
            self.assertFalse(any(k.startswith("account_") for k in self.out["links"][cid]), cid)
        self.assertIn("fewer account events", self.out["unresolved"]["case_a7"]["account"]["reason"])
        self.assertIn("slot structure", self.out["unresolved"]["case_a10"]["account"]["reason"])

    def test_wide_window_match_is_tagged_and_only_taken_when_unique(self):
        self.assertEqual(self.out["links"]["case_a5"]["account_5000"], ["activity", "wide_window"])
        self.assertIn("account_5000", {s["resource_id"] for s in self.out["cases"]["case_a5"]["steps"]})
        self.assertNotIn("account_6000", self.out["links"]["case_a6"])

    def test_output_integrity_for_every_case(self):
        for cid, c in self.out["cases"].items():
            phases = [s["phase"] for s in c["steps"]]
            self.assertEqual(phases, sorted(phases, key=["FINDING", "REMEDIATION", "VERIFICATION"].index), cid)
            self.assertEqual([s["step_no"] for s in c["steps"]], list(range(1, len(phases) + 1)), cid)
            if c["status"] == "COMPLETE":
                self.assertTrue(all(s["phase"] == "VERIFICATION" and s["evidence_id"] for s in c["steps"]), cid)
            for s in c["steps"]:
                if s["phase"] == "REMEDIATION" or (c["status"] == "INCOMPLETE" and s["phase"] == "VERIFICATION"):
                    self.assertEqual(s["evidence_id"], "", cid)
                if s["phase"] == "FINDING":
                    self.assertTrue(s["evidence_id"], cid)
            found = {s["resource_id"] for s in c["steps"] if s["phase"] == "FINDING"}
            remediated = {s["resource_id"] for s in c["steps"] if s["phase"] == "REMEDIATION"}
            self.assertEqual(found, remediated, cid)                     # every remediation answers a cited finding


class TestCrossComponentInjected(unittest.TestCase):
    """Two or more failure modes on the same resource or case."""

    def test_late_capture_plus_unknown_policy_condition_plus_unresolved_identity(self):
        p = pipe(view([fact("late", "audit_record_9", "2026-09-15T09:00:00Z", rtype="SECURITY_AUDIT_LOG", user="user_1")]), [])
        p.facts = p.view.by_res["audit_record_9"]
        p.view = view(p.facts)
        req = {"requested_at": REQ, "audit_at": AUDIT, "reported_at": REQ}
        found = p.linked_resources(Subject(user_id="user_1"), req)
        self.assertIn("audit_record_9", found)                                   # permitted discovery
        st = p.resource_state("audit_record_9", req, found["audit_record_9"], None, Subject(user_id="user_1"))
        t = p.policy.decide(st)
        self.assertEqual((st.presence, st.evidence_id, t.unresolved, t.condition), ("UNKNOWN", "", True, "UNKNOWN"))
        from ghost.planner import build_steps
        self.assertEqual(build_steps([t], True), [])                              # nothing asserted in a COMPLETE row
        self.assertEqual([(s["phase"], s["evidence_id"]) for s in build_steps([t], False)], [("VERIFICATION", "")])

    def test_late_file_cannot_supply_policy_inputs_for_a_citable_record(self):
        # a citable observation of the audit record exists, a later file adds a retention date and a legal-hold end date
        early = fact("early", "audit_record_9", "2026-09-14T10:00:00Z", rtype="SECURITY_AUDIT_LOG", user="user_1")
        late = fact("late", "audit_record_9", "2026-09-15T09:00:00Z", rtype="SECURITY_AUDIT_LOG", user="user_1")
        late.attrs["retention_until"] = "2020-01-01T00:00:00Z"                   # a date that would make the condition FALSE
        facts = [early, late]
        p = pipe(view(facts), facts)
        st = p.resource_state("audit_record_9", {"audit_at": AUDIT, "requested_at": REQ}, set(), None, Subject(user_id="user_1"))
        self.assertIsNone(st.context["retention_until"])
        self.assertEqual(p.policy.decide(st).condition, "TRUE")                  # decided from the citable observation only
        self.assertEqual(st.evidence_id, "early")

    def test_contradictory_signals_class_for_one_event_phase_for_the_other(self):
        cases = [case("case_1", "COMPLETE"), case("case_2", "NOT_STARTED", "2026-09-03T12:00:00Z")]
        events = [event("account_a", 70, "FAILED"), event("account_b", 50, "SUCCESS")]
        # class prefers case_1->b, case_2->a; the cases' other events agree with the opposite pairing
        phase = lambda cid, ev: 2 * PHASE_POINTS if (cid, ev["resource_id"]) in {("case_1", "account_a"), ("case_2", "account_b")} else 0  # noqa: E731
        res, un = solve_group(cases, events, bonus=phase, checkable=lambda cid: 2)
        self.assertEqual(res, {})                                                 # nothing is forced when signals disagree

    def test_known_account_outside_the_slot_bands_does_not_rescue_an_unfit_pool(self):
        cases = [case("case_1"), case("case_2", requested="2026-09-03T12:00:00Z")]
        events = [event("account_a", 52), event("account_b", 50)]                  # both in the second band
        res, un = solve_group(cases, events, known={"case_1": "account_a"}, bonus=lambda cid, ev: 2 * PHASE_POINTS)
        self.assertEqual(res, {})
        self.assertIn("slot structure", un["case_2"]["reason"])

    def test_wide_window_never_overrides_a_pool_level_ambiguity(self):
        # pool events at -70/-50 stay ambiguous even though a third unclaimed event sits near reported_at
        from ghost.pipeline import Pipeline
        from test_identity import _Act
        events = [event("account_a", 70), event("account_b", 50), event("account_c", 5)]
        p = Pipeline.__new__(Pipeline)
        p.act, p.unresolved_identities = _Act(events), {}
        pc = {c["case_id"]: {"req": c, "subject": None, "linked": {}} for c in (case("case_1"), case("case_2", requested="2026-09-03T12:00:00Z"))}
        assigned = p.assign_null_accounts(pc)
        # the wide-window event is the single unclaimed candidate of BOTH cases: iteration order must not decide for them
        self.assertEqual(assigned, {})
        self.assertEqual({c: p.unresolved_identities[c]["account"]["candidates"] for c in pc}, {"case_1": ["account_c"], "case_2": ["account_c"]})
        self.assertIn("another case", p.unresolved_identities["case_1"]["account"]["reason"])
        del pc["case_2"]                                                            # a lone case may take it
        p.unresolved_identities = {}
        self.assertEqual(p.assign_null_accounts(pc), {"case_1": "account_c"})


class TestDocumentCombinations(unittest.TestCase):
    ev_rows = [{"file_id": "pdf_early", "captured_at": "2026-09-14T10:00:00Z"},
               {"file_id": "pdf_late", "captured_at": "2026-09-17T10:00:00Z"}]
    raw = ("REGISTER\nRecord\naccount_1\nDetail\nana@x.org\nDetail\nuser_1\n"
           "Record\nsearch_2\nDetail\nbo@x.org\nDetail\nuser_2\n")

    def facts(self):
        docs = [{"file_id": "pdf_early", "kind": "pdf", "content": self.raw}, {"file_id": "pdf_late", "kind": "pdf", "content": self.raw}]
        with mock.patch("ghost.docs._flags", return_value={}):
            return doc_facts([], docs, self.ev_rows)

    def test_two_people_in_one_document_each_discover_only_their_own_resource(self):
        facts = self.facts()
        p = pipe(view(facts), facts)
        req = {"requested_at": REQ, "audit_at": AUDIT, "reported_at": REQ}
        one = p.linked_resources(Subject(user_id="user_1", emails={"ana@x.org"}, current_email="ana@x.org"), req)
        two = p.linked_resources(Subject(user_id="user_2", emails={"bo@x.org"}, current_email="bo@x.org"), req)
        self.assertEqual((set(one), set(two)), ({"account_1"}, {"search_2"}))

    def test_later_captured_copy_of_the_document_adds_nothing(self):
        facts = [f for f in self.facts() if f.file_id == "pdf_late"]
        p = pipe(view(facts), facts)
        req = {"requested_at": REQ, "audit_at": AUDIT, "reported_at": REQ}
        self.assertEqual(p.linked_resources(Subject(user_id="user_1", emails={"ana@x.org"}, current_email="ana@x.org"), req), {})
        st = p.resource_state("account_1", {"audit_at": AUDIT, "requested_at": REQ}, set())
        self.assertEqual((st.presence, st.evidence_id), ("UNKNOWN", ""))

    def test_ambiguous_document_owner_never_links_and_cannot_be_rescued_by_a_matching_email(self):
        raw = "Record\naccount_1 search_2\nDetail\nana@x.org\n"                       # two resources, one email
        docs = [{"file_id": "pdf_early", "kind": "pdf", "content": raw}]
        with mock.patch("ghost.docs._flags", return_value={}):
            facts = pdf_facts(docs, self.ev_rows, 1000)
        self.assertEqual(facts, [])
        p = pipe(view(facts), facts)
        req = {"requested_at": REQ, "audit_at": AUDIT, "reported_at": REQ}
        self.assertEqual(p.linked_resources(Subject(user_id="user_1", emails={"ana@x.org"}, current_email="ana@x.org"), req), {})

    def test_misleading_display_reference_in_a_register_does_not_become_evidence(self):
        register = {"file_id": "pdf_early", "kind": "pdf", "content": "ORD-1 | user_08 | x\nORD-3 | user_17 | y\n"}
        link = {"rid": "tombstone_17", "disp": "user_17", "email": "x@x.org", "user": "user_17", "activity_id": "a", "t": __import__("datetime").datetime(2026, 9, 14, 10, 7)}
        with mock.patch("ghost.docs._flags", return_value={}):
            self.assertEqual(doc_facts([link], [register], self.ev_rows), [])


if __name__ == "__main__":
    unittest.main()

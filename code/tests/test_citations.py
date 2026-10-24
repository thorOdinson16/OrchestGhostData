"""Negative and positive tests of the citation checks (evaluation/citations.py).   python -m unittest discover -s code/tests -v"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evaluation"))

from citations import check_run, format_problems, support_problems  # noqa: E402
from test_snapshots import make_view  # noqa: E402
from test_temporal import AUDIT, REQ, fact  # noqa: E402

OBS = "2026-09-14T10:00:00Z"
SYS = "search_service"
STATE = {"REPORT_REMAINING_RECORD": "PRESENT", "VERIFY_RECORD_ABSENT": "ABSENT", "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
         "VERIFY_DATA_FLOW_DISABLED": "DISABLED", "VERIFY_RECORD_REDACTED": "REDACTED", "REPORT_EXPOSED_PII": "PRESENT"}


def step(op, rid="search_1", evidence="early", system=SYS, phase="FINDING"):
    return {"step_no": 1, "phase": phase, "system_id": system, "resource_id": rid, "operation": op, "state": STATE[op],
            "evidence_id": evidence}


def problems(facts, snaps, st, subject_files=()):
    v = make_view(facts, snaps)
    return format_problems(v, st, AUDIT) or support_problems(v, st, REQ, AUDIT, set(subject_files))


PRESENT = [fact("early", "search_1", OBS, email="a@x.org")]
GONE = [fact("early", "search_1", OBS, exists=0, state="NOT_FOUND")]
VERIFY = {"phase": "VERIFICATION"}


class Format(unittest.TestCase):
    def test_unknown_file_id(self):
        self.assertEqual(problems(PRESENT, [], step("REPORT_REMAINING_RECORD", evidence="evidence_999")),
                         ["cited file is not in the manifest"])

    def test_file_captured_after_the_audit_is_rejected_even_if_it_observes_the_resource(self):
        late = [fact("late", "search_1", OBS, email="a@x.org")]
        self.assertEqual(problems(late, [], step("REPORT_REMAINING_RECORD", evidence="late")), ["cited file captured after audit_at"])


class Support(unittest.TestCase):
    def test_valid_citations_pass(self):
        self.assertEqual(problems(PRESENT, [], step("REPORT_REMAINING_RECORD")), [])
        self.assertEqual(problems(GONE, [], step("VERIFY_RECORD_ABSENT", **VERIFY)), [])
        # a snapshot of the subject's own batch that omits the record
        self.assertEqual(problems(PRESENT, [("early", SYS, None)], step("VERIFY_RECORD_ABSENT", rid="search_9", **VERIFY), {"early"}), [])

    def test_structurally_valid_but_irrelevant_file(self):
        other = [fact("early", "account_7", OBS, email="b@x.org")]       # a real, citable file about something else
        self.assertIn("neither observes", problems(other, [], step("REPORT_REMAINING_RECORD"))[0])

    def test_file_that_observes_a_different_resource(self):
        other = [fact("early", "search_2", OBS, email="a@x.org")]
        self.assertIn("neither observes", problems(other, [], step("REPORT_REMAINING_RECORD", rid="search_1"))[0])

    def test_observation_outside_the_case_window_does_not_count(self):
        stale = [fact("early", "search_1", "2026-08-01T00:00:00Z", email="a@x.org")]      # before requested_at
        self.assertIn("neither observes", problems(stale, [], step("REPORT_REMAINING_RECORD"))[0])

    def test_absence_cannot_rest_on_a_file_that_shows_the_record(self):
        self.assertIn("present", problems(PRESENT, [], step("VERIFY_RECORD_ABSENT", **VERIFY))[0])

    def test_absence_cannot_rest_on_a_snapshot_of_another_system(self):
        why = problems([], [("early", "account_service", None)], step("VERIFY_RECORD_ABSENT", **VERIFY), {"early"})
        self.assertIn("neither observes", why[0])

    def test_absence_cannot_rest_on_a_snapshot_that_does_not_speak_for_the_subject(self):
        st = step("VERIFY_RECORD_ABSENT", evidence="snap", **VERIFY)
        self.assertIn("does not speak for the subject", problems([], [("snap", SYS, None)], st, {"early"})[0])
        # ... unless its metadata declares system-wide scope, or the subject has no other records at all
        self.assertEqual(problems([], [("snap", SYS, "system")], st, {"early"}), [])
        self.assertEqual(problems([], [("snap", SYS, None)], st, set()), [])

    def test_a_snapshot_outside_the_case_window_does_not_cover_it(self):
        v = make_view([], [("snap", SYS, None)])
        v.snapshots = [(s, f, "2026-08-01T00:00:00Z") for s, f, _ in v.snapshots]
        st = step("VERIFY_RECORD_ABSENT", evidence="snap", **VERIFY)
        self.assertTrue(support_problems(v, st, REQ, AUDIT, set()))

    def test_claim_must_match_what_the_observation_shows(self):
        self.assertIn("does not show the claimed state", problems(GONE, [], step("REPORT_REMAINING_RECORD"))[0])   # absence backs a finding
        self.assertIn("does not show", problems(PRESENT, [], step("REPORT_ACTIVE_DATA_FLOW"))[0])
        no_identifier = [fact("early", "search_1", OBS)]
        self.assertIn("does not show", problems(no_identifier, [], step("REPORT_EXPOSED_PII"))[0])
        self.assertEqual(problems(PRESENT, [], step("REPORT_EXPOSED_PII")), [])

    def test_finding_needs_an_observation_not_just_a_snapshot(self):
        self.assertIn("no observation", problems([], [("early", SYS, None)], step("REPORT_REMAINING_RECORD"), {"early"})[0])

    def test_active_flow_and_disabled_flow(self):
        on = [fact("early", "data_flow_1", OBS, rtype="DATA_FLOW", state="ACTIVE")]
        off = [fact("early", "data_flow_1", OBS, rtype="DATA_FLOW", state="DISABLED")]

        def mk(op, **k):
            return step(op, rid="data_flow_1", system="integration_service", **k)
        self.assertEqual(problems(on, [], mk("REPORT_ACTIVE_DATA_FLOW")), [])
        self.assertEqual(problems(off, [], mk("VERIFY_DATA_FLOW_DISABLED", **VERIFY)), [])
        self.assertTrue(problems(on, [], mk("VERIFY_DATA_FLOW_DISABLED", **VERIFY)))
        self.assertTrue(problems(off, [], mk("REPORT_ACTIVE_DATA_FLOW")))


class CheckRun(unittest.TestCase):
    def run_check(self, st, facts=PRESENT, snaps=()):
        pipeline = SimpleNamespace(view=make_view(facts, snaps), per_case={"case_1": {"linked": {"search_1": {"email"}}}})
        req = [{"case_id": "case_1", "requested_at": REQ, "audit_at": AUDIT}]
        return check_run(pipeline, {"case_1": ("INCOMPLETE", [st], [])}, req)

    def test_valid_step_is_counted_as_format_and_support_ok(self):
        counts, bad = self.run_check(step("REPORT_REMAINING_RECORD"))
        self.assertEqual((counts, bad), ({"cited": 1, "format_ok": 1, "support_ok": 1}, []))

    def test_format_and_support_failures_are_reported_separately(self):
        late = [fact("late", "search_1", OBS, email="a@x.org")]
        counts, bad = self.run_check(step("REPORT_REMAINING_RECORD", evidence="late"), facts=late)
        self.assertEqual((counts["format_ok"], [b[4] for b in bad]), (0, ["format"]))
        counts, bad = self.run_check(step("REPORT_REMAINING_RECORD", rid="search_2"))
        self.assertEqual((counts["format_ok"], counts["support_ok"], [b[4] for b in bad]), (1, 0, ["support"]))

    def test_uncited_steps_are_not_checked(self):
        counts, bad = self.run_check(step("REPORT_REMAINING_RECORD", evidence=""))
        self.assertEqual((counts["cited"], bad), (0, []))


if __name__ == "__main__":
    unittest.main()

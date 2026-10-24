"""Regression tests for explicit uncertainty and ambiguous identity matching.   python -m unittest discover -s code/tests -v"""
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghost.pipeline import Pipeline  # noqa: E402
from ghost.slots import MARGIN, PHASE_POINTS, solve_group  # noqa: E402
from ghost.state import ABSENT, PRESENT, UNKNOWN, ResourceState  # noqa: E402
from test_temporal import AUDIT, REQ, fact, pipe, view  # noqa: E402

T0 = datetime(2026, 9, 5, 12, 0, 0)


def case(cid, status="NOT_STARTED", requested="2026-09-02T12:00:00Z"):
    return {"case_id": cid, "reported_status": status, "requested_at": requested, "reported_at": "2026-09-05T12:00:00Z",
            "audit_at": "2026-09-20T12:00:00Z"}


def event(rid, minutes_before, result="FAILED"):
    return {"event_type": "DELETE_RECORD", "target_system": "account_service", "source_system": "deletion_worker",
            "resource_id": rid, "reported_result": result, "t": T0 - timedelta(minutes=minutes_before)}


class Presence(unittest.TestCase):
    def test_three_valued_presence(self):
        mk = lambda p: ResourceState("r", "s", "USER_PROFILE", p)  # noqa: E731
        self.assertEqual((mk(True).presence, mk(False).presence, mk(None).presence), (PRESENT, ABSENT, UNKNOWN))

    def test_missing_evidence_is_unknown_confirmed_absence_is_absent(self):
        req = {"audit_at": AUDIT, "requested_at": REQ}
        self.assertEqual(pipe(view([]), []).resource_state("search_1", req, set()).presence, UNKNOWN)
        snaps = [("snap", "search_service", "2026-09-15T11:00:00Z")]
        self.assertEqual(pipe(view([], snaps), []).resource_state("search_1", req, set()).presence, ABSENT)
        f = [fact("early", "search_1", "2026-09-14T10:00:00Z", exists=0, state="NOT_FOUND")]
        self.assertEqual(pipe(view(f), f).resource_state("search_1", req, set()).presence, ABSENT)


class Pairing(unittest.TestCase):
    def test_unique_match_by_result_class(self):
        cases = [case("case_1", "COMPLETE"), case("case_2", "NOT_STARTED", "2026-09-03T12:00:00Z")]
        events = [event("account_a", 70, "SUCCESS"), event("account_b", 50, "FAILED")]
        res, un = solve_group(cases, events, bonus=lambda cid, ev: PHASE_POINTS)
        self.assertEqual((res, un), ({"case_1": "account_a", "case_2": "account_b"}, {}))
        res, un = solve_group(cases, events)                      # the class separates them but is not identity by itself
        self.assertEqual(res, {})
        self.assertIn("independent evidence", un["case_1"]["reason"])

    def test_two_equally_plausible_candidates_stay_unresolved(self):
        cases = [case("case_1"), case("case_2", requested="2026-09-03T12:00:00Z")]
        events = [event("account_a", 70), event("account_b", 50)]      # same result class, no other evidence
        res, un = solve_group(cases, events)
        self.assertEqual(res, {})
        self.assertEqual(set(un), {"case_1", "case_2"})
        self.assertEqual(un["case_1"]["candidates"], ["account_a", "account_b"])

    def test_phase_agreement_needs_to_clear_the_margin(self):
        cases = [case("case_1"), case("case_2", requested="2026-09-03T12:00:00Z")]
        events = [event("account_a", 70), event("account_b", 50)]
        support = lambda n: (lambda cid, ev: n * PHASE_POINTS if (cid, ev["resource_id"]) == ("case_1", "account_a") else 0)  # noqa: E731
        res, un = solve_group(cases, events, bonus=support(1))                 # one agreement: 3 points < MARGIN
        self.assertEqual(res, {})
        self.assertGreater(MARGIN, PHASE_POINTS)
        res, un = solve_group(cases, events, bonus=support(2))                 # two agreements: 6 points >= MARGIN
        self.assertEqual(res.get("case_1"), "account_a")

    def test_contradicted_by_the_cases_other_events_stays_unresolved(self):
        cases = [case("case_1", "COMPLETE")]
        events = [event("account_a", 60, "SUCCESS")]
        res, un = solve_group(cases, events, checkable=lambda cid: 2, bonus=lambda cid, ev: 0)   # checkable, none agrees
        self.assertEqual(res, {})
        self.assertIn("not all", un["case_1"]["reason"])
        res, un = solve_group(cases, events, checkable=lambda cid: 2, bonus=lambda cid, ev: 2 * PHASE_POINTS)
        self.assertEqual(res, {"case_1": "account_a"})                   # all checkable events agree
        res, un = solve_group(cases, events, checkable=lambda cid: 0, bonus=lambda cid, ev: 0)   # nothing to check
        self.assertEqual(res, {})                       # ... but then the result class alone is not enough for a pool of one
        self.assertIn("independent evidence", un["case_1"]["reason"])

    def test_known_case_pins_the_other(self):
        cases = [case("case_1"), case("case_2", requested="2026-09-03T12:00:00Z")]
        events = [event("account_a", 70), event("account_b", 50)]
        res, un = solve_group(cases, events, known={"case_1": "account_a"}, bonus=lambda cid, ev: PHASE_POINTS)
        self.assertEqual(res, {"case_2": "account_b"})

    def test_only_candidate_contradicting_the_result_class_is_not_forced(self):
        res, un = solve_group([case("case_1", "COMPLETE")], [event("account_a", 60, "FAILED")])
        self.assertEqual(res, {})
        self.assertIn("contradicts", un["case_1"]["reason"])
        res, un = solve_group([case("case_1", "COMPLETE")], [event("account_a", 60, "FAILED")],
                              bonus=lambda cid, ev: PHASE_POINTS)       # independent phase support lets it through
        self.assertEqual(res, {"case_1": "account_a"})

    def test_extra_event_makes_a_single_case_ambiguous_unless_class_separates(self):
        res, un = solve_group([case("case_1")], [event("account_a", 60), event("account_b", 55)])
        self.assertEqual(res, {})
        res, un = solve_group([case("case_1")], [event("account_a", 60, "SUCCESS"), event("account_b", 55, "FAILED")])
        self.assertEqual(res, {})                       # the class separates them, but a lone class is not identity
        res, un = solve_group([case("case_1")], [event("account_a", 60, "SUCCESS"), event("account_b", 55, "FAILED")],
                              bonus=lambda cid, ev: PHASE_POINTS if ev["resource_id"] == "account_b" else 0)
        self.assertEqual(res, {"case_1": "account_b"})

    def test_oversized_pool_is_not_guessed(self):
        cases = [case(f"case_{i}", requested=f"2026-09-0{i}T12:00:00Z") for i in range(1, 8)]
        events = [event(f"account_{i}", 70 - i) for i in range(7)]
        res, un = solve_group(cases, events)
        self.assertEqual((res, len(un)), ({}, 7))


class MissingOrDecoyEvents(unittest.TestCase):
    """Adversarial pools: the true event is missing, a foreign event is present, or the evidence is misleading."""

    @staticmethod
    def three_cases():
        return [case("case_1"), case("case_2", requested="2026-09-03T12:00:00Z"), case("case_3", requested="2026-09-04T12:00:00Z")]

    def test_true_event_missing_and_decoy_outside_its_slot_band_leaves_the_pool_unresolved(self):
        # true events sit in bands 0 and 2 of the 3 equal bands; the replacement lands in band 1 twice -> bands 0,1,1
        events = [event("account_a", 72), event("account_decoy", 62), event("account_c", 60)]
        res, un = solve_group(self.three_cases(), events, bonus=lambda cid, ev: 2 * PHASE_POINTS)
        self.assertEqual(res, {})
        self.assertIn("slot structure", un["case_1"]["reason"])

    def test_foreign_extra_event_in_the_wrong_band_is_excluded_not_matched(self):
        cases = [case("case_1"), case("case_2", requested="2026-09-03T12:00:00Z")]
        events = [event("account_a", 70), event("account_b", 50), event("account_x", 65)]   # x also in band 0
        truth = {("case_1", "account_a"), ("case_2", "account_b")}
        bonus = lambda cid, ev: 2 * PHASE_POINTS if (cid, ev["resource_id"]) in truth else 0  # noqa: E731
        res, un = solve_group(cases, events, bonus=bonus)
        self.assertEqual(res, {"case_1": "account_a", "case_2": "account_b"})

    def test_foreign_extra_event_inside_a_band_with_equal_support_is_ambiguous(self):
        cases = [case("case_1"), case("case_2", requested="2026-09-03T12:00:00Z")]
        events = [event("account_a", 70), event("account_b", 50), event("account_x", 52)]   # x and b both in band 1
        res, un = solve_group(cases, events, bonus=lambda cid, ev: 2 * PHASE_POINTS)
        self.assertNotIn("case_2", res)
        self.assertTrue({"account_b", "account_x"} <= set(un["case_2"]["candidates"]))

    def test_misleading_class_versus_phase_is_not_forced(self):
        # the true event's class mismatches the case (a claim can be wrong); a decoy matches the class but not the phase
        cases = [case("case_1", "COMPLETE")]
        events = [event("account_true", 60, "FAILED"), event("account_decoy", 58, "SUCCESS")]
        bonus = lambda cid, ev: 2 * PHASE_POINTS if ev["resource_id"] == "account_true" else 0  # noqa: E731
        res, un = solve_group(cases, events, bonus=bonus)
        self.assertEqual(res, {})                                # 12 vs 10 points: inside the margin -> unresolved

    def test_partial_agreement_of_the_cases_other_events_is_not_enough(self):
        cases = [case("case_1", "COMPLETE")]
        events = [event("account_a", 60, "SUCCESS")]
        res, un = solve_group(cases, events, checkable=lambda cid: 2, bonus=lambda cid, ev: PHASE_POINTS)   # 1 of 2 agrees
        self.assertEqual(res, {})
        self.assertIn("not all", un["case_1"]["reason"])

    def test_clear_uniquely_supported_match_still_resolves(self):
        cases = [case("case_1", "COMPLETE")]
        events = [event("account_a", 60, "SUCCESS")]
        res, un = solve_group(cases, events, checkable=lambda cid: 2, bonus=lambda cid, ev: 2 * PHASE_POINTS)
        self.assertEqual((res, un), ({"case_1": "account_a"}, {}))

    def test_known_limitation_a_decoy_matching_slot_class_and_phase_cannot_be_detected(self):
        """Documented limitation, not a guarantee: a replacement event that matches the pool's slot band, the case's
        result class and the case's phase carries exactly the evidence a true event would, so it is accepted."""
        cases = [case("case_1", "COMPLETE")]
        decoy = event("account_decoy", 60, "SUCCESS")
        res, _ = solve_group(cases, [decoy], checkable=lambda cid: 1, bonus=lambda cid, ev: PHASE_POINTS)
        self.assertEqual(res, {"case_1": "account_decoy"})


class _Act:
    def __init__(self, rows):
        self.rows, self.by_res = rows, {}
        for r in rows:
            self.by_res.setdefault(r["resource_id"], []).append(r)

    def case_events(self, rid):
        return []


class PropagationIntoTheCase(unittest.TestCase):
    def _pipeline(self, events):
        p = Pipeline.__new__(Pipeline)
        p.act, p.unresolved_identities = _Act(events), {}
        return p

    def _per_case(self, *cases):
        return {c["case_id"]: {"req": c, "subject": None, "linked": {}} for c in cases}

    def test_unresolved_account_gives_no_account_step_state_or_citation(self):
        cases = [case("case_1"), case("case_2", requested="2026-09-03T12:00:00Z")]
        p = self._pipeline([event("account_a", 70), event("account_b", 50)])
        pc = self._per_case(*cases)
        assigned = p.assign_null_accounts(pc)
        self.assertEqual(assigned, {})
        self.assertFalse(any(r.startswith("account_") for c in pc.values() for r in c["linked"]))
        self.assertEqual(set(p.unresolved_identities), {"case_1", "case_2"})
        self.assertIn("account", p.unresolved_identities["case_1"])
        p.view = None
        p.policy = None
        status, steps, states = p.build_case(dict(pc["case_1"]))
        self.assertEqual((steps, states), ([], []))                  # nothing is asserted about an unidentified account

    def test_clear_match_is_assigned_and_recorded_as_activity_link(self):
        cases = [case("case_1", "COMPLETE"), case("case_2", requested="2026-09-03T12:00:00Z")]
        p = self._pipeline([event("account_a", 70, "SUCCESS"), event("account_b", 50)])
        pc = self._per_case(*cases)
        bonus = lambda cid, ev: PHASE_POINTS  # noqa: E731
        bonus.checkable = lambda cid: 1
        p.phase_bonus = lambda per_case, learn_exclude=(): bonus          # the cases' other events agree with the account
        self.assertEqual(p.assign_null_accounts(pc), {"case_1": "account_a", "case_2": "account_b"})
        self.assertEqual(pc["case_1"]["linked"]["account_a"], {"activity"})
        self.assertEqual(p.unresolved_identities, {})

    def test_competing_cases_for_one_anonymised_record_stay_unresolved(self):
        rec = {"event_type": "ANONYMIZE_RECORD", "target_system": "analytics_platform", "source_system": "deletion_worker",
               "resource_id": "analytics_9", "reported_result": "SUCCESS", "t": T0 - timedelta(minutes=28, seconds=-1)}
        # both cases' accounts sit at seconds-phase 41 relative to the record, so the record fits either case equally
        a1, a2 = event("account_a", 70), event("account_b", 50)
        a1["t"] = rec["t"] - timedelta(seconds=41 + 60 * 40)
        a2["t"] = rec["t"] - timedelta(seconds=41 + 60 * 20)
        p = self._pipeline([a1, a2, rec])
        pc = self._per_case(case("case_1"), case("case_2", requested="2026-09-03T12:00:00Z"))
        pc["case_1"]["linked"] = {"account_a": {"email"}}
        pc["case_2"]["linked"] = {"account_b": {"email"}}
        acc_ev = {"account_a": a1, "account_b": a2}
        out = p.assign_null_others(pc, acc_ev)
        self.assertEqual(out, {})
        self.assertEqual(set(p.unresolved_identities), {"case_1", "case_2"})
        del pc["case_2"]                                   # with a single competing case the record is attached
        p.unresolved_identities = {}
        self.assertEqual(p.assign_null_others(pc, acc_ev), {"case_1": ["analytics_9"]})


class LaterCapturedEvidenceAndIdentity(unittest.TestCase):
    def test_later_captured_exact_identifier_discovers_but_never_sets_state(self):
        from ghost.identity import Subject
        s = Subject(user_id="user_1", emails={"a@x.org"}, current_email="a@x.org")
        f = [fact("late", "search_7", "2026-09-15T09:00:00Z", email="a@x.org")]   # captured after audit, observed before
        p = pipe(view(f), f)
        req = {"requested_at": REQ, "audit_at": AUDIT, "reported_at": REQ}
        self.assertIn("search_7", p.linked_resources(s, req))                     # permitted discovery
        st = p.resource_state("search_7", req, set())
        self.assertEqual((st.presence, st.evidence_id), (UNKNOWN, ""))             # but no state and no citation

    def test_observation_after_audit_never_links(self):
        from ghost.identity import Subject
        s = Subject(user_id="user_1", emails={"a@x.org"}, current_email="a@x.org")
        f = [fact("late", "search_7", "2026-09-16T09:00:00Z", email="a@x.org")]
        found = pipe(view(f), f).linked_resources(s, {"requested_at": REQ, "audit_at": AUDIT, "reported_at": REQ})
        self.assertNotIn("search_7", found)


class ExistingValidations(unittest.TestCase):
    def test_sample_cases_keep_status_and_every_emitted_step_is_correct(self):
        import csv
        import json
        try:
            from ghost.classify import classify_requests
            pipe_ = Pipeline()
            everything = pipe_.samples + pipe_.requests
            results = pipe_.run(everything, classify_requests(everything))
            with open(Path(__file__).resolve().parents[2] / "dataset" / "sample_output.csv", encoding="utf-8") as fh:
                truth = {r["case_id"]: r for r in csv.DictReader(fh)}
        except Exception as e:  # dataset or model cache unavailable
            self.skipTest(f"dataset/cache unavailable: {type(e).__name__}")
        key = lambda st: (st["phase"], st["system_id"], st["resource_id"], st["operation"], st["state"])  # noqa: E731
        emitted = wrong = 0
        for cid, row in truth.items():
            self.assertEqual(results[cid][0], row["deletion_status"], cid)
            ref = {key(x) for x in json.loads(row["steps_json"])}
            for st in results[cid][1]:
                emitted += 1
                wrong += key(st) not in ref
        self.assertEqual(wrong, 0, f"{wrong} of {emitted} emitted sample steps are not in the reference")

    """Account pairing against identifier-confirmed groups of the real dataset (uses cached model output)."""

    def test_leave_one_group_out_pairing_has_no_wrong_assignments(self):
        import contextlib
        import io
        import runpy
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                runpy.run_path(str(Path(__file__).resolve().parents[1] / "evaluation" / "slot_validation.py"), run_name="__main__")
        except Exception as e:  # dataset or model cache unavailable
            self.skipTest(f"dataset/cache unavailable: {type(e).__name__}")
        out = buf.getvalue()
        for line in out.splitlines():
            if line.startswith("class"):
                self.assertIn("wrong 0", line)
        self.assertIn("coverage", out)


if __name__ == "__main__":
    unittest.main()

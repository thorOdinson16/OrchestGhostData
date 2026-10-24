"""Retention / legal-hold inference and unresolved rule conflicts.   python -m unittest discover -s code/tests -v

Specification text (problem_statement.md): "preserve records that the supplied policy requires the organization to retain" and
"records that must be retained remain available". It never says that a record's mere existence means it is under retention or
a hold; the existence reading is an empirical assumption (policy.RETAIN_BY_EXISTENCE) that these tests pin down.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ghost.policy as pol  # noqa: E402
from ghost.policy import FALSE, TRUE, UNKNOWN, PolicyEngine  # noqa: E402
from ghost.state import ResourceState  # noqa: E402

AUDIT = "2026-09-15T12:00:00Z"
RETENTION, HOLD = "Record is within the supplied retention period", "A supplied legal hold is active"


def state(cat, present=True, ev="e1"):
    return ResourceState("r_1", "sys", cat, present, False, None, ev)


def rule(rid, cat, cond, action):
    return {"rule_id": rid, "data_category": cat, "condition": cond, "required_action": action}


class RetentionAndLegalHold(unittest.TestCase):
    def decide(self, cat, ctx, **kw):
        return PolicyEngine().decide(state(cat, **kw), {"audit_at": AUDIT, **ctx})

    def test_supplied_dates_decide(self):
        t = self.decide("SECURITY_AUDIT_LOG", {"retention_until": "2026-10-01"})
        self.assertEqual((t.condition, t.required, t.verify_op), (TRUE, "RETAIN", "VERIFY_RECORD_PRESENT"))
        t = self.decide("SECURITY_AUDIT_LOG", {"retention_until": "2026-01-01"})
        self.assertEqual((t.condition, t.required, t.verify_op, t.undetermined), (FALSE, "NONE", "", False))   # lapsed: untouched
        t = self.decide("LEGAL_HOLD_RECORD", {"retained_until": "2026-01-01"})
        self.assertEqual((t.condition, t.verify_op), (FALSE, ""))

    def test_missing_date_and_no_observation_is_unknown(self):
        for cat in ("SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD"):
            t = self.decide(cat, {}, present=None, ev="")
            self.assertEqual((t.condition, t.unresolved, t.undetermined, t.violated), (UNKNOWN, True, True, False), cat)

    def test_absent_record_without_a_date_is_unknown_not_retained(self):
        t = self.decide("SECURITY_AUDIT_LOG", {}, present=False)
        self.assertEqual(t.condition, UNKNOWN)

    def test_existence_fallback_is_an_explicit_assumption_and_can_be_switched_off(self):
        for cat in ("SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD"):
            self.assertEqual(self.decide(cat, {}, present=True).condition, TRUE, cat)       # default: documented assumption
            with mock.patch.object(pol, "RETAIN_BY_EXISTENCE", False):
                t = self.decide(cat, {}, present=True)
                self.assertEqual((t.condition, t.unresolved), (UNKNOWN, True), cat)           # strict reading: stays unknown

    def test_a_date_always_beats_the_existence_assumption(self):
        t = self.decide("SECURITY_AUDIT_LOG", {"retention_until": "2026-01-01"}, present=True)
        self.assertEqual(t.condition, FALSE)


class RuleConflicts(unittest.TestCase):
    def engine(self, *rules):
        return PolicyEngine(list(rules))

    def test_two_applicable_rules_with_different_actions_conflict(self):
        e = self.engine(rule("r1", "USER_PROFILE", "Always", "DELETE"), rule("r2", "USER_PROFILE", "Always", "REDACT"))
        t = e.decide(state("USER_PROFILE"))
        self.assertEqual((t.unresolved, t.condition, t.violated, t.rule_id), (True, UNKNOWN, False, ""))
        self.assertIn("conflicting", t.reason)

    def test_two_applicable_rules_with_the_same_action_do_not_conflict(self):
        e = self.engine(rule("r1", "USER_PROFILE", "Always", "DELETE"), rule("r2", "USER_PROFILE", "Always", "DELETE"))
        t = e.decide(state("USER_PROFILE"))
        self.assertEqual((t.unresolved, t.violated, t.required), (False, True, "DELETE"))

    def test_unknown_rule_that_could_forbid_deletion_blocks_the_decision(self):
        # DELETE applies, but whether a legal hold (RETAIN) applies is unknown: deleting could destroy retained data
        e = self.engine(rule("r1", "USER_PROFILE", "Always", "DELETE"), rule("r2", "USER_PROFILE", HOLD, "RETAIN"))
        st = state("USER_PROFILE", present=None, ev="")
        t = e.decide(st, {"audit_at": AUDIT})
        self.assertEqual(t.unresolved, True)
        self.assertEqual(t.violated, False)
        self.assertIn("r2", t.reason)
        # the same engine with the hold's date known and lapsed: the unknown is resolved and DELETE applies
        t = e.decide(state("USER_PROFILE"), {"audit_at": AUDIT, "retained_until": "2026-01-01"})
        self.assertEqual((t.unresolved, t.violated, t.rule_id), (False, True, "r1"))

    def test_unknown_rule_with_the_same_action_is_irrelevant(self):
        e = self.engine(rule("r1", "USER_PROFILE", "Always", "DELETE"), rule("r2", "USER_PROFILE", HOLD, "DELETE"))
        t = e.decide(state("USER_PROFILE", present=None, ev=""), {"audit_at": AUDIT})
        self.assertEqual((t.unresolved, t.rule_id), (False, "r1"))                          # nothing the hold says changes it

    def test_false_rule_never_blocks(self):
        e = self.engine(rule("r1", "USER_PROFILE", "Always", "DELETE"), rule("r2", "USER_PROFILE", HOLD, "RETAIN"))
        t = e.decide(state("USER_PROFILE"), {"audit_at": AUDIT, "retained_until": "2026-01-01"})
        self.assertFalse(t.unresolved)

    def test_the_real_rules_have_one_rule_per_category_so_nothing_else_changes(self):
        cats = {}
        for r in pol.read_csv("deletion_rules.csv"):
            cats.setdefault(r["data_category"], []).append(r)
        self.assertTrue(all(len(v) == 1 for v in cats.values()))

    def test_missing_rule_and_missing_evaluator_are_unresolved(self):
        t = self.engine().decide(state("USER_PROFILE"))
        self.assertEqual((t.unresolved, t.violated), (True, False))
        t = self.engine(rule("r1", "USER_PROFILE", "customer likes blue", "DELETE")).decide(state("USER_PROFILE"))
        self.assertEqual((t.unresolved, t.violated), (True, False))


if __name__ == "__main__":
    unittest.main()

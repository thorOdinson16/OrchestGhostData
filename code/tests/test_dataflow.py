"""Data-flow activity is three-valued: active, inactive, or unknown - missing is never "false".   python -m unittest discover -s code/tests -v"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghost.docs import _flags  # noqa: E402
from ghost.mapping import Fact  # noqa: E402
from ghost.policy import PolicyEngine  # noqa: E402
from ghost.state import ResourceState, fact_flags, merge_flags, tri  # noqa: E402


def flow(attrs, state=None, rtype="DATA_FLOW"):
    return Fact(0, "f", "2026-09-14T10:00:00Z", "integration_service", "data_flow_1", rtype, None, None, 1, state, None, None, None,
                None, None, attrs, "line1")


def active_of(attrs, **kw):
    return fact_flags(flow(attrs, **kw))[2]


class TriState(unittest.TestCase):
    def test_recognised_spellings(self):
        for v in (True, 1, "1", "true", "TRUE", " yes ", "y"):
            self.assertIs(tri(v), True, v)
        for v in (False, 0, "0", "false", "No", "n"):
            self.assertIs(tri(v), False, v)

    def test_everything_else_is_unknown(self):
        for v in (None, "", "null", "None", "maybe", "enabled", 2, [], {}, "tru"):
            self.assertIsNone(tri(v), repr(v))


class StateInterpretation(unittest.TestCase):
    def test_explicit_values_keep_their_meaning(self):
        self.assertIs(active_of({"enabled": "1"}), True)
        self.assertIs(active_of({"enabled": "true"}), True)
        self.assertIs(active_of({"enabled": "0"}), False)
        self.assertIs(active_of({"enabled": "false"}), False)
        self.assertIs(active_of({}, state="ACTIVE"), True)
        self.assertIs(active_of({}, state="DISABLED"), False)

    def test_missing_null_and_malformed_are_unknown(self):
        for attrs in ({}, {"enabled": None}, {"enabled": ""}, {"enabled": "maybe"}, {"enabled": "null"}):
            self.assertIsNone(active_of(attrs), attrs)

    def test_scheduler_must_agree_for_a_connector_to_count_as_active(self):
        hb = lambda **a: active_of({"event": "connector_heartbeat", "enabled": "1", **a})   # noqa: E731
        self.assertIs(hb(scheduler_registered="1"), True)
        self.assertIs(hb(scheduler_registered="0"), False)
        self.assertIsNone(hb(scheduler_registered="??"))                      # enabled, but whether it is scheduled is unreadable
        self.assertIs(active_of({"event": "connector_heartbeat", "enabled": "0", "scheduler_registered": "??"}), False)

    def test_backup_restore_flag_is_three_valued_too(self):
        arch = lambda a: fact_flags(flow(a, rtype="BACKUP_ARCHIVE"))[2]    # noqa: E731
        self.assertIs(arch({"restore_allowed": "true"}), True)
        self.assertIs(arch({"restore_allowed": "false"}), False)
        self.assertIsNone(arch({"restore_allowed": "unclear"}))
        self.assertIs(arch({"eligible": "1", "deletion_block": "0"}), True)
        self.assertIs(arch({"eligible": "1", "deletion_block": "1"}), False)
        self.assertIsNone(arch({"eligible": "1", "deletion_block": "???"}))

    def test_merge_keeps_unknown_when_no_fact_says_anything(self):
        self.assertIsNone(merge_flags([flow({"enabled": "maybe"})])[2])
        self.assertIs(merge_flags([flow({"enabled": "maybe"}), flow({"enabled": "1"})])[2], True)


class DocumentExtraction(unittest.TestCase):
    def flags(self, answer, prefix="data_flow"):
        with mock.patch("ghost.docs.interpret", return_value={"present": True, "active": answer, "quote": ""}):
            return _flags(prefix, "text", "data_flow_1")

    def test_true_and_false_are_recorded(self):
        self.assertEqual(self.flags(True)["enabled"], "true")
        self.assertEqual(self.flags(False)["enabled"], "false")

    def test_null_missing_and_malformed_are_not_serialised_as_false(self):
        for answer in (None, "unclear", "true", 1, 0, [], {}):
            self.assertNotIn("enabled", self.flags(answer), repr(answer))
        with mock.patch("ghost.docs.interpret", return_value={"present": True}):                  # key absent altogether
            self.assertNotIn("enabled", _flags("data_flow", "text", "data_flow_1"))

    def test_backup_restore_flag_follows_the_same_rule(self):
        self.assertEqual(self.flags(False, "backup")["restore_allowed"], "false")
        self.assertEqual(self.flags(True, "backup")["restore_allowed"], "true")
        self.assertNotIn("restore_allowed", self.flags(None, "backup"))

    def test_unknown_document_flow_ends_up_with_unknown_activity(self):
        attrs = self.flags(None)
        self.assertIsNone(fact_flags(flow(attrs))[2])


class DownstreamPolicy(unittest.TestCase):
    def treat(self, active):
        return PolicyEngine().decide(ResourceState("data_flow_1", "integration_service", "DATA_FLOW", True, False, active, "f"))

    def test_active_inactive_unknown(self):
        t = self.treat(True)
        self.assertEqual((t.violated, t.uncertain, t.finding_op), (True, False, "REPORT_ACTIVE_DATA_FLOW"))
        t = self.treat(False)
        self.assertEqual((t.violated, t.uncertain, t.undetermined), (False, False, False))
        t = self.treat(None)
        self.assertEqual((t.violated, t.uncertain, t.undetermined, t.verify_op), (False, True, True, "VERIFY_DATA_FLOW_DISABLED"))


if __name__ == "__main__":
    unittest.main()

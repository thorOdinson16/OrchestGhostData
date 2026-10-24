"""Policy tests: condition-aware rules (TRUE / FALSE / UNKNOWN), conflicts, missing rules, planner handling.

    python -m unittest discover -s code/tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghost.planner import build_steps  # noqa: E402
from ghost.policy import FALSE, TRUE, UNKNOWN, PolicyEngine  # noqa: E402
from ghost.state import ResourceState  # noqa: E402

AUDIT = "2026-09-15T12:00:00Z"
ME = {"subject_emails": {"me@x.org"}, "subject_user": "user_1", "audit_at": AUDIT}


def state(cat, rid="r_1", present=True, identified=False, active=None, ev="evidence_1"):
    return ResourceState(rid, "sys", cat, present, identified, active, ev)


def decide(cat, ctx=None, engine=None, **kw):
    return (engine or PolicyEngine()).decide(state(cat, **kw), {**ME, **(ctx or {})})


class UnconditionalRules(unittest.TestCase):
    def test_always_rules_keep_behaviour(self):
        for cat, op in (("USER_PROFILE", "DELETE_RECORD"), ("SECONDARY_IDENTIFIER", "DELETE_RECORD"),
                        ("SEARCH_INDEX_ENTRY", "DELETE_RECORD"), ("CACHE_ENTRY", "DELETE_RECORD"),
                        ("USER_EXPORT", "DELETE_RECORD"), ("MARKETING_PROFILE", "DELETE_RECORD")):
            t = decide(cat)
            self.assertEqual((t.condition, t.violated, t.remediation_op), (TRUE, True, op), cat)
            self.assertFalse(decide(cat, present=False).violated, cat)

    def test_data_flow_contract_rule(self):
        self.assertTrue(decide("DATA_FLOW", active=True).violated)
        self.assertFalse(decide("DATA_FLOW", active=False).violated)


class ConditionalRules(unittest.TestCase):
    # rule_05 support message: shared conversation context must be retained -> REDACT
    def test_rule_05(self):
        t = decide("SUPPORT_MESSAGE", {"conversation": True}, identified=True)
        self.assertEqual((t.rule_id, t.condition, t.violated, t.remediation_op), ("rule_05", TRUE, True, "REDACT_RECORD"))
        self.assertFalse(decide("SUPPORT_MESSAGE", {"conversation": True}, identified=False).violated)
        u = decide("SUPPORT_MESSAGE", {"conversation": False}, identified=True)
        self.assertEqual((u.condition, u.unresolved, u.violated), (UNKNOWN, True, False))

    # rule_06 attachment: delete only if it belongs only to the subject
    def test_rule_06(self):
        t = decide("SUPPORT_ATTACHMENT", {"owners": {"me@x.org"}})
        self.assertEqual((t.condition, t.violated, t.remediation_op), (TRUE, True, "DELETE_RECORD"))
        self.assertTrue(decide("SUPPORT_ATTACHMENT", {"owners": {"user_1"}}).violated)
        f = decide("SUPPORT_ATTACHMENT", {"owners": {"me@x.org", "other@x.org"}})
        self.assertEqual((f.condition, f.violated, f.verify_op, f.unresolved), (FALSE, False, "", False))
        u = decide("SUPPORT_ATTACHMENT", {"owners": set()})
        self.assertEqual((u.condition, u.unresolved, u.violated), (UNKNOWN, True, False))

    # rule_07 / rule_08 analytics and telemetry: anonymize while attributable
    def test_rules_07_08(self):
        for cat, rule in (("ANALYTICS_PROFILE", "rule_07"), ("PRODUCT_TELEMETRY", "rule_08")):
            t = decide(cat, identified=True)
            self.assertEqual((t.rule_id, t.condition, t.violated, t.remediation_op), (rule, TRUE, True, "ANONYMIZE_RECORD"))
            f = decide(cat, identified=False)       # nothing attributable left: rule met, final state still verified
            self.assertEqual((f.condition, f.violated, f.verify_op), (FALSE, False, "VERIFY_RECORD_ANONYMIZED"))
            self.assertEqual(decide(cat, present=False).condition, FALSE)
            u = decide(cat, present=None, identified=False)
            self.assertEqual((u.condition, u.unresolved, u.violated), (UNKNOWN, True, False))

    # rule_11 email delivery log: redact when it contains an email
    def test_rule_11(self):
        t = decide("EMAIL_DELIVERY_LOG", {"text_has_email": True}, identified=True)
        self.assertEqual((t.condition, t.violated, t.remediation_op), (TRUE, True, "REDACT_RECORD"))
        f = decide("EMAIL_DELIVERY_LOG", {"text_has_email": False})
        self.assertEqual((f.condition, f.violated, f.verify_op), (FALSE, False, "VERIFY_RECORD_REDACTED"))
        u = decide("EMAIL_DELIVERY_LOG", {"text_has_email": None})
        self.assertEqual((u.condition, u.unresolved), (UNKNOWN, True))

    # rule_12 audit log: retain while within retention
    def test_rule_12(self):
        t = decide("SECURITY_AUDIT_LOG", {"retention_until": "2031-12-31T23:59:59Z"})
        self.assertEqual((t.condition, t.required, t.verify_op), (TRUE, "RETAIN", "VERIFY_RECORD_PRESENT"))
        f = decide("SECURITY_AUDIT_LOG", {"retention_until": "2026-01-01T00:00:00Z"})
        self.assertEqual((f.condition, f.verify_op, f.unresolved), (FALSE, "", False))
        self.assertEqual(decide("SECURITY_AUDIT_LOG").condition, TRUE)                      # still held by the audit store
        u = decide("SECURITY_AUDIT_LOG", present=None)
        self.assertEqual((u.condition, u.unresolved), (UNKNOWN, True))

    # rule_13 legal hold: retain while a hold is active
    def test_rule_13(self):
        t = decide("LEGAL_HOLD_RECORD", {"retained_until": "2033-12-31T23:59:59Z"})
        self.assertEqual((t.condition, t.required), (TRUE, "RETAIN"))
        f = decide("LEGAL_HOLD_RECORD", {"retained_until": "2026-02-01T00:00:00Z"})
        self.assertEqual((f.condition, f.verify_op), (FALSE, ""))
        u = decide("LEGAL_HOLD_RECORD", present=None)
        self.assertEqual((u.condition, u.unresolved), (UNKNOWN, True))

    # rule_14 backup: retained only if restore access can be blocked
    def test_rule_14(self):
        t = decide("BACKUP_ARCHIVE", {"restore_known": True}, active=True)
        self.assertEqual((t.condition, t.violated, t.remediation_op), (TRUE, True, "BLOCK_RESTORE"))
        self.assertFalse(decide("BACKUP_ARCHIVE", {"restore_known": True}, active=False).violated)
        u = decide("BACKUP_ARCHIVE", {"restore_known": False}, active=None)
        self.assertEqual((u.condition, u.unresolved, u.violated), (UNKNOWN, True, False))

    # rule_15 tombstone: redact direct identifiers of a deleted-account reference
    def test_rule_15(self):
        t = decide("ACCOUNT_TOMBSTONE", {"account_status": "DELETED"}, identified=True)
        self.assertEqual((t.condition, t.violated, t.remediation_op), (TRUE, True, "REDACT_RECORD"))
        self.assertEqual(decide("ACCOUNT_TOMBSTONE", {"account_status": None}).condition, TRUE)   # tombstone by record kind
        f = decide("ACCOUNT_TOMBSTONE", {"account_status": "ACTIVE"}, identified=True)
        self.assertEqual((f.condition, f.violated, f.verify_op), (FALSE, False, ""))


class RuleTableEdgeCases(unittest.TestCase):
    def test_conflicting_rules_are_unresolved(self):
        rules = [{"rule_id": "a", "data_category": "X", "condition": "Always", "required_action": "DELETE"},
                 {"rule_id": "b", "data_category": "X", "condition": "Always", "required_action": "REDACT"}]
        t = decide("X", engine=PolicyEngine(rules))
        self.assertEqual((t.unresolved, t.violated, t.condition), (True, False, UNKNOWN))
        self.assertIn("conflicting", t.reason)

    def test_missing_rule_is_unresolved_not_a_default_delete(self):
        t = decide("NO_SUCH_CATEGORY")
        self.assertEqual((t.unresolved, t.violated), (True, False))
        self.assertIn("no rule", t.reason)

    def test_unknown_condition_text_is_unknown(self):
        rules = [{"rule_id": "z", "data_category": "X", "condition": "Moon is full", "required_action": "DELETE"}]
        t = decide("X", engine=PolicyEngine(rules))
        self.assertEqual((t.condition, t.unresolved, t.violated), (UNKNOWN, True, False))

    def test_unconditional_fallback_beats_false_conditional(self):
        rules = [{"rule_id": "a", "data_category": "X", "condition": "Attachment belongs only to the deletion subject",
                  "required_action": "DELETE"},
                 {"rule_id": "b", "data_category": "X", "condition": "Always", "required_action": "REDACT"}]
        t = decide("X", {"owners": {"me@x.org", "other@x.org"}}, engine=PolicyEngine(rules), identified=True)
        self.assertEqual((t.rule_id, t.required, t.condition), ("b", "REDACT", TRUE))

    def test_every_csv_condition_has_an_evaluator(self):
        from ghost.policy import CONDITIONS
        for rs in PolicyEngine().rules.values():
            for r in rs:
                self.assertIn(r["condition"].strip().lower(), CONDITIONS, r["rule_id"])


class PlannerHandling(unittest.TestCase):
    def test_unresolved_has_no_finding_and_no_complete_step(self):
        u = decide("SECURITY_AUDIT_LOG", present=None)
        self.assertEqual(build_steps([u], True), [])
        steps = build_steps([u], False)
        self.assertEqual([s["phase"] for s in steps], ["VERIFICATION"])
        self.assertEqual(steps[0]["evidence_id"], "")

    def test_false_condition_without_verification_emits_nothing(self):
        f = decide("SUPPORT_ATTACHMENT", {"owners": {"me@x.org", "other@x.org"}})
        self.assertEqual(build_steps([f], True), [])
        self.assertEqual(build_steps([f], False), [])


class ConditionInputsAreTemporallyEligible(unittest.TestCase):
    """Policy-condition inputs may not come from evidence captured after audit_at (later files are for discovery only)."""

    def setUp(self):
        from test_temporal import AUDIT as A, REQ, fact, pipe, view
        self.A, self.REQ, self.fact, self.pipe, self.view = A, REQ, fact, pipe, view

    def _state(self, facts, rid, rtype, doc_text=None):
        p = self.pipe(self.view(facts), facts)
        p._doc_text = doc_text or {}
        return p.resource_state(rid, {"audit_at": self.A, "requested_at": self.REQ}, set()), p

    def test_retention_date_only_in_a_later_captured_file_is_not_used(self):
        f = self.fact("late", "audit_record_1", "2026-09-15T09:00:00Z", rtype="SECURITY_AUDIT_LOG", user="user_1")
        f.attrs["retention_until"] = "2033-12-31T23:59:59Z"
        st, p = self._state([f], "audit_record_1", "SECURITY_AUDIT_LOG")
        self.assertIsNone(st.context["retention_until"])
        self.assertEqual(PolicyEngine().decide(st).condition, UNKNOWN)

    def test_retention_date_in_a_citable_file_is_used(self):
        f = self.fact("early", "audit_record_1", "2026-09-14T10:00:00Z", rtype="SECURITY_AUDIT_LOG", user="user_1")
        f.attrs["retention_until"] = "2033-12-31T23:59:59Z"
        st, _ = self._state([f], "audit_record_1", "SECURITY_AUDIT_LOG")
        self.assertEqual(PolicyEngine().decide(st).condition, TRUE)

    def test_screenshot_quote_alone_is_not_conversation_content(self):
        f = self.fact("early", "support_1", "2026-09-14T10:00:00Z", rtype="SUPPORT_MESSAGE", email="a@x.org", loc="doc")
        f.attrs.update(event="doc_observation", interp_quote="Customer: a@x.org")
        st, _ = self._state([f], "support_1", "SUPPORT_MESSAGE", {"early": '{"table": {"columns": ["Name"]}}'})
        self.assertFalse(st.context["conversation"])
        self.assertEqual(PolicyEngine().decide(st).condition, UNKNOWN)

    def test_screenshot_with_message_table_or_order_is_conversation_content(self):
        f = self.fact("early", "support_1", "2026-09-14T10:00:00Z", rtype="SUPPORT_MESSAGE", email="a@x.org", loc="doc")
        f.attrs.update(event="doc_observation")
        for text in ('{"columns": ["Time", "Sender", "Message"]}'.replace("Sender", '"Sender"').replace("Message", '"Message"'),
                     "Order ORD-20260534 In transit"):
            st, _ = self._state([f], "support_1", "SUPPORT_MESSAGE", {"early": text})
            self.assertTrue(st.context["conversation"], text)


if __name__ == "__main__":
    unittest.main()

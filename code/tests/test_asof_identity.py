"""Audit-time-aware identity resolution.   python -m unittest discover -s code/tests -v

An identifier introduced after a case's audit_at must not influence that case; the value it replaced is the one in force at
the cutoff; a snapshot updated after the cutoff does not make unchanged values new; unprovable history is ambiguous.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghost.identity import IdentityStore, email_hash  # noqa: E402

BEFORE, CUTOFF, AFTER = "2026-03-01T00:00:00Z", "2026-06-01T00:00:00Z", "2026-09-01T00:00:00Z"


def store(users, log):
    tables = {"user_identities.csv": users, "user_audit_log.csv": log}
    with mock.patch("ghost.identity.read_csv", lambda name: tables[name]):
        return IdentityStore()


def user(uid, email, phone="", name="N", updated=AFTER):
    return {"user_id": uid, "name": name, "email": email, "phone": phone, "email_hash": email_hash(email), "updated_at": updated}


def change(aid, uid, when, fld, old, new):
    return {"audit_id": aid, "user_id": uid, "event_time": when, "field_name": fld, "old_value": old, "new_value": new}


class AsOf(unittest.TestCase):
    def test_alias_added_before_the_cutoff_resolves(self):
        s = store([user("u1", "new@x.org")], [change("a1", "u1", BEFORE, "email", "old@x.org", "new@x.org")])
        for email in ("old@x.org", "NEW@x.org"):
            sub = s.resolve(email, as_of=CUTOFF)
            self.assertEqual(sub.user_id, "u1", email)
        self.assertEqual(s.resolve("new@x.org", as_of=CUTOFF).current_email, "new@x.org")

    def test_alias_added_after_the_cutoff_does_not_resolve_and_the_replaced_one_does(self):
        s = store([user("u1", "new@x.org")], [change("a1", "u1", AFTER, "email", "old@x.org", "new@x.org")])
        self.assertIsNone(s.resolve("new@x.org", as_of=CUTOFF))
        self.assertIn("after the cutoff", s.why_unresolved("new@x.org", CUTOFF))
        sub = s.resolve("old@x.org", as_of=CUTOFF)                       # the value in force at the cutoff
        self.assertEqual((sub.user_id, sub.current_email), ("u1", "old@x.org"))
        self.assertNotIn("new@x.org", sub.emails)
        self.assertNotIn(email_hash("new@x.org"), sub.hashes)
        self.assertEqual(s.resolve("new@x.org").user_id, "u1")           # without a cutoff nothing changes

    def test_chain_of_changes_is_cut_at_the_right_link(self):
        log = [change("a1", "u1", BEFORE, "email", "a@x.org", "b@x.org"), change("a2", "u1", AFTER, "email", "b@x.org", "c@x.org")]
        s = store([user("u1", "c@x.org")], log)
        sub = s.resolve("b@x.org", as_of=CUTOFF)
        self.assertEqual((sorted(sub.emails), sub.current_email), (["a@x.org", "b@x.org"], "b@x.org"))
        self.assertIsNone(s.resolve("c@x.org", as_of=CUTOFF))

    def test_snapshot_updated_after_the_cutoff_does_not_make_unchanged_values_new(self):
        s = store([user("u1", "me@x.org", phone="+1 555", updated=AFTER)], [])
        sub = s.resolve("me@x.org", as_of=CUTOFF)
        self.assertEqual((sub.user_id, sub.phones, sub.current_email), ("u1", {"+1 555"}, "me@x.org"))

    def test_phone_changes_are_cut_the_same_way(self):
        s = store([user("u1", "me@x.org", phone="+2")], [change("a1", "u1", AFTER, "phone", "+1", "+2")])
        sub = s.resolve("me@x.org", as_of=CUTOFF)
        self.assertEqual(sub.phones, {"+1"})
        self.assertEqual(s.resolve("me@x.org").phones, {"+1", "+2"})

    def test_email_held_by_two_users_is_ambiguous_unless_the_cutoff_separates_them(self):
        users = [user("u1", "shared@x.org"), user("u2", "other@x.org")]
        # u2 only took the address over after the cutoff: at the cutoff u1 was its only holder
        later = store(users, [change("a1", "u2", AFTER, "email", "other@x.org", "shared@x.org")])
        self.assertIsNone(later.resolve("shared@x.org"))                              # ever: two holders
        self.assertEqual(later.resolve("shared@x.org", as_of=CUTOFF).user_id, "u1")   # at the cutoff: one
        # u2 held it before the cutoff and u1 now does: both are past holders at the cutoff, so it stays ambiguous
        earlier = store(users, [change("a1", "u2", BEFORE, "email", "shared@x.org", "other@x.org")])
        self.assertIsNone(earlier.resolve("shared@x.org", as_of=CUTOFF))
        self.assertEqual(earlier.why_unresolved("shared@x.org", CUTOFF), "several users hold this email")

    def test_inconsistent_history_is_ambiguous_not_guessed(self):
        # the second change claims an old value that nothing introduced: when it appeared is unknown
        log = [change("a1", "u1", BEFORE, "email", "a@x.org", "b@x.org"), change("a2", "u1", AFTER, "email", "ghost@x.org", "c@x.org")]
        s = store([user("u1", "c@x.org")], log)
        self.assertIsNone(s.resolve("ghost@x.org", as_of=CUTOFF))
        self.assertIn("cannot establish", s.why_unresolved("ghost@x.org", CUTOFF))
        self.assertIn("ghost@x.org", s.users["u1"].as_of(CUTOFF).ambiguous)
        self.assertEqual(s.resolve("b@x.org", as_of=CUTOFF).user_id, "u1")

    def test_unknown_email_stays_unresolved(self):
        s = store([user("u1", "me@x.org")], [])
        self.assertIsNone(s.resolve("nobody@x.org", as_of=CUTOFF))
        self.assertIsNone(s.resolve("", as_of=CUTOFF))
        self.assertEqual(s.why_unresolved("nobody@x.org", CUTOFF), "no user has ever held this email")

    def test_no_cutoff_keeps_all_aliases(self):
        s = store([user("u1", "new@x.org")], [change("a1", "u1", AFTER, "email", "old@x.org", "new@x.org")])
        self.assertEqual(s.resolve("old@x.org").emails, {"old@x.org", "new@x.org"})


class PipelineUsesTheCutoff(unittest.TestCase):
    def test_request_email_introduced_after_audit_is_an_unresolved_subject_and_never_complete(self):
        from test_completion import REQUEST, _Act
        from test_temporal import pipe, view
        s = store([user("u1", "new@x.org")], [change("a1", "u1", "2026-09-20T00:00:00Z", "email", "old@x.org", "new@x.org")])
        p = pipe(view([]), [])
        p.ids, p.act, p.unresolved_identities = s, _Act(), {}
        req = dict(REQUEST, email="new@x.org", audit_at="2026-09-15T12:00:00Z")
        status, steps, _ = p.run([req], {"case_1": {"intent": "DELETE"}})["case_1"]
        self.assertEqual((status, steps), ("INCOMPLETE", []))
        self.assertIn("after the cutoff", p.unresolved_identities["case_1"]["subject"]["reason"])
        p.run([dict(req, email="old@x.org")], {"case_1": {"intent": "DELETE"}})      # the replaced address is in force
        self.assertNotIn("subject", p.unresolved_identities.get("case_1", {}))


if __name__ == "__main__":
    unittest.main()

"""Attribution (whose record) is kept apart from exposure (what the record shows).   python -m unittest discover -s code/tests -v"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghost.docs import doc_facts, pdf_facts, scoped_texts  # noqa: E402
from ghost.policy import PolicyEngine  # noqa: E402
from ghost.state import ResourceState, exposure_unknown, fact_flags, merge_flags  # noqa: E402

EVIDENCE = [{"file_id": "doc_1", "captured_at": "2026-09-14T10:00:00Z"}]


def card(*lines):
    return "Record\n" + "\n".join(lines) + "\n"


# two support records in one statement: the first shows the customer's email, the second only references the user id
RAW = ("SUPPORT EXPORT\n"
       + card("support_100", "Detail", "ana@x.org", "Detail", "I cannot log in to my account")
       + card("support_200", "Detail", "user_2", "Detail", "Refund processed for order ORD-7"))


class Interpreter:
    """Stands in for the model: answers from the text it is shown, and remembers what it was shown."""

    def __init__(self):
        self.seen = []

    def __call__(self, prefix, rid, disp, text, file_id):
        self.seen.append((rid, text))
        return {"present": True, "identifiers_exposed": "@" in text, "quote": ""}


def facts_of(raw, interp):
    with mock.patch("ghost.docs.interpret", interp):
        return {f.resource_id: f for f in pdf_facts([{"file_id": "doc_1", "kind": "pdf", "content": raw}], EVIDENCE, 0)}


class RecordScopedFlags(unittest.TestCase):
    def test_each_record_is_judged_on_its_own_text(self):
        interp = Interpreter()
        facts = facts_of(RAW, interp)
        seen = dict(interp.seen)
        self.assertIn("ana@x.org", seen["support_100"])
        self.assertNotIn("ana@x.org", seen["support_200"])          # the other record's email is not shown for this one
        self.assertNotIn("support_100", seen["support_200"])
        self.assertEqual(facts["support_100"].attrs["doc_exposes"], "true")
        self.assertEqual(facts["support_200"].attrs["doc_exposes"], "false")
        self.assertTrue(fact_flags(facts["support_100"])[1])
        self.assertFalse(fact_flags(facts["support_200"])[1])

    def test_attribution_is_still_recorded_record_by_record(self):
        facts = facts_of(RAW, Interpreter())
        self.assertEqual((facts["support_100"].email, facts["support_100"].user_ref), ("ana@x.org", None))
        self.assertEqual((facts["support_200"].email, facts["support_200"].user_ref), (None, "user_2"))

    def test_header_is_kept_and_other_records_are_dropped(self):
        scoped = scoped_texts(RAW, ["support_200"])
        self.assertIn("SUPPORT EXPORT", scoped)
        self.assertIn("support_200", scoped)
        self.assertNotIn("support_100", scoped)
        self.assertIsNone(scoped_texts("free text with support_300", ["support_300"]))     # undivided document


class AttributionIsNotExposure(unittest.TestCase):
    def test_catalog_link_alone_does_not_make_a_record_expose_an_email(self):
        link = {"rid": "support_300", "disp": "TKT-300", "email": "ana@x.org", "user": "user_1", "activity_id": "act_1",
                "t": __import__("datetime").datetime(2026, 9, 10)}
        row = {"file_id": "doc_1", "kind": "pdf", "content": card("support_300 TKT-300", "Detail", "Delivery was late, order ORD-3")}
        with mock.patch("ghost.docs.interpret", Interpreter()):
            f = [x for x in doc_facts([link], [row], EVIDENCE) if x.attrs.get("catalog")][0]
        self.assertEqual((f.email, f.user_ref), ("ana@x.org", "user_1"))           # attribution survives
        self.assertEqual(f.attrs["doc_exposes"], "false")
        self.assertEqual(merge_flags([f])[1], False)                               # ... but the record exposes nothing

    def test_linked_record_that_does_show_an_email_is_exposed(self):
        link = {"rid": "support_300", "disp": "TKT-300", "email": "ana@x.org", "user": None, "activity_id": "act_1",
                "t": __import__("datetime").datetime(2026, 9, 10)}
        row = {"file_id": "doc_1", "kind": "pdf", "content": card("support_300 TKT-300", "Detail", "reply to ana@x.org")}
        with mock.patch("ghost.docs.interpret", Interpreter()):
            f = [x for x in doc_facts([link], [row], EVIDENCE) if x.attrs.get("catalog")][0]
        self.assertTrue(merge_flags([f])[1])

    def test_unknown_exposure_is_not_serialised_as_false_and_stays_open(self):
        for answer in (None, "yes", 1, 0, "false"):                                # null, malformed, non-boolean
            f = list(facts_of(card("support_100", "Detail", "ana@x.org"),
                              lambda *a, ans=answer: {"present": True, "identifiers_exposed": ans}).values())[0]
            self.assertNotIn("doc_exposes", f.attrs, answer)
            self.assertTrue(exposure_unknown([f]), answer)
        st = ResourceState("support_100", "support_platform", "SUPPORT_MESSAGE", True, False, None, "doc_1", exposure_unknown=True)
        t = PolicyEngine().decide(st, {"conversation": True})
        self.assertEqual((t.violated, t.uncertain), (False, True))                 # not clean, not a violation: open
        st.exposure_unknown = False
        self.assertFalse(PolicyEngine().decide(st, {"conversation": True}).undetermined)


if __name__ == "__main__":
    unittest.main()

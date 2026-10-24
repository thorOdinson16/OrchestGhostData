"""Regression tests for record-level association of identifiers in PDFs and screenshots.   python -m unittest discover -s code/tests -v"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghost.docs import _mentions, associate, doc_mentions_record, segments  # noqa: E402
from ghost.identity import Subject  # noqa: E402
from test_temporal import AUDIT, REQ, fact, pipe, view  # noqa: E402


def card(*lines):
    return "Record\n" + "\n".join(lines) + "\n"


class Association(unittest.TestCase):
    def test_several_users_and_resources_in_one_pdf_stay_with_their_own_record(self):
        raw = ("STATEMENT\n" + card("account_100", "Detail", "ana@x.org", "Detail", "user_1")
               + card("search_200", "Detail", "bo@x.org", "Detail", "user_2"))
        found, un = associate(raw)
        self.assertEqual(found, {"account_100": ("ana@x.org", "user_1"), "search_200": ("bo@x.org", "user_2")})
        self.assertEqual(un, [])

    def test_email_and_user_id_of_different_records_are_not_merged(self):
        raw = card("support_5", "Detail", "ana@x.org") + card("Event", "Detail", "user_9")
        found, un = associate(raw)
        self.assertEqual(found, {"support_5": ("ana@x.org", None)})      # user_9 sits in another record

    def test_identifiers_in_a_header_or_unrelated_table_do_not_attach(self):
        raw = ("Account holder ana@x.org user_1\n" + card("Journal", "Detail", "classification Internal")
               + card("audit_record_7", "Detail", "event closed")
               + "ORD-1 | user_08 | Card dispute\nORD-2 | user_12 | Tax\n")
        found, un = associate(raw)
        self.assertEqual(found, {})
        self.assertEqual(un, [("audit_record_7", "no identifier in the same record")])

    def test_one_unambiguous_association_is_kept(self):
        found, un = associate(card("reference CS-1 / support_001", "Detail", "Priya", "Detail", "priya@x.org") + card("file a.pdf"))
        self.assertEqual((found, un), ({"support_001": ("priya@x.org", None)}, []))

    def test_ambiguous_associations_stay_unresolved(self):
        two_rids = card("account_1 and search_2", "Detail", "ana@x.org")
        found, un = associate(two_rids)
        self.assertEqual(found, {})
        self.assertEqual({r for r, _ in un}, {"account_1", "search_2"})
        two_owners = card("account_1", "Detail", "ana@x.org", "Detail", "bo@x.org")
        found, un = associate(two_owners)
        self.assertEqual((found, [r for r, _ in un]), ({}, ["account_1"]))
        conflicting = card("account_1", "Detail", "ana@x.org") + card("account_1", "Detail", "bo@x.org")
        found, un = associate(conflicting)
        self.assertEqual(found, {})
        self.assertIn(("account_1", "the document gives two different owners"), un)

    def test_table_rows_associate_only_within_the_row(self):
        raw = "support_5 | ana@x.org | open\nsupport_6 | bo@x.org | closed\naccount_7 | search_8 | user_3\n"
        found, un = associate(raw)
        self.assertEqual(found, {"support_5": ("ana@x.org", None), "support_6": ("bo@x.org", None)})
        self.assertEqual({r for r, _ in un}, {"account_7", "search_8"})

    def test_text_before_the_first_card_is_a_header_not_a_record(self):
        self.assertEqual([k for k, _ in segments("Title user_1\nRecord\nx\n")], ["card"])

    def test_resource_token_must_match_whole_id(self):
        self.assertFalse(_mentions("see search_428 row", "search_42"))
        self.assertTrue(_mentions("see search_428 row", "search_428"))


class CatalogDocuments(unittest.TestCase):
    link = {"rid": "tombstone_17", "disp": "user_17", "email": "x@x.org", "user": "user_17"}

    def test_bare_user_id_display_does_not_match_a_register_listing_many_people(self):
        register = {"kind": "pdf", "content": "ORD-1 | user_08 | Card\nORD-3 | user_17 | Chargeback\n"}
        self.assertFalse(doc_mentions_record(register, self.link, register["content"]))

    def test_bare_user_id_display_matches_a_screenshot_whose_own_record_field_carries_it(self):
        shot = {"kind": "image", "content": json.dumps({"other": {"record": "user_17"}, "table": {"rows": [["user_99"]]}})}
        self.assertTrue(doc_mentions_record(shot, self.link, shot["content"]))
        other = {"kind": "image", "content": json.dumps({"other": {"record": "user_99"}, "table": {"rows": [["user_17"]]}})}
        self.assertFalse(doc_mentions_record(other, self.link, other["content"]))   # only a table row

    def test_non_identifier_display_reference_matches_as_a_whole_token(self):
        link = {"rid": "export_1842", "disp": "NST-2026-0718-1842", "email": "x@x.org", "user": "user_1"}
        pdf = {"kind": "pdf", "content": "Statement NST-2026-0718-1842"}
        self.assertTrue(doc_mentions_record(pdf, link, pdf["content"]))
        self.assertFalse(doc_mentions_record(pdf, link, "Statement NST-2026-0718-18420"))


class TemporalRestrictions(unittest.TestCase):
    subject = Subject(user_id="user_1", emails={"a@x.org"}, current_email="a@x.org")
    req = {"requested_at": REQ, "audit_at": AUDIT, "reported_at": REQ}

    def doc_fact(self, file_id, obs, **attrs):
        f = fact(file_id, "support_5", obs, email="a@x.org", loc="doc")
        f.attrs.update(event="doc_observation", catalog=True, **attrs)
        return f

    def test_document_captured_after_audit_never_discovers_or_sets_state(self):
        f = [self.doc_fact("late", "2026-09-17T10:00:00Z")]      # doc facts carry their capture time as observation time
        p = pipe(view(f), f)
        self.assertNotIn("support_5", p.linked_resources(self.subject, self.req))
        st = p.resource_state("support_5", {"audit_at": AUDIT, "requested_at": REQ}, set())
        self.assertEqual((st.presence, st.evidence_id), ("UNKNOWN", ""))

    def test_catalog_link_created_after_audit_cannot_tie_a_record_to_a_person(self):
        f = [self.doc_fact("early", "2026-09-14T10:00:00Z", link_t="2026-09-16T00:00:00Z")]
        self.assertNotIn("support_5", pipe(view(f), f).linked_resources(self.subject, self.req))
        g = [self.doc_fact("early", "2026-09-14T10:00:00Z", link_t="2026-09-14T10:07:00Z")]
        self.assertIn("support_5", pipe(view(g), g).linked_resources(self.subject, self.req))


class SupportedFormats(unittest.TestCase):
    """The real PDFs keep their supported associations; the register of other customers links nothing."""

    def _text(self, name):
        try:
            import pymupdf
            path = Path(__file__).resolve().parents[2] / "dataset" / "evidence" / "documents" / name
            with pymupdf.open(path) as d:
                return "\n".join(pg.get_text() for pg in d)
        except Exception as e:  # dataset or pymupdf unavailable
            self.skipTest(f"unavailable: {type(e).__name__}")

    def test_real_pdfs(self):
        self.assertEqual(associate(self._text("delivery_correction_attachment.pdf")),
                         ({"support_001": ("priya.raman@gmail.com", None)}, []))
        self.assertEqual(associate(self._text("security_retention_extract.pdf")), ({"audit_record_003": (None, "user_03")}, []))
        self.assertEqual(associate(self._text("legal_retention_register_20260529.pdf")), ({}, []))
        self.assertEqual(associate(self._text("account_history_report_20260718.pdf")), ({}, []))

    def test_register_does_not_become_evidence_for_the_tombstone(self):
        from ghost.pipeline import Pipeline
        try:
            p = Pipeline()
        except Exception as e:
            self.skipTest(f"unavailable: {type(e).__name__}")
        files = {f.file_id for f in p.facts if f.resource_id == "tombstone_17"}
        self.assertEqual(files, {"evidence_42"})            # not evidence_27 (the register of disputed orders)


if __name__ == "__main__":
    unittest.main()

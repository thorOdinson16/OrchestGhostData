"""A classification failure is an error, never a silent NOT_APPLICABLE.   python -m unittest discover -s code/tests -v"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghost.classify import ClassificationError, classify_requests, validate_results  # noqa: E402
from ghost.pipeline import Pipeline  # noqa: E402
from test_completion import REQUEST, _Act, _Ids  # noqa: E402
from test_temporal import pipe, view  # noqa: E402

ROWS = [{"case_id": f"case_{i}", "request_text": "delete me"} for i in (1, 2, 3)]


def item(cid, intent="DELETE"):
    return {"case_id": cid, "intent": intent, "language": "English", "reason": "x"}


def fake_chat(batch_answer, retry_answer=None):
    """chat_json stand-in: the first call per case set gets `batch_answer`; single-case retries get retry_answer(cid, n)."""
    calls = []

    def chat(kind, messages, cache_material):
        calls.append(cache_material)
        if "#retry" in cache_material:
            n = int(cache_material.rsplit("#retry", 1)[1])
            cid = next(r["case_id"] for r in ROWS if f'"{r["case_id"]}"' in cache_material)
            return retry_answer(cid, n) if retry_answer else {"results": []}
        return batch_answer
    chat.calls = calls
    return chat


class Validation(unittest.TestCase):
    def test_complete_valid_answer(self):
        good, bad = validate_results({"results": [item("a"), item("b", "NOT_DELETE")]}, ["a", "b"])
        self.assertEqual((sorted(good), bad), (["a", "b"], {}))

    def test_missing_invalid_duplicate_and_foreign_results_are_rejected(self):
        resp = {"results": [item("a"), item("b", "MAYBE"), item("c"), item("c"), item("zzz"), item("d", None), "junk"]}
        good, bad = validate_results(resp, ["a", "b", "c", "d", "e"])
        self.assertEqual(sorted(good), ["a"])
        self.assertIn("unrecognised intent 'MAYBE'", bad["b"])
        self.assertIn("2 results", bad["c"])                  # duplicates are not trusted even when they agree
        self.assertIn("unrecognised intent None", bad["d"])
        self.assertEqual(bad["e"], "no result returned")
        self.assertNotIn("zzz", good)

    def test_malformed_responses(self):
        for resp in ({}, {"results": "DELETE"}, [], None, {"results": None}):
            good, bad = validate_results(resp, ["a"])
            self.assertEqual((good, list(bad)), ({}, ["a"]), resp)

    def test_intent_must_be_exactly_a_known_value(self):
        for intent in ("delete", "Delete", " DELETE", "DELETE ", "", 1, True, ["DELETE"]):
            good, bad = validate_results({"results": [item("a", intent)]}, ["a"])
            self.assertEqual((good, list(bad)), ({}, ["a"]), repr(intent))


class ClassifyRequests(unittest.TestCase):
    def run_with(self, chat):
        with mock.patch("ghost.classify.chat_json", chat):
            return classify_requests(ROWS)

    def test_valid_batch_answer_needs_no_retry(self):
        chat = fake_chat({"results": [item("case_1"), item("case_2", "NOT_DELETE"), item("case_3")]})
        out = self.run_with(chat)
        self.assertEqual({c: v["intent"] for c, v in out.items()}, {"case_1": "DELETE", "case_2": "NOT_DELETE", "case_3": "DELETE"})
        self.assertEqual(len(chat.calls), 1)

    def test_partial_response_is_completed_by_single_case_retries(self):
        chat = fake_chat({"results": [item("case_1")]}, lambda cid, n: {"results": [item(cid, "NOT_DELETE")]})
        out = self.run_with(chat)
        self.assertEqual(sorted(out), ["case_1", "case_2", "case_3"])
        self.assertEqual((out["case_2"]["intent"], out["case_1"]["intent"]), ("NOT_DELETE", "DELETE"))
        self.assertTrue(all("#retry1" in c for c in chat.calls[1:]))   # retries do not replay the cached batch answer

    def test_invalid_then_valid_on_the_second_retry(self):
        chat = fake_chat({"results": [item("case_1"), item("case_2"), item("case_3", "???")]},
                         lambda cid, n: {"results": [item(cid)]} if n == 2 else {"results": [item(cid, "")]})
        self.assertEqual(self.run_with(chat)["case_3"]["intent"], "DELETE")

    def test_unrecoverable_cases_raise_instead_of_becoming_not_applicable(self):
        chat = fake_chat({"results": [item("case_1"), item("case_2", "??"), item("case_3"), item("case_3")]})
        with self.assertRaises(ClassificationError) as cm:
            self.run_with(chat)
        self.assertEqual(sorted(cm.exception.reasons), ["case_2", "case_3"])

    def test_omitted_case_ids_raise(self):
        with self.assertRaises(ClassificationError) as cm:
            self.run_with(fake_chat({"results": []}))
        self.assertEqual(sorted(cm.exception.reasons), ["case_1", "case_2", "case_3"])

    def test_duplicate_input_ids_raise(self):
        with mock.patch("ghost.classify.chat_json", fake_chat({"results": []})), self.assertRaises(ClassificationError):
            classify_requests(ROWS + [ROWS[0]])

    def test_a_failing_call_is_reported_not_swallowed(self):
        def chat(kind, messages, cache_material):
            if "#retry" in cache_material:
                raise RuntimeError("LLM call failed for classify: Timeout")
            return {"results": [item("case_1"), item("case_2")]}
        with mock.patch("ghost.classify.chat_json", chat), self.assertRaises(ClassificationError) as cm:
            classify_requests(ROWS)
        self.assertEqual(list(cm.exception.reasons), ["case_3"])


class PipelineDoesNotDefaultToNotApplicable(unittest.TestCase):
    def go(self, intents):
        p = pipe(view([]), [])
        p.ids, p.act, p.unresolved_identities = _Ids(None), _Act(), {}
        return p.run([REQUEST], intents)

    def test_missing_or_unrecognised_intent_raises(self):
        for intents in ({}, {"case_1": {}}, {"case_1": {"intent": "UNKNOWN"}}, {"case_1": {"intent": None}}, {"case_1": None}):
            with self.assertRaises(ClassificationError, msg=intents):
                self.go(intents)

    def test_explicit_not_delete_is_still_not_applicable(self):
        self.assertEqual(self.go({"case_1": {"intent": "NOT_DELETE"}})["case_1"], ("NOT_APPLICABLE", [], []))


if __name__ == "__main__":
    unittest.main()

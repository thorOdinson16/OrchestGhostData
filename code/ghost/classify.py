"""Request classification: does the (possibly non-English) request text authorise deletion?"""
import json
from concurrent.futures import ThreadPoolExecutor

from .llm import chat_json

SYSTEM = """You triage customer privacy requests for an online shop. Requests may be written in any language.
Decide whether the text AUTHORISES DELETION of the person's account / personal data.

Return intent "DELETE" when the person asks (explicitly or clearly) for their account, profile, personal data or
copies of it to be deleted, erased, removed, closed-and-erased, or asks to finish / verify / audit a deletion they
already asked for or believe was done (e.g. "I deleted my account, please make sure nothing remains"). Asking to
"close" the account together with removing/stopping use of the person's data, or to close it "completely", is a
deletion request. Mentioning that an integration must stop sending data while asking to close the account is DELETE.
Return intent "NOT_DELETE" when the person asks for something else: access to or a copy of their data, an export,
correction/update of details, marketing or newsletter opt-out/unsubscribe, restriction of processing, a complaint,
a question about policy, or any request that explicitly says NOT to delete the account/data. A request that only
stops marketing or only asks for a data copy is NOT_DELETE even if it mentions the account.
Judge only from the text. Output a JSON object: {"results": [{"case_id": "...", "intent": "DELETE"|"NOT_DELETE",
"language": "<language name>", "reason": "<=15 words"}]} with one entry per input case, in the same order."""


INTENTS = ("DELETE", "NOT_DELETE")
RETRIES = 2   # extra single-case attempts for a case whose batch answer was missing, malformed or contradictory


class ClassificationError(RuntimeError):
    """A request could not be given exactly one recognised intent. Never mapped to NOT_APPLICABLE: a failure to classify
    is not a decision that the person did not ask for deletion."""

    def __init__(self, reasons):
        self.reasons = dict(reasons)   # case_id -> why
        super().__init__("classification failed for %d case(s): %s" % (
            len(self.reasons), "; ".join(f"{c}: {w}" for c, w in sorted(self.reasons.items())[:5])))


def validate_results(response, expected_ids):
    """-> ({case_id: item} with exactly one recognised intent, {case_id: reason} for the others).

    A case is rejected if it is missing, has an unrecognised or non-string intent, or is reported more than once (even
    consistently: the batch answer is then not trustworthy for it). Ids that were not asked about are ignored."""
    expected = list(expected_ids)
    items = response.get("results") if isinstance(response, dict) else None
    if not isinstance(items, list):
        return {}, {c: "response has no results list" for c in expected}
    seen = {}
    for it in items:
        cid = it.get("case_id") if isinstance(it, dict) else None
        if isinstance(cid, str) and cid in expected:
            seen.setdefault(cid, []).append(it)
    good, bad = {}, {}
    for cid in expected:
        got = seen.get(cid, [])
        if not got:
            bad[cid] = "no result returned"
        elif len(got) > 1:
            bad[cid] = "%d results returned" % len(got)
        elif not isinstance(got[0].get("intent"), str) or got[0]["intent"] not in INTENTS:
            bad[cid] = "unrecognised intent %r" % (got[0].get("intent"),)
        else:
            good[cid] = got[0]
    return good, bad


def _ask(chunk, attempt=0):
    payload = [{"case_id": r["case_id"], "request_text": r["request_text"]} for r in chunk]
    user = json.dumps(payload, ensure_ascii=False)
    # a different cache key per attempt: a cached malformed answer must not be replayed for the retry
    return chat_json("classify", [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                     SYSTEM + user + (f"#retry{attempt}" if attempt else ""))


def classify_requests(rows, batch=20):
    """rows: list of dicts with case_id, request_text. Returns {case_id: {"intent": "DELETE"|"NOT_DELETE", ...}} with one
    entry per input case, or raises ClassificationError naming every case that stayed unclassified after the retries."""
    ids = [r["case_id"] for r in rows]
    if len(set(ids)) != len(ids):
        raise ClassificationError({c: "duplicate case_id in the input" for c in sorted({c for c in ids if ids.count(c) > 1})})
    out, bad = {}, {}
    chunks = [rows[i : i + batch] for i in range(0, len(rows), batch)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for chunk, res in zip(chunks, pool.map(_ask, chunks)):
            good, why = validate_results(res, [r["case_id"] for r in chunk])
            out.update(good)
            bad.update(why)
    by_id = {r["case_id"]: r for r in rows}
    for cid in sorted(bad):                     # retry stragglers one by one
        for attempt in range(1, RETRIES + 1):
            try:
                good, why = validate_results(_ask([by_id[cid]], attempt), [cid])
            except RuntimeError as e:           # the call itself failed after its own retries
                why = {cid: str(e)}
                good = {}
            if cid in good:
                out[cid] = good[cid]
                bad.pop(cid)
                break
            bad[cid] = why[cid]
    if bad:
        raise ClassificationError(bad)
    return out
